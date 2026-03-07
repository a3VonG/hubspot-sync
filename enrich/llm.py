"""Thin wrapper around the Anthropic SDK for structured JSON extraction.

Sends a system prompt + user content to Claude and parses the JSON response.
Includes retry logic for transient failures and JSON parse errors.
"""

import json
import logging
import time
from typing import Any

import anthropic

log = logging.getLogger(__name__)

# Retry config
_MAX_RETRIES = 2
_RETRY_DELAY = 2  # seconds


def extract_json(
    *,
    system_prompt: str,
    user_content: str,
    api_key: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 1024,
) -> dict[str, Any]:
    """Send *system_prompt* + *user_content* to Claude, parse JSON reply.

    Raises ``LLMError`` if all retries are exhausted.
    """
    client = anthropic.Anthropic(api_key=api_key)

    last_error: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )

            text = _response_text(response)
            return _parse_json(text)

        except (anthropic.APIError, anthropic.APIConnectionError) as exc:
            last_error = exc
            log.warning(
                "Anthropic API error (attempt %d/%d): %s",
                attempt + 1,
                _MAX_RETRIES + 1,
                exc,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY * (attempt + 1))

        except _JSONParseError as exc:
            last_error = exc
            log.warning(
                "JSON parse failed (attempt %d/%d): %s",
                attempt + 1,
                _MAX_RETRIES + 1,
                exc,
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)

    raise LLMError(f"LLM extraction failed after {_MAX_RETRIES + 1} attempts") from last_error


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _response_text(response: anthropic.types.Message) -> str:
    """Pull the text out of an Anthropic message response."""
    for block in response.content:
        if block.type == "text":
            return block.text
    raise LLMError("Anthropic response contained no text block")


def _parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output, tolerating markdown fences."""
    text = text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[: -3]

    text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _JSONParseError(f"Invalid JSON from LLM: {exc}\nRaw: {text[:500]}") from exc

    if not isinstance(result, dict):
        raise _JSONParseError(f"Expected JSON object, got {type(result).__name__}")

    return result


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Raised when the LLM call fails after retries."""


class _JSONParseError(Exception):
    """Internal: JSON parsing failed, triggers retry."""
