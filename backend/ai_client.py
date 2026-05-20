"""
ai_client.py — All Ollama LLM calls go through here.
Calls http://localhost:11434 — 100% free, runs locally.
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
    try:
        r = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=15))
def call_ollama(prompt: str, max_tokens: int = 500, temperature: float = 0.1) -> str:
    """
    Call local Ollama API. Returns raw text response.
    Raises on HTTP error — tenacity will retry up to 3 times.
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
    try:
        response = httpx.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except httpx.ConnectError:
        logger.error(
            "Ollama not reachable at localhost:11434. "
            "Make sure 'ollama serve' is running."
        )
        return ""
    except httpx.TimeoutException:
        logger.warning("Ollama timed out — model may be loading. Will retry.")
        raise


def call_ollama_json(prompt: str, max_tokens: int = 300) -> dict | None:
    """
    Call Ollama and parse JSON response.
    Strips markdown code fences. Extracts first JSON object from response.
    Returns None on parse failure.
    """
    raw = call_ollama(prompt, max_tokens=max_tokens, temperature=0.1)
    if not raw:
        return None
    try:
        # Strip markdown fences
        cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip())
        cleaned = re.sub(r'\s*```$', '', cleaned)
        # Extract first complete JSON object (handles leading/trailing text)
        match = re.search(r'\{[^{}]*\}', cleaned, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(cleaned)
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Ollama JSON parse failed: {e} | raw[:200]: {raw[:200]}")
        return None