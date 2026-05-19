import asyncio
import spacy
import re
import logging
from db import insert_raw_lead, log_progress
from ai_client import call_ollama_json
from prompts import EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

# Load spaCy model ONCE at module level
try:
    nlp = spacy.load("en_core_web_lg")
except OSError:
    logger.error("spaCy model 'en_core_web_lg' not found. Please run 'python -m spacy download en_core_web_lg'")
    raise

async def run(run_id: str, raw_records: list[dict]) -> None:
    for i, record in enumerate(raw_records):
        extracted = {}

        if record.get("raw_html") and len(record["raw_html"]) > 200:
            extracted = await extract_with_ollama(record["raw_html"])
            extraction_method = "ollama_html"
        else:
            text = " ".join(filter(None, [
                record.get("company_name"),
                record.get("address"),
                record.get("phone"),
            ]))
            extracted = extract_with_spacy(text)
            extraction_method = "spacy_ner"

        merged = {
            "company_name": extracted.get("company_name") or record.get("company_name"),
            "first_name":   extracted.get("first_name")   or record.get("first_name"),
            "last_name":    extracted.get("last_name")    or record.get("last_name"),
            "email":        extracted.get("email")        or record.get("email"),
            "phone":        extracted.get("phone")        or record.get("phone"),
            "address":      record.get("address"),
            "industry":     record.get("industry"),
            "business_age_months": record.get("business_age_months"),
            "source":       record.get("source"),
            "raw_html":     record.get("raw_html", "")[:5000],
            "extraction_method": extraction_method,
        }
        insert_raw_lead(run_id, merged)

        if i % 10 == 0:
            log_progress(run_id, "extraction", f"Extracted {i}/{len(raw_records)} records", i)
        
        await asyncio.sleep(0.1)

async def extract_with_ollama(raw_html: str) -> dict:
    prompt = EXTRACTION_PROMPT.format(html=raw_html[:3000])
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, call_ollama_json, prompt, 300)

    if result is None:
        return {"first_name": None, "last_name": None, "email": None,
                "company_name": None, "phone": None}
    return result

def extract_with_spacy(text: str) -> dict:
    doc = nlp(text)

    persons = [ent.text for ent in doc.ents if ent.label_ == "PERSON"]
    orgs    = [ent.text for ent in doc.ents if ent.label_ == "ORG"]

    first_name, last_name = None, None
    if persons:
        parts = persons[0].split()
        first_name = parts[0] if parts else None
        last_name  = " ".join(parts[1:]) if len(parts) > 1 else None

    company_name = orgs[0] if orgs else None

    phone_match = re.search(r'(\+?1?\s?)?(\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4})', text)
    phone = phone_match.group(0) if phone_match else None

    email_match = re.search(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', text)
    email = email_match.group(0) if email_match else None

    return {
        "first_name": first_name,
        "last_name": last_name,
        "company_name": company_name,
        "phone": phone,
        "email": email,
    }
