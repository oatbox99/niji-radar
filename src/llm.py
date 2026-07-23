"""Provider-agnostic LLM wrapper. Default: Gemini (gemini-2.5-flash), new google.genai SDK.

Swapping providers = reimplement extract_json() against another SDK; nothing else in
the codebase imports an SDK directly. The API key is read from the environment
(GEMINI_API_KEY) — never hardcode it (monorepo rule, enforced by hook).
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

GEMINI_MODEL = "gemini-2.5-flash"


class LLMError(RuntimeError):
    """Raised when the LLM is unavailable or returns unusable output."""


def extract_json(prompt: str) -> Dict[str, Any]:
    """Send `prompt` to the LLM in JSON mode and return the parsed object.

    JSON mode forces syntactically valid JSON, so the failure modes are: no key,
    SDK missing, a blocked/empty candidate (accessing .text raises), or non-object
    JSON — all surfaced as LLMError, never silently swallowed.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise LLMError("GEMINI_API_KEY not set (see .env.example)")

    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise LLMError("google-genai not installed (pip install -r requirements.txt)") from exc

    client = genai.Client(api_key=api_key)
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,  # deterministic extraction
            ),
        )
    except Exception as exc:  # noqa: BLE001 — network/API errors -> LLMError contract
        raise LLMError(f"Gemini call failed: {exc}") from exc

    # resp.text raises when the candidate was safety-blocked or empty — a realistic
    # outcome for scraped blog text. Convert any accessor failure to LLMError.
    try:
        text = (resp.text or "").strip()
    except Exception as exc:  # noqa: BLE001 — blocked/empty candidate has no .text
        raise LLMError(f"LLM response has no usable text part: {exc}") from exc
    if not text:
        raise LLMError("LLM returned an empty response")
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM did not return valid JSON: {text[:200]!r}") from exc
    if not isinstance(result, dict):
        raise LLMError(f"LLM returned JSON {type(result).__name__}, expected an object")
    return result
