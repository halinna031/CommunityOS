"""Small Gemini-only adapter shared by intent parsing, ingestion, and explanation."""
from __future__ import annotations

from . import config


def generate_text(prompt: str, *, system_instruction: str = "", max_output_tokens: int = 512,
                  json_mode: bool = False) -> str:
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
    response = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(**options),
    )
    return (response.text or "").strip()
