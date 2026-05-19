import asyncio
import logging
from db import get_enriched_leads, log_progress
from ai_client import call_ollama_json
from prompts import SCORING_PROMPT, OUTREACH_PROMPT

logger = logging.getLogger(__name__)

async def run(run_id: str) -> None:
    # Rule-based scoring + Ollama AI batch scoring (groups of 5)
    pass

def compute_base_score(lead: dict) -> tuple[int, str]:
    score = 0
    reasons = []

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
    reasons.append(f"{complete}/5 fields complete (+{field_pts})")

    if lead.get("email_verified") == 1:
        score += 15; reasons.append("email verified (+15)")
    if lead.get("phone_valid") == 1:
        score += 15; reasons.append("phone valid (+15)")

    age = lead.get("business_age_months")
    if age is not None:
        if age <= 12: score += 20; reasons.append("under 1 year old (+20)")
        elif age <= 24: score += 15; reasons.append("under 2 years old (+15)")
        elif age <= 48: score += 8; reasons.append("under 4 years old (+8)")
    
    HIGH_DEMAND = ["restaurant", "trucking", "construction", "medical", "salon", "auto_repair", "dental"]
    MED_DEMAND = ["retail", "landscaping", "fitness", "bakery", "plumbing", "electrical"]
    industry = (lead.get("industry") or "").lower()
    if any(v in industry for v in HIGH_DEMAND):
        score += 20; reasons.append(f"high-demand vertical ({industry}) (+20)")
    elif any(v in industry for v in MED_DEMAND):
        score += 10; reasons.append(f"medium-demand vertical ({industry}) (+10)")

    return min(score, 100), "; ".join(reasons)

async def ollama_score_lead(lead: dict) -> tuple[int, str]:
    prompt = SCORING_PROMPT.format(
        company_name=lead.get("company_name", "Unknown"),
        industry=lead.get("industry", "Unknown"),
        business_age_months=lead.get("business_age_months", "Unknown"),
        email_verified=lead.get("email_verified", 0),
        phone_valid=lead.get("phone_valid", 0),
        source=lead.get("source", "Unknown"),
    )
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, call_ollama_json, prompt, 200)
    if result and "score" in result:
        return int(result["score"]), result.get("top_reason", "")
    return 0, "AI scoring failed"
