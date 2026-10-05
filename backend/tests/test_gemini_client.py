"""
Tests for llm/gemini_client.py — all SDK calls are monkeypatched.

Covers (spec §15.12):
- Fence stripping
- Successful parse path
- Repair retry on first failure + success on second
- Double failure → LLMMalformedResponse
- 429 → LLMRateLimited
- Timeout → LLMUnavailable
- Missing key → LLMConfigError
- 401/403 → LLMConfigError
- Server error → LLMUnavailable
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.config import Settings
from app.llm.base import (
    ExtractionRequest,
    ExtractionResponse,
    LLMConfigError,
    LLMMalformedResponse,
    LLMRateLimited,
    LLMUnavailable,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_JSON = json.dumps({
    "intent": "answer",
    "updates": [
        {
            "field": "full_name",
            "value_text": "Jane Smith",
            "value_bool": None,
            "value_list": None,
            "confidence": "high",
            "evidence": "Jane Smith",
        }
    ],
    "ambiguities": [],
    "conflict_resolution": None,
    "acknowledgement": "Got it.",
})

FENCED_JSON = f"```json\n{VALID_JSON}\n```"
FENCED_NO_LANG = f"```\n{VALID_JSON}\n```"
INVALID_JSON = "I'm sorry, I cannot help with that."
MISSING_KEYS_JSON = json.dumps({"acknowledgement": "Sure."})

VALID_SETTINGS = Settings(
    LLM_PROVIDER="gemini",
    GEMINI_API_KEY="test-key-123",
    GEMINI_MODEL="gemini-2.5-flash",
    LLM_TIMEOUT_SECONDS=20,
)

EMPTY_SETTINGS = Settings(
    LLM_PROVIDER="gemini",
    GEMINI_API_KEY=None,
    GEMINI_MODEL="gemini-2.5-flash",
    LLM_TIMEOUT_SECONDS=20,
)


def make_request() -> ExtractionRequest:
    return ExtractionRequest(
        state_json="{}",
        recent_messages=[],
        user_message="My name is Jane Smith.",
        focus_field=None,
        pending_conflict=None,
    )


def make_mock_response(text: str) -> MagicMock:
    """Create a mock GenerateContentResponse where .text returns the given string."""
    mock = MagicMock()
    mock.text = text
    return mock


def make_client(settings: Settings = VALID_SETTINGS) -> "GeminiClient":
    """Build a GeminiClient with the SDK client constructor patched out."""
    with patch("google.genai.Client") as MockClient:
        MockClient.return_value = MagicMock()
        from app.llm.gemini_client import GeminiClient
        return GeminiClient(settings)


# ---------------------------------------------------------------------------
# Fence stripping
# ---------------------------------------------------------------------------

class TestFenceStripping:
    def test_strips_json_fence(self):
        from app.llm.gemini_client import _strip_fences
        assert _strip_fences(FENCED_JSON) == VALID_JSON

    def test_strips_bare_fence(self):
        from app.llm.gemini_client import _strip_fences
        assert _strip_fences(FENCED_NO_LANG) == VALID_JSON

    def test_passthrough_when_no_fence(self):
        from app.llm.gemini_client import _strip_fences
        assert _strip_fences(VALID_JSON) == VALID_JSON

    def test_strips_whitespace(self):
        from app.llm.gemini_client import _strip_fences
        assert _strip_fences(f"  {VALID_JSON}  ") == VALID_JSON


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_successful_extraction(self):
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.return_value = make_mock_response(VALID_JSON)
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            result = client.extract(make_request())

        assert isinstance(result, ExtractionResponse)
        assert result.intent == "answer"
        assert result.updates[0].value_text == "Jane Smith"

    def test_fenced_response_parsed_correctly(self):
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.return_value = make_mock_response(FENCED_JSON)
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            result = client.extract(make_request())

        assert result.updates[0].value_text == "Jane Smith"


# ---------------------------------------------------------------------------
# Repair retry
# ---------------------------------------------------------------------------

class TestRepairRetry:
    def test_repair_succeeds_on_second_attempt(self):
        """First call returns invalid JSON; second (repair) returns valid JSON."""
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = [
                make_mock_response(INVALID_JSON),   # first: unparseable
                make_mock_response(VALID_JSON),     # second: valid
            ]
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            result = client.extract(make_request())

        assert mock_sdk.models.generate_content.call_count == 2
        assert isinstance(result, ExtractionResponse)

    def test_double_failure_raises_malformed(self):
        """Both attempts return invalid JSON → LLMMalformedResponse."""
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = [
                make_mock_response(INVALID_JSON),
                make_mock_response(MISSING_KEYS_JSON),
            ]
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)

            with pytest.raises(LLMMalformedResponse):
                client.extract(make_request())

        assert mock_sdk.models.generate_content.call_count == 2


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

class TestErrorMapping:
    def _make_client_error(self, code: int):
        """Create a google.genai ClientError with the given HTTP code."""
        from google.genai.errors import ClientError
        err = ClientError.__new__(ClientError)
        err.code = code
        err.message = f"HTTP {code}"
        err.status = str(code)
        err.details = {}
        return err

    def _make_server_error(self, code: int = 500):
        from google.genai.errors import ServerError
        err = ServerError.__new__(ServerError)
        err.code = code
        err.message = f"HTTP {code}"
        err.status = str(code)
        err.details = {}
        return err

    def test_429_raises_rate_limited(self):
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = self._make_client_error(429)
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            with pytest.raises(LLMRateLimited):
                client.extract(make_request())

    def test_401_raises_config_error(self):
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = self._make_client_error(401)
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            with pytest.raises(LLMConfigError):
                client.extract(make_request())

    def test_403_raises_config_error(self):
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = self._make_client_error(403)
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            with pytest.raises(LLMConfigError):
                client.extract(make_request())

    def test_server_error_raises_unavailable(self):
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = self._make_server_error(503)
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            with pytest.raises(LLMUnavailable):
                client.extract(make_request())

    def test_timeout_raises_unavailable(self):
        import httpx
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = httpx.TimeoutException("timeout")
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            with pytest.raises(LLMUnavailable):
                client.extract(make_request())

    def test_network_error_raises_unavailable(self):
        import httpx
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.side_effect = httpx.NetworkError("connection refused")
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            client = GeminiClient(VALID_SETTINGS)
            with pytest.raises(LLMUnavailable):
                client.extract(make_request())


# ---------------------------------------------------------------------------
# Missing key
# ---------------------------------------------------------------------------

class TestMissingKey:
    def test_missing_key_raises_config_error_at_construction(self):
        """GeminiClient should raise LLMConfigError immediately if no key set."""
        with patch("google.genai.Client"):
            from app.llm.gemini_client import GeminiClient
            with pytest.raises(LLMConfigError):
                GeminiClient(EMPTY_SETTINGS)


# ---------------------------------------------------------------------------
# SDK call receives correct model name
# ---------------------------------------------------------------------------

class TestModelName:
    def test_uses_model_from_settings(self):
        with patch("google.genai.Client") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.models.generate_content.return_value = make_mock_response(VALID_JSON)
            MockClient.return_value = mock_sdk

            from app.llm.gemini_client import GeminiClient
            settings = Settings(
                LLM_PROVIDER="gemini",
                GEMINI_API_KEY="key",
                GEMINI_MODEL="gemini-2.5-flash",
                LLM_TIMEOUT_SECONDS=20,
            )
            client = GeminiClient(settings)
            client.extract(make_request())

        call_kwargs = mock_sdk.models.generate_content.call_args
        assert call_kwargs[1]["model"] == "gemini-2.5-flash" or \
               call_kwargs[0][0] == "gemini-2.5-flash" or \
               "gemini-2.5-flash" in str(call_kwargs)
