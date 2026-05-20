"""
scoring.py — Phase 4: Score each lead 0-100 for financing likelihood.

Two-pass approach:
Pass 1 — Rule-based base score (fast, no LLM calls)
Pass 2 — Ollama AI score (only for base_score >= 40)
Outreach notes generated for all leads scoring >= 60
"""

import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

HIGH_DEMAND = ["restaurant", "trucking", "construction", "medical", "salon",
               "auto_repair", "dental", "fast_food", "bakery"]
MED_DEMAND = ["retail", "landscaping", "fitness", "plumbing", "electrical",
              "legal", "real_estate", "insurance", "grocery"]


def compute_base_score(lead: dict) -> tuple[int, str]:
    """Returns (score 0-100, reason string)."""
    score = 0
    reasons = []

    # Field completeness (max 30 pts)
    fields = {
        "first_name": lead.get("first_name"),
        "last_name": lead.get("last_name"),
        "email": lead.get("email"),
        "company_name": lead.get("company_name"),
        "phone": lead.get("phone"),
    }
    complete = sum(1 for v in fields.values() if v and str(v).strip())
    field_pts = complete * 6
    score += field_pts
    reasons.append(f"{complete}/5 fields ({field_pts}pts)")

    # Email verified (15 pts)
    if lead.get("email_verified") == 1:
        score += 15
        reasons.append("email_verified(+15)")

    # Phone valid (15 pts)
    if lead.get("phone_valid") == 1:
        score += 15
        reasons.append("phone_valid(+15)")

    # Business age (max 20 pts)
    age = lead.get("business_age_months")
    if age is not None:
        try:
            age = int(age)
            if age <= 12:
                score += 20
                reasons.append("under_1yr(+20)")
            elif age <= 24:
                score += 15
                reasons.append("under_2yr(+15)")
            elif age <= 48:
                score += 8
                reasons.append("under_4yr(+8)")
        except (ValueError, TypeError):
            pass

    # Industry demand (max 20 pts)
    industry = (lead.get("industry") or "").lower()
    if any(v in industry for v in HIGH_DEMAND):
        score += 20
        reasons.append(f"high_demand({industry})(+20)")
    elif any(v in industry for v in MED_DEMAND):
        score += 10
        reasons.append(f"med_demand({industry})(+10)")

    # SBA source bonus — already sought financing
    if lead.get("source") == "sba":
        score += 10
        reasons.append("sba_prior_loan(+10)")
        score = min(score, 100)

    return min(score, 100), "; ".join(reasons)


async def ollama_score_lead(lead: dict, base_score: int) -> tuple[int, str]:
    """
    Call local Llama for holistic lead quality assessment.
    Only called when base_score >= 40.
    Returns (ai_score, ai_reason). Falls back to base_score on failure.
    """
    try:
        from ai_client import call_ollama_json
        from prompts import SCORING_PROMPT

        prompt = SCORING_PROMPT.format(
            company_name=lead.get("company_name") or "Unknown",
            industry=lead.get("industry") or "Unknown",
            business_age_months=lead.get("business_age_months") or "Unknown",
            email_verified=lead.get("email_verified", 0),
            phone_valid=lead.get("phone_valid", 0),
            source=lead.get("source") or "Unknown",
        )
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, call_ollama_json, prompt, 200)

        if result and isinstance(result, dict) and "score" in result:
            ai_score = max(0, min(100, int(result["score"])))
            ai_reason = result.get("top_reason", "")
            return ai_score, ai_reason

    except Exception as e:
        logger.debug(f"Ollama scoring failed for {lead.get('company_name')}: {e}")

    return base_score, "rule_based_only"


async def generate_outreach_note(lead: dict) -> str:
    """Generate a one-line outreach note for the sales team."""
    try:
        from ai_client import call_ollama
        from prompts import OUTREACH_PROMPT

        prompt = OUTREACH_PROMPT.format(
            company_name=lead.get("company_name") or "",
            industry=lead.get("industry") or "",
            business_age_months=lead.get("business_age_months") or "",
            quality_score=lead.get("quality_score", 0),
            score_reason=lead.get("score_reason") or "",
        )
        loop = asyncio.get_event_loop()
        note = await loop.run_in_executor(None, call_ollama, prompt, 80, 0.4)
        return (note or "").strip()[:200]
    except Exception as e:
        logger.debug(f"Outreach note generation failed: {e}")
        company = lead.get("company_name", "")
        industry = lead.get("industry", "business")
        return f"{company} is a {industry} — consider working capital or equipment financing."


async def run(run_id: str) -> None:
    """Phase 4 entry point. Scores all enriched leads for this run."""
    from db import get_connection, log_progress

    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM enriched_leads WHERE run_id = ?", (run_id,)
    ).fetchall()
    leads = [dict(r) for r in rows]
    conn.close()

    total = len(leads)
    log_progress(run_id, "scoring", f"Scoring {total} leads...", 0)

    # Process in batches of 5 for Ollama
    batch_size = 5
    updates = []

    for i, lead in enumerate(leads):
        try:
            base_score, base_reason = compute_base_score(lead)

            if base_score >= 40:
                ai_score, ai_reason = await ollama_score_lead(lead, base_score)
                final_score = (base_score + ai_score) // 2
                final_reason = f"{base_reason} | AI: {ai_reason}"
            else:
                final_score = base_score
                final_reason = base_reason

            updates.append({
                "id": lead["id"],
                "quality_score": final_score,
                "score_reason": final_reason[:500],
            })

        except Exception as e:
            logger.warning(f"Scoring error for lead {lead.get('id')}: {e}")
            updates.append({
                "id": lead["id"],
                "quality_score": 30,
                "score_reason": "scoring_error",
            })

        # Batch sleep every 5 calls
        if (i + 1) % batch_size == 0:
            await asyncio.sleep(0.5)
            log_progress(run_id, "scoring",
                         f"Scored {i + 1}/{total} leads", i + 1)

    # Bulk update scores
    conn = get_connection()
    for upd in updates:
        conn.execute(
            "UPDATE enriched_leads SET quality_score = ?, score_reason = ? WHERE id = ?",
            (upd["quality_score"], upd["score_reason"], upd["id"])
        )
    conn.commit()

    # Generate outreach notes for qualifying leads (score >= 60)
    qualifying = [
        dict(conn.execute("SELECT * FROM enriched_leads WHERE id = ?", (u["id"],)).fetchone())
        for u in updates if u["quality_score"] >= 60
    ]
    conn.close()

    log_progress(run_id, "scoring",
                 f"Generating outreach notes for {len(qualifying)} qualifying leads...",
                 len(qualifying))

    conn = get_connection()
    for j, lead in enumerate(qualifying):
        try:
            note = await generate_outreach_note(lead)
            conn.execute(
                "UPDATE enriched_leads SET outreach_note = ? WHERE id = ?",
                (note, lead["id"])
            )
            if j % 5 == 0:
                conn.commit()
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.debug(f"Note gen error: {e}")

    conn.commit()
    conn.close()

    log_progress(run_id, "scoring", f"Scoring complete — {total} leads scored", total)