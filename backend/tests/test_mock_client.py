"""
Tests for llm/mock_client.py and llm/base.py.

Covers: mock client heuristics, Protocol compliance, fixture loading,
ScriptedLLMClient behaviour, and the LLM error hierarchy.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.llm.base import (
    ExtractionRequest,
    ExtractionResponse,
    LLMClient,
    LLMMalformedResponse,
)
from app.llm.mock_client import MockLLMClient
from app.domain.state import FieldPath
from tests.conftest import ScriptedLLMClient, load_fixture, response_from_fixture

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def make_request(message: str, focus: str | None = None, pending: str | None = None) -> ExtractionRequest:
    return ExtractionRequest(
        state_json="{}",
        recent_messages=[],
        user_message=message,
        focus_field=focus,
        pending_conflict=pending,
    )


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_mock_client_satisfies_protocol(self):
        client = MockLLMClient()
        assert isinstance(client, LLMClient)

    def test_scripted_client_satisfies_protocol(self):
        client = ScriptedLLMClient([])
        assert isinstance(client, LLMClient)


# ---------------------------------------------------------------------------
# Mock client: name extraction
# ---------------------------------------------------------------------------

class TestMockNameExtraction:
    def test_my_name_is_pattern(self):
        client = MockLLMClient()
        resp = client.extract(make_request("My name is Jane Smith."))
        paths = [u.field for u in resp.updates]
        assert FieldPath.FULL_NAME.value in paths
        name_upd = next(u for u in resp.updates if u.field == FieldPath.FULL_NAME.value)
        assert name_upd.value_text == "Jane Smith"

    def test_name_evidence_is_present(self):
        client = MockLLMClient()
        resp = client.extract(make_request("My name is Jane Smith."))
        upd = next(u for u in resp.updates if u.field == FieldPath.FULL_NAME.value)
        assert upd.evidence  # evidence must be non-empty


# ---------------------------------------------------------------------------
# Mock client: yes/no with focus_field
# ---------------------------------------------------------------------------

class TestMockYesNo:
    def test_yes_with_focus_has_children(self):
        client = MockLLMClient()
        resp = client.extract(make_request("yes", focus=FieldPath.HAS_CHILDREN.value))
        assert len(resp.updates) == 1
        upd = resp.updates[0]
        assert upd.field == FieldPath.HAS_CHILDREN.value
        assert upd.value_bool is True

    def test_no_with_focus_has_children(self):
        client = MockLLMClient()
        resp = client.extract(make_request("no", focus=FieldPath.HAS_CHILDREN.value))
        upd = resp.updates[0]
        assert upd.value_bool is False

    def test_yes_with_no_focus_no_update(self):
        """Bare 'yes' with no focus field shouldn't produce a bool update."""
        client = MockLLMClient()
        resp = client.extract(make_request("yes", focus=None))
        bool_updates = [u for u in resp.updates if u.value_bool is not None]
        assert len(bool_updates) == 0


# ---------------------------------------------------------------------------
# Mock client: none answer
# ---------------------------------------------------------------------------

class TestMockNoneAnswer:
    def test_none_for_gifts(self):
        client = MockLLMClient()
        resp = client.extract(make_request("none", focus=FieldPath.SPECIFIC_GIFTS.value))
        assert len(resp.updates) == 1
        upd = resp.updates[0]
        assert upd.field == FieldPath.SPECIFIC_GIFTS.value
        assert upd.value_list == []

    def test_none_for_additional_wishes(self):
        client = MockLLMClient()
        resp = client.extract(make_request("none", focus=FieldPath.ADDITIONAL_WISHES.value))
        upd = resp.updates[0]
        assert upd.field == FieldPath.ADDITIONAL_WISHES.value
        assert upd.value_text == ""

    def test_no_gifts_phrase(self):
        client = MockLLMClient()
        resp = client.extract(make_request("no gifts", focus=FieldPath.SPECIFIC_GIFTS.value))
        upd = resp.updates[0]
        assert upd.value_list == []


# ---------------------------------------------------------------------------
# Mock client: executor pattern
# ---------------------------------------------------------------------------

