"""
dedup.py — Phase 5: Remove duplicates and filter to final qualifying leads.
"""

import asyncio
import logging
import os
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

MIN_QUALITY_SCORE = int(os.getenv("MIN_QUALITY_SCORE", "60"))
TARGET_LEAD_COUNT = int(os.getenv("TARGET_LEAD_COUNT", "200"))
REQUIRED_FIELDS = ["first_name", "last_name", "email", "company_name", "phone"]


def deduplicate_leads(leads: list[dict]) -> list[dict]:
    """
    Remove duplicates using recordlinkage fuzzy matching.
    Falls back to exact dedup if recordlinkage fails.
    """
    if not leads:
        return []

    try:
        import pandas as pd
        import recordlinkage

        df = pd.DataFrame(leads)
        df["company_clean"] = (
            df["company_name"].fillna("").str.lower().str.strip()
        )
        df["phone_clean"] = df["phone"].fillna("").str.replace(r"\D", "", regex=True)

        indexer = recordlinkage.Index()
        indexer.block("company_clean")
        candidate_links = indexer.index(df)

        if len(candidate_links) == 0:
            return leads

        compare = recordlinkage.Compare()
        compare.string(
            "company_clean", "company_clean",
            method="jarowinkler", threshold=0.85, label="name_sim"
        )
        compare.exact("phone_clean", "phone_clean", label="phone_match")

        features = compare.compute(candidate_links, df)
        matches = features[features.sum(axis=1) >= 1.5]

        ids_to_drop = set()
        for idx_a, idx_b in matches.index:
            score_a = df.loc[idx_a, "quality_score"] if "quality_score" in df.columns else 0
            score_b = df.loc[idx_b, "quality_score"] if "quality_score" in df.columns else 0
            ids_to_drop.add(idx_a if score_a <= score_b else idx_b)

        result = df.drop(index=list(ids_to_drop)).to_dict("records")
        logger.info(f"Dedup: {len(leads)} → {len(result)} (removed {len(ids_to_drop)} duplicates)")
        return result

    except Exception as e:
        logger.warning(f"recordlinkage dedup failed: {e} — using simple dedup")
        # Simple fallback dedup on company name + phone
        seen = set()
        unique = []
        for lead in leads:
            key = (
                (lead.get("company_name") or "").lower().strip(),
                (lead.get("phone") or "").replace(" ", "").replace("-", ""),
            )
            if key not in seen:
                seen.add(key)
                unique.append(lead)
        return unique


async def run(run_id: str, config: dict) -> int:
    """
    Phase 5 entry point.
    Returns count of final qualifying leads.
    """
    from db import get_connection, update_run_status, log_progress

    min_score = config.get("min_score", MIN_QUALITY_SCORE)
    target = config.get("target_count", TARGET_LEAD_COUNT)

    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM enriched_leads WHERE run_id = ?", (run_id,)
    ).fetchall()
    leads = [dict(r) for r in rows]
    conn.close()

    log_progress(run_id, "dedup", f"Deduplicating {len(leads)} leads...", len(leads))

    # Step 1: Deduplicate
    deduped = deduplicate_leads(leads)

    # Step 2: Filter — require all 5 fields AND minimum score
    final_leads = [
        lead for lead in deduped
        if lead.get("quality_score", 0) >= min_score
        and all(
            lead.get(f) and str(lead.get(f, "")).strip()
            for f in REQUIRED_FIELDS
        )
    ]

    # Step 3: Sort by score descending
    final_leads.sort(key=lambda x: x.get("quality_score", 0), reverse=True)

    # Step 4: Take top N
    final_leads = final_leads[:target]

    logger.info(
        f"Dedup: {len(leads)} enriched → {len(deduped)} deduped → "
        f"{len(final_leads)} final (min_score={min_score}, target={target})"
    )

    # If we have very few leads, lower the threshold and retry
    if len(final_leads) < 10 and min_score > 30:
        relaxed_score = max(30, min_score - 20)
        logger.warning(
            f"Only {len(final_leads)} leads above score {min_score}. "
            f"Relaxing to {relaxed_score}."
        )
        final_leads = [
            lead for lead in deduped
            if lead.get("quality_score", 0) >= relaxed_score
            and lead.get("company_name")
        ]
        final_leads.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        final_leads = final_leads[:target]
        log_progress(
            run_id, "dedup",
            f"Score threshold relaxed to {relaxed_score} — {len(final_leads)} leads qualifying",
            len(final_leads)
        )

    update_run_status(run_id, "running", total_final=len(final_leads))
    log_progress(run_id, "dedup",
                 f"Dedup complete — {len(final_leads)} final qualifying leads",
                 len(final_leads))
    return len(final_leads)