"""
ai_client.py — All Ollama/LLM interactions centralized here.
Uses Ollama local API at http://localhost:11434
Model: llama3.1 (8B parameter, runs on CPU or GPU, completely free)
"""

import httpx
import json
import re
import logging
import os
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

load_dotenv()

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1")
logger = logging.getLogger(__name__)


def check_ollama_available() -> bool:
    """Returns True if Ollama server is reachable."""
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def call_ollama(prompt: str, max_tokens: int = 500, temperature: float = 0.1) -> str:
    """
    Call the local Ollama API.
    Returns the raw text response from the model.
    Raises on HTTP error (tenacity will retry).
    temperature=0.1 for extraction tasks (deterministic), 0.4 for creative tasks (outreach notes)
    """
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": max_tokens,
            "top_p": 0.9,
        }
    }
    response = httpx.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json=payload,
        timeout=120  # Local LLM can be slow on CPU — allow 2 minutes
    )
    response.raise_for_status()
    data = response.json()
    return data.get("response", "").strip()


def call_ollama_json(prompt: str, max_tokens: int = 300) -> dict | None:
    """
    Call Ollama and parse the response as JSON.
    Strips markdown code fences if present.
    Returns None on parse failure (caller handles fallback).
    """
    raw = call_ollama(prompt, max_tokens=max_tokens, temperature=0.1)
    try:
        # Strip markdown fences Llama sometimes adds
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        # Extract first JSON object if there's surrounding text
        match = re.search(r'\{.*\}', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Ollama JSON parse failed: {e} | raw response: {raw[:300]}")
        return None