class TestMockExecutor:
    def test_my_brother_james(self):
        client = MockLLMClient()
        resp = client.extract(make_request("My brother James is my executor."))
        paths = [u.field for u in resp.updates]
        assert FieldPath.EXECUTOR_NAME.value in paths
        assert FieldPath.EXECUTOR_RELATIONSHIP.value in paths

    def test_executor_name_correct(self):
        client = MockLLMClient()
        resp = client.extract(make_request("My brother James Smith is my executor."))
        name_upd = next(u for u in resp.updates if u.field == FieldPath.EXECUTOR_NAME.value)
        assert "James" in name_upd.value_text


# ---------------------------------------------------------------------------
# Mock client: address
# ---------------------------------------------------------------------------

class TestMockAddress:
    def test_i_live_at(self):
        client = MockLLMClient()
        resp = client.extract(make_request("I live at 12 High Street, London."))
        paths = [u.field for u in resp.updates]
        assert FieldPath.HOME_ADDRESS.value in paths


# ---------------------------------------------------------------------------
# Mock client: conflict resolution
# ---------------------------------------------------------------------------

class TestMockConflictResolution:
    def test_yes_resolves_conflict_accept(self):
        client = MockLLMClient()
        resp = client.extract(make_request("yes", pending="Some conflict here"))
        assert resp.conflict_resolution == "accept_proposed"

    def test_no_resolves_conflict_keep(self):
        client = MockLLMClient()
        resp = client.extract(make_request("no, keep existing", pending="Some conflict"))
        assert resp.conflict_resolution == "keep_existing"


# ---------------------------------------------------------------------------
# Fixtures load correctly
# ---------------------------------------------------------------------------

class TestFixtures:
    FIXTURE_NAMES = [
        "valid_multi_field",
        "valid_correction",
        "ambiguous_address",
        "low_confidence_name",
        "unknown_field",
        "wrong_type",
        "hallucinated_evidence",
        "prompt_injection_attempt",
    ]

    def test_all_parseable_fixtures_load(self):
        for name in self.FIXTURE_NAMES:
            resp = response_from_fixture(name)
            assert isinstance(resp, ExtractionResponse)

    def test_multi_field_has_three_updates(self):
        resp = response_from_fixture("valid_multi_field")
        assert len(resp.updates) == 3

    def test_correction_has_intent_correction(self):
        resp = response_from_fixture("valid_correction")
        assert resp.intent == "correction"

    def test_ambiguous_has_no_updates_but_ambiguity(self):
        resp = response_from_fixture("ambiguous_address")
        assert len(resp.updates) == 0
        assert len(resp.ambiguities) == 1

    def test_low_confidence_update_is_low(self):
        resp = response_from_fixture("low_confidence_name")
        assert resp.updates[0].confidence == "low"

    def test_malformed_fixtures_are_raw_strings(self):
        """malformed_not_json and malformed_missing_keys store raw strings."""
        for name in ("malformed_not_json", "malformed_missing_keys"):
            data = load_fixture(name)
            assert "raw" in data  # stored under 'raw' key


# ---------------------------------------------------------------------------
# ScriptedLLMClient
# ---------------------------------------------------------------------------

class TestScriptedClient:
    def test_returns_queued_response(self):
        resp = response_from_fixture("valid_multi_field")
        client = ScriptedLLMClient([resp])
        req = make_request("My name is Jane Smith")
        result = client.extract(req)
        assert result == resp

    def test_records_calls(self):
        resp = response_from_fixture("valid_multi_field")
        client = ScriptedLLMClient([resp])
        req = make_request("My name is Jane Smith")
        client.extract(req)
        assert len(client.calls) == 1
        assert client.calls[0] == req

    def test_raises_when_queue_empty(self):
        client = ScriptedLLMClient([])
        with pytest.raises(LLMMalformedResponse):
            client.extract(make_request("hello"))

    def test_multiple_queued_responses(self):
        r1 = response_from_fixture("valid_multi_field")
        r2 = response_from_fixture("valid_correction")
        client = ScriptedLLMClient([r1, r2])
        req = make_request("test")
        assert client.extract(req).intent == "answer"
        assert client.extract(req).intent == "correction"
