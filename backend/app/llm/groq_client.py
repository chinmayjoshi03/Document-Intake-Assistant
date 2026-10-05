"""
Groq LLM client using the OpenAI-compatible API.

Groq exposes an OpenAI-compatible endpoint, so we use the `openai` SDK
pointed at https://api.groq.com/openai/v1.

SDK imports are isolated to this file — nothing else in the codebase
imports from openai directly.

Design decisions:
- Uses chat.completions.create with response_format={"type": "json_object"}
  for structured JSON output.
- Strips accidental markdown fences from the response before parsing.
- On parse/validation failure: one repair attempt with a focused prompt.
  If the second attempt also fails, raises LLMMalformedResponse.
- Never logs the API key or full user content.
- Error mapping: RateLimitError→LLMRateLimited, AuthenticationError→
  LLMConfigError, APIStatusError(5xx)/timeout/network→LLMUnavailable.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.config import Settings
from app.llm.base import (
    ExtractionRequest,
    ExtractionResponse,
    LLMConfigError,
    LLMMalformedResponse,
    LLMRateLimited,
    LLMUnavailable,
)
from app.llm.prompts import build_repair_prompt, build_system_prompt, build_user_prompt

logger = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

_GROQ_BASE_URL = "https://api.groq.com/openai/v1"


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present; return raw text otherwise."""
    m = _FENCE_RE.match(text.strip())
    return m.group(1) if m else text.strip()


def _parse_response(raw: str) -> ExtractionResponse:
    """Parse and validate raw JSON text into ExtractionResponse."""
    cleaned = _strip_fences(raw)
    return ExtractionResponse.model_validate_json(cleaned)


class GroqClient:
    """
    Production LLM client backed by the Groq API (OpenAI-compatible).

    Raises LLMConfigError if the API key is missing at construction time
    so failures surface at startup rather than on the first request.
    """

    def __init__(self, settings: Settings) -> None:
        from openai import OpenAI  # noqa: F401 (kept local to isolate SDK dep)

        if not settings.groq_api_key:
            raise LLMConfigError(
                "GROQ_API_KEY is not set. "
                "Set it in .env or use LLM_PROVIDER=mock."
            )

        self._model = settings.groq_model
        self._timeout = settings.llm_timeout_seconds
        self._system_prompt = build_system_prompt()

        # Instantiate the SDK client — key is read from settings, never logged
        self._client = OpenAI(
            api_key=settings.groq_api_key,
            base_url=_GROQ_BASE_URL,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        """
        Call Groq to extract proposed updates from the user message.

        Strips fences, validates with Pydantic. On first failure, retries
        once with a repair prompt. Raises typed LLMError subclasses on all
        failure paths so callers never have to inspect raw SDK exceptions.
        """
        user_prompt = build_user_prompt(
            state_json=request.state_json,
            recent_messages=request.recent_messages,
            user_message=request.user_message,
            focus_field=request.focus_field,
            pending_conflict=request.pending_conflict,
        )

        # --- First attempt ---
        raw = self._call_sdk(user_prompt)
        first_error: Optional[Exception] = None
        try:
            return _parse_response(raw)
        except Exception as exc:
            first_error = exc
            logger.warning(
                "Groq response parse failed (attempt 1): %s — attempting repair",
                type(exc).__name__,
            )

        # --- Repair attempt ---
        repair_prompt = build_repair_prompt(
            bad_json=_strip_fences(raw),
            validation_error=str(first_error),
        )
        raw2 = self._call_sdk(repair_prompt)
        try:
            return _parse_response(raw2)
        except Exception as repair_err:
            logger.error(
                "Groq repair attempt also failed: %s",
                type(repair_err).__name__,
            )
            raise LLMMalformedResponse(
                f"Groq returned unparseable output after repair: {repair_err}"
            ) from repair_err

    # ------------------------------------------------------------------
    # Internal SDK call + error mapping
    # ------------------------------------------------------------------

    def _call_sdk(self, prompt: str) -> str:
        """
        Make one chat.completions.create call and return the response text.

        Maps SDK exceptions to our typed error hierarchy so the rest of
        the codebase never needs to know about openai internals.
        Never logs the API key or full prompt content.
        """
        from openai import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            AuthenticationError,
            RateLimitError,
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": self._system_prompt},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
                timeout=self._timeout,
            )
            return response.choices[0].message.content or ""

        except RateLimitError as exc:
            raise LLMRateLimited("Groq rate limit (429)") from exc

        except AuthenticationError as exc:
            raise LLMConfigError(
                "Groq authentication error. Check GROQ_API_KEY."
            ) from exc

        except APITimeoutError as exc:
            raise LLMUnavailable("Groq request timed out") from exc

        except APIConnectionError as exc:
            raise LLMUnavailable(
                f"Groq network error: {type(exc).__name__}"
            ) from exc

        except APIStatusError as exc:
            code = exc.status_code
            if code == 429:
                raise LLMRateLimited(f"Groq rate limit ({code})") from exc
            if code in (401, 403):
                raise LLMConfigError(
                    f"Groq authentication error ({code}). Check GROQ_API_KEY."
                ) from exc
            raise LLMUnavailable(f"Groq API error ({code})") from exc

        except Exception as exc:
            logger.error("Unexpected error calling Groq: %s", type(exc).__name__)
            raise LLMUnavailable(f"Unexpected error: {type(exc).__name__}") from exc
