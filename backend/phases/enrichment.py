import logging
import os
import phonenumbers
from pyisemail import is_email
from db import insert_enriched_lead, log_progress
from ai_client import call_ollama_json
from prompts import RESEARCH_PROMPT

logger = logging.getLogger(__name__)

HUNTER_CALL_COUNT = 0
MAX_HUNTER_CALLS = 20

async def run(run_id: str) -> None:
    # Logic to fetch raw leads, enrich, and insert into enriched_leads
    # Needs to implement the 3-method enrichment strategy and phone normalization
    pass

def normalize_phone(raw_phone: str) -> str | None:
    try:
        parsed = phonenumbers.parse(raw_phone, "US")
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
        return None
    except Exception:
        return None

def validate_email(email: str) -> bool:
    if not email:
        return False
    GENERIC_PREFIXES = ['info@', 'contact@', 'admin@', 'hello@', 'support@', 'sales@', 'noreply@', 'no-reply@', 'office@', 'mail@']
    email_lower = email.lower()
    if any(email_lower.startswith(p) for p in GENERIC_PREFIXES):
        return False
    return bool(is_email(email))
