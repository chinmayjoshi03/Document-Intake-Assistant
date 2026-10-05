"""
Gemini LLM client using the official google-genai SDK.

SDK imports are isolated to this file — nothing else in the codebase
imports from google.genai directly.

Design decisions:
- Uses generate_content with GenerateContentConfig(response_mime_type,
  response_schema) for structured JSON output.
- Strips accidental markdown fences from response.text before parsing,
  because some model versions occasionally wrap JSON in ```json ... ```.
- On parse/validation failure: one repair attempt with a focused prompt.
  If the second attempt also fails, raises LLMMalformedResponse.
- Never logs the API key or full user content.
- Error mapping: ClientError(429)→LLMRateLimited, ClientError(401/403)→
  LLMConfigError, ServerError/timeout/network→LLMUnavailable.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from app.config import Settings
from app.domain.state import FieldPath
from app.llm.base import (
    ExtractionRequest,
    ExtractionResponse,
    LLMConfigError,
    LLMMalformedResponse,
    LLMRateLimited,
    LLMUnavailable,
)
from app.llm.prompts import build_system_prompt, build_user_prompt, build_repair_prompt

logger = logging.getLogger(__name__)

# Fence pattern: ```json ... ``` or ``` ... ```
_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present; return raw text otherwise."""
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text.strip()


def _parse_response(raw: str) -> ExtractionResponse:
    """Parse and validate raw JSON text into ExtractionResponse."""
    cleaned = _strip_fences(raw)
    return ExtractionResponse.model_validate_json(cleaned)


class GeminiClient:
    """
    Production LLM client backed by the Gemini API.

    Raises LLMConfigError if the API key is missing at construction time
    so failures surface at startup rather than on the first request.
    """

    def __init__(self, settings: Settings) -> None:
        # SDK imports only inside this file
        from google import genai  # noqa: F401 (kept local to isolate SDK dep)

        if not settings.gemini_api_key:
            raise LLMConfigError(
                "GEMINI_API_KEY is not set. "
                "Set it in .env or use LLM_PROVIDER=mock."
            )

        self._model = settings.gemini_model
        self._timeout = settings.llm_timeout_seconds
        self._system_prompt = build_system_prompt()

        # Instantiate the SDK client — key is read from settings, never logged
        self._client = genai.Client(api_key=settings.gemini_api_key)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        """
        Call Gemini to extract proposed updates from the user message.

        Strips fences, validates with Pydantic.  On first failure, retries
        once with a repair prompt.  Raises typed LLMError subclasses on all
        failure paths so callers never have to inspect raw SDK exceptions.
        """
        from google.genai import types
        from google.genai.errors import APIError, ClientError, ServerError

        user_prompt = build_user_prompt(
            state_json=request.state_json,
            recent_messages=request.recent_messages,
            user_message=request.user_message,
            focus_field=request.focus_field,
            pending_conflict=request.pending_conflict,
        )

        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt,
            response_mime_type="application/json",
            response_schema=ExtractionResponse,
            temperature=0,
        )

        # --- First attempt ---
        raw = self._call_sdk(user_prompt, config)
        first_error: Optional[Exception] = None
        try:
            return _parse_response(raw)
        except Exception as exc:
            first_error = exc  # captured before Python clears the name post-except
            logger.warning(
                "Gemini response parse failed (attempt 1): %s — attempting repair",
                type(exc).__name__,
            )

        # --- Repair attempt ---
        repair_prompt = build_repair_prompt(
            bad_json=_strip_fences(raw),
            validation_error=str(first_error),
        )
        raw2 = self._call_sdk(repair_prompt, config)
        try:
            return _parse_response(raw2)
        except Exception as repair_err:
            logger.error(
                "Gemini repair attempt also failed: %s",
                type(repair_err).__name__,
            )
            raise LLMMalformedResponse(
                f"Gemini returned unparseable output after repair: {repair_err}"
            ) from repair_err

    # ------------------------------------------------------------------
    # Internal SDK call + error mapping
    # ------------------------------------------------------------------

    def _call_sdk(self, prompt: str, config: Any) -> str:
        """
        Make one generate_content call and return response.text.

        Maps SDK exceptions to our typed error hierarchy so the rest of
        the codebase never needs to know about google.genai internals.
        Never logs the API key or full prompt content.
        """
        from google.genai.errors import ClientError, ServerError
        import httpx

        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
            return response.text or ""

        except ClientError as exc:
            code = getattr(exc, "code", 0)
            if code == 429:
                raise LLMRateLimited(f"Gemini rate limit (429)") from exc
            if code in (401, 403):
                raise LLMConfigError(
                    f"Gemini authentication error ({code}). Check GEMINI_API_KEY."
                ) from exc
            # Other 4xx — treat as unavailable
            raise LLMUnavailable(f"Gemini client error ({code})") from exc

        except ServerError as exc:
            code = getattr(exc, "code", 500)
            raise LLMUnavailable(f"Gemini server error ({code})") from exc

        except httpx.TimeoutException as exc:
            raise LLMUnavailable("Gemini request timed out") from exc

        except httpx.NetworkError as exc:
            raise LLMUnavailable(f"Gemini network error: {type(exc).__name__}") from exc

        except Exception as exc:
            # Catch-all: log type only (not content which may include user data)
            logger.error("Unexpected error calling Gemini: %s", type(exc).__name__)
            raise LLMUnavailable(f"Unexpected error: {type(exc).__name__}") from exc
