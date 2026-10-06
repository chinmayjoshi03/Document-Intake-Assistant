"""
Tests for llm/groq_client.py — all SDK calls are monkeypatched.

Covers:
- Fence stripping
- Successful parse path
- Repair retry on first failure + success on second
- Double failure → LLMMalformedResponse
- RateLimitError → LLMRateLimited
- APITimeoutError → LLMUnavailable
- APIConnectionError → LLMUnavailable
- AuthenticationError → LLMConfigError
- APIStatusError(4xx/5xx) → mapped correctly
- Missing key → LLMConfigError at construction time
- Correct model name forwarded to SDK
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

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

FENCED_JSON     = f"```json\n{VALID_JSON}\n```"
FENCED_NO_LANG  = f"```\n{VALID_JSON}\n```"
INVALID_JSON    = "I'm sorry, I cannot help with that."
MISSING_KEYS_JSON = json.dumps({"acknowledgement": "Sure."})

VALID_SETTINGS = Settings(
    LLM_PROVIDER="groq",
    GROQ_API_KEY="gsk_test-key-123",
    GROQ_MODEL="openai/gpt-oss-20b",
    LLM_TIMEOUT_SECONDS=20,
)

EMPTY_SETTINGS = Settings(
    LLM_PROVIDER="groq",
    GROQ_API_KEY=None,
    GROQ_MODEL="openai/gpt-oss-20b",
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


def make_mock_completion(text: str) -> MagicMock:
    """Create a mock openai ChatCompletion where .choices[0].message.content returns text."""
    mock = MagicMock()
    mock.choices[0].message.content = text
    return mock


# ---------------------------------------------------------------------------
# Fence stripping
# ---------------------------------------------------------------------------

class TestFenceStripping:
    def test_strips_json_fence(self):
        from app.llm.groq_client import _strip_fences
        assert _strip_fences(FENCED_JSON) == VALID_JSON

    def test_strips_bare_fence(self):
        from app.llm.groq_client import _strip_fences
        assert _strip_fences(FENCED_NO_LANG) == VALID_JSON

    def test_passthrough_when_no_fence(self):
        from app.llm.groq_client import _strip_fences
        assert _strip_fences(VALID_JSON) == VALID_JSON

    def test_strips_whitespace(self):
        from app.llm.groq_client import _strip_fences
        assert _strip_fences(f"  {VALID_JSON}  ") == VALID_JSON


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_successful_extraction(self):
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.return_value = make_mock_completion(VALID_JSON)
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            result = client.extract(make_request())

        assert isinstance(result, ExtractionResponse)
        assert result.intent == "answer"
        assert result.updates[0].value_text == "Jane Smith"

    def test_fenced_response_parsed_correctly(self):
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.return_value = make_mock_completion(FENCED_JSON)
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            result = client.extract(make_request())

        assert result.updates[0].value_text == "Jane Smith"


# ---------------------------------------------------------------------------
# Repair retry
# ---------------------------------------------------------------------------

class TestRepairRetry:
    def test_repair_succeeds_on_second_attempt(self):
        """First call returns invalid JSON; second (repair) returns valid JSON."""
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = [
                make_mock_completion(INVALID_JSON),  # first: unparseable
                make_mock_completion(VALID_JSON),    # second: valid repair
            ]
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            result = client.extract(make_request())

        assert mock_sdk.chat.completions.create.call_count == 2
        assert isinstance(result, ExtractionResponse)

    def test_double_failure_raises_malformed(self):
        """Both attempts return invalid JSON → LLMMalformedResponse."""
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = [
                make_mock_completion(INVALID_JSON),
                make_mock_completion(MISSING_KEYS_JSON),
            ]
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)

            with pytest.raises(LLMMalformedResponse):
                client.extract(make_request())

        assert mock_sdk.chat.completions.create.call_count == 2


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

class TestErrorMapping:
    def test_rate_limit_raises_rate_limited(self):
        from openai import RateLimitError
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = RateLimitError(
                message="rate limit", response=MagicMock(status_code=429), body={}
            )
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            with pytest.raises(LLMRateLimited):
                client.extract(make_request())

    def test_authentication_error_raises_config_error(self):
        from openai import AuthenticationError
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = AuthenticationError(
                message="invalid key", response=MagicMock(status_code=401), body={}
            )
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            with pytest.raises(LLMConfigError):
                client.extract(make_request())

    def test_timeout_raises_unavailable(self):
        from openai import APITimeoutError
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = APITimeoutError(request=MagicMock())
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            with pytest.raises(LLMUnavailable):
                client.extract(make_request())

    def test_connection_error_raises_unavailable(self):
        from openai import APIConnectionError
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            with pytest.raises(LLMUnavailable):
                client.extract(make_request())

    def test_server_status_error_raises_unavailable(self):
        from openai import APIStatusError
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.side_effect = APIStatusError(
                message="server error",
                response=MagicMock(status_code=503),
                body={},
            )
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            with pytest.raises(LLMUnavailable):
                client.extract(make_request())


# ---------------------------------------------------------------------------
# Missing key
# ---------------------------------------------------------------------------

class TestMissingKey:
    def test_missing_key_raises_config_error_at_construction(self):
        """GroqClient should raise LLMConfigError immediately if no key set."""
        with patch("openai.OpenAI"):
            from app.llm.groq_client import GroqClient
            with pytest.raises(LLMConfigError):
                GroqClient(EMPTY_SETTINGS)


# ---------------------------------------------------------------------------
# Correct model name forwarded to SDK
# ---------------------------------------------------------------------------

class TestModelName:
    def test_uses_model_from_settings(self):
        with patch("openai.OpenAI") as MockClient:
            mock_sdk = MagicMock()
            mock_sdk.chat.completions.create.return_value = make_mock_completion(VALID_JSON)
            MockClient.return_value = mock_sdk

            from app.llm.groq_client import GroqClient
            client = GroqClient(VALID_SETTINGS)
            client.extract(make_request())

        call_kwargs = mock_sdk.chat.completions.create.call_args
        assert "openai/gpt-oss-20b" in str(call_kwargs)
