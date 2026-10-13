"""Small Gemini-only adapter shared by intent parsing, ingestion, and explanation."""
from __future__ import annotations

import re
import threading
import time

from . import config

_CALL_LOCK = threading.Lock()
_LAST_CALL_AT = 0.0


def _wait_for_interval(seconds: float) -> None:
    """Serialize optional low-rate API calls, used by the batch ingester."""
    global _LAST_CALL_AT
    with _CALL_LOCK:
        remaining = seconds - (time.monotonic() - _LAST_CALL_AT)
        if remaining > 0:
            time.sleep(remaining)
        _LAST_CALL_AT = time.monotonic()


def generate_text(prompt: str, *, system_instruction: str = "", max_output_tokens: int = 512,
                  json_mode: bool = False, min_interval_seconds: float = 0) -> str:
    """Call Gemini API. The API key is read only from GEMINI_API_KEY."""
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.GEMINI_API_KEY)
    options = {"max_output_tokens": max_output_tokens}
    if system_instruction:
        options["system_instruction"] = system_instruction
    if json_mode:
        options["response_mime_type"] = "application/json"
    for attempt in range(4):
        if min_interval_seconds > 0:
            _wait_for_interval(min_interval_seconds)
        try:
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(**options),
            )
            break
        except Exception as exc:
            message = str(exc)
            retryable = any(marker in message.upper() for marker in (
                "503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "500", "502", "504",
            ))
            if not retryable or attempt == 3:
                raise
            delay = min(2 ** (attempt + 1), 12)
            retry_hint = re.search(r"retry in ([0-9.]+)s", message, re.IGNORECASE)
            if retry_hint:
                delay = max(delay, float(retry_hint.group(1)))
            time.sleep(delay)
    return (response.text or "").strip()
