"""
Tests for domain/turn.py — the turn orchestration pipeline.

Uses ScriptedLLMClient so tests are fully deterministic with no network calls.

Covers (from spec §15):
2.  Malformed model output: one repair retry; if still fails, state/history unchanged.
3.  Unknown field + wrong type rejected; valid updates in same batch applied.
4.  Hallucinated evidence rejected.
5.  Low-confidence update not applied; clarifying question returned.
6.  Correction overwrites earlier value; correcting CONFIRMED drops to PROVIDED.
7.  has_children=false + existing names → conflict; resolves correctly.
8.  Next-question logic: canonical order, skips filled fields.
9.  Provider errors leave state untouched, map to correct codes.
11. Direct edit goes through same validation; session PATCH works.
"""

from __future__ import annotations

import asyncio
import pytest

from app.domain.session import Session, SessionStore
from app.domain.state import FieldPath, FieldStatus, WishesState
from app.domain.turn import process_turn, process_direct_edit
from app.llm.base import (
    ExtractionResponse,
    LLMConfigError,
    LLMMalformedResponse,
    LLMRateLimited,
    LLMUnavailable,
    ProposedUpdateSchema,
)
from tests.conftest import ScriptedLLMClient, response_from_fixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def make_session(store: SessionStore) -> Session:
    return await store.create()


def make_response(
    intent: str = "answer",
    updates=None,
    ambiguities=None,
    conflict_resolution=None,
    acknowledgement: str = "Got it.",
) -> ExtractionResponse:
    return ExtractionResponse(
        intent=intent,
        updates=updates or [],
        ambiguities=ambiguities or [],
        conflict_resolution=conflict_resolution,
        acknowledgement=acknowledgement,
    )


def upd(field: str, **kwargs) -> ProposedUpdateSchema:
    defaults = dict(
        field=field, value_text=None, value_bool=None, value_list=None,
        confidence="high", evidence="",
    )
    defaults.update(kwargs)
    return ProposedUpdateSchema(**defaults)


# ---------------------------------------------------------------------------
# Test 1 (spec §15.1): Multi-field message updates all fields
# ---------------------------------------------------------------------------

class TestMultiField:
    @pytest.mark.asyncio
    async def test_multi_field_updates_applied(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        # Build response with evidence present in the user message
        user_msg = "My name is Jane Smith, I live at 12 High Street, London, and my executor is James Smith."
        resp = make_response(updates=[
            upd("full_name", value_text="Jane Smith", evidence="Jane Smith"),
            upd("home_address", value_text="12 High Street, London", evidence="12 High Street, London"),
            upd("executor.name", value_text="James Smith", evidence="James Smith"),
        ])
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        assert result.error is None
        s = result.state
        assert s.get(FieldPath.FULL_NAME).value == "Jane Smith"
        assert s.get(FieldPath.HOME_ADDRESS).value == "12 High Street, London"
        assert s.get(FieldPath.EXECUTOR_NAME).value == "James Smith"

    @pytest.mark.asyncio
    async def test_filled_fields_not_re_asked(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        user_msg = "My name is Jane Smith."
        resp = make_response(updates=[
            upd("full_name", value_text="Jane Smith", evidence="Jane Smith"),
        ])
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        # Reply should ask about the NEXT field (home_address), not full_name again
        assert "full_name" not in result.reply.lower()
        assert "address" in result.reply.lower() or "home" in result.reply.lower()


# ---------------------------------------------------------------------------
# Test 2 (spec §15.2): Malformed output — state and history unchanged on failure
# ---------------------------------------------------------------------------

class TestMalformedOutput:
    @pytest.mark.asyncio
    async def test_llm_error_leaves_state_unchanged(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        # Seed some state first
        session.state.set(FieldPath.FULL_NAME, "Jane Smith")
        await store.save(session)

        client = ScriptedLLMClient([])  # will raise LLMMalformedResponse on first call

        result = await process_turn(sid, "hello", store, client)

        assert result.error is not None
        assert result.error["code"] == "llm_malformed"

        # State must be untouched
        updated_session = await store.get(sid)
        assert updated_session.state.get(FieldPath.FULL_NAME).value == "Jane Smith"

    @pytest.mark.asyncio
    async def test_history_unchanged_on_llm_error(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id
        initial_msg_count = len(session.messages)

        client = ScriptedLLMClient([])
        await process_turn(sid, "hello", store, client)

        updated = await store.get(sid)
        assert len(updated.messages) == initial_msg_count


# ---------------------------------------------------------------------------
# Test 3 (spec §15.3): Unknown field + wrong type — bad rejected, good applied
# ---------------------------------------------------------------------------

class TestMixedValidityBatch:
    @pytest.mark.asyncio
    async def test_unknown_field_rejected_valid_applied(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        user_msg = "My favourite colour is blue and my name is Jane Smith."
        resp = make_response(updates=[
            upd("favourite_colour", value_text="blue", evidence="blue"),
            upd("full_name", value_text="Jane Smith", evidence="Jane Smith"),
        ])
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        assert result.state.get(FieldPath.FULL_NAME).value == "Jane Smith"
        assert any("favourite_colour" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_wrong_type_rejected_valid_applied(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        user_msg = "yes, my name is Jane Smith"
        resp = make_response(updates=[
            upd("has_children", value_text="yes", evidence="yes"),  # wrong type: should use value_bool
            upd("full_name", value_text="Jane Smith", evidence="Jane Smith"),
        ])
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        # full_name applied
        assert result.state.get(FieldPath.FULL_NAME).value == "Jane Smith"
        # has_children NOT applied (wrong type slot)
        assert result.state.get(FieldPath.HAS_CHILDREN).status == FieldStatus.UNKNOWN
        assert any("has_children" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Test 4 (spec §15.4): Hallucinated evidence rejected
# ---------------------------------------------------------------------------

class TestHallucinatedEvidence:
    @pytest.mark.asyncio
    async def test_hallucinated_evidence_rejected(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        user_msg = "My name is Jane."
        # Evidence contains words not in the message
        resp = make_response(updates=[
            upd("full_name", value_text="Jane Elizabeth Smith-Johnson",
                evidence="Jane Elizabeth Smith-Johnson"),
        ])
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        assert result.state.get(FieldPath.FULL_NAME).status == FieldStatus.UNKNOWN
        assert any("hallucin" in w.lower() or "not a substring" in w.lower()
                   for w in result.warnings)


# ---------------------------------------------------------------------------
# Test 5 (spec §15.5): Low confidence — not applied, clarifying question returned
# ---------------------------------------------------------------------------

class TestLowConfidence:
    @pytest.mark.asyncio
    async def test_low_confidence_not_applied(self):
        from app.llm.base import AmbiguitySchema
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        user_msg = "Jane"
        resp = make_response(
            updates=[upd("full_name", value_text="Jane", evidence="Jane", confidence="low")],
            ambiguities=[
                AmbiguitySchema(
                    field="full_name",
                    reason="Only first name given",
                    clarifying_question="Could you provide your full name including surname?",
                )
            ],
        )
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        # Not applied
        assert result.state.get(FieldPath.FULL_NAME).status == FieldStatus.UNKNOWN
        # Clarifying question surfaced
        assert "surname" in result.reply.lower() or "full name" in result.reply.lower()


# ---------------------------------------------------------------------------
# Test 6 (spec §15.6): Correction overwrites; CONFIRMED → PROVIDED on correction
# ---------------------------------------------------------------------------

class TestCorrections:
    @pytest.mark.asyncio
    async def test_correction_overwrites_earlier_value(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.EXECUTOR_NAME, "James Smith")
        await store.save(session)
        sid = session.session_id

        user_msg = "Actually, my brother is called Jim Smith."
        resp = make_response(
            intent="correction",
            updates=[upd("executor.name", value_text="Jim Smith", evidence="Jim Smith")],
        )
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        assert result.state.get(FieldPath.EXECUTOR_NAME).value == "Jim Smith"

    @pytest.mark.asyncio
    async def test_correction_of_confirmed_drops_to_provided(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.FULL_NAME, "Jane Smith", FieldStatus.CONFIRMED)
        await store.save(session)
        sid = session.session_id

        user_msg = "Actually my name is Jane Doe."
        resp = make_response(
            intent="correction",
            updates=[upd("full_name", value_text="Jane Doe", evidence="Jane Doe")],
        )
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        f = result.state.get(FieldPath.FULL_NAME)
        assert f.value == "Jane Doe"
        assert f.status == FieldStatus.PROVIDED   # dropped back from CONFIRMED


# ---------------------------------------------------------------------------
# Test 7 (spec §15.7): has_children conflict + resolution
# ---------------------------------------------------------------------------

class TestChildrenConflict:
    @pytest.mark.asyncio
    async def test_has_children_false_with_existing_names_creates_conflict(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.HAS_CHILDREN, True)
        session.state.set(FieldPath.CHILDREN_NAMES, ["Alice", "Bob"])
        await store.save(session)
        sid = session.session_id

        user_msg = "no children"
        resp = make_response(updates=[
            upd("has_children", value_bool=False, evidence="no children"),
        ])
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        # Conflict created — nothing applied yet
        assert result.pending_conflict is not None
        assert result.state.get(FieldPath.HAS_CHILDREN).value is True  # unchanged

    @pytest.mark.asyncio
    async def test_accept_proposed_clears_children_names(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.HAS_CHILDREN, True)
        session.state.set(FieldPath.CHILDREN_NAMES, ["Alice"])
        # Manually create a pending conflict
        from app.domain.session import PendingConflict
        from app.domain.updates import ValidatedUpdate
        held = [ValidatedUpdate(path=FieldPath.HAS_CHILDREN, value=False, confidence="high")]
        session.pending_conflict = PendingConflict.from_validated_updates(
            "Test conflict", held
        )
        await store.save(session)
        sid = session.session_id

        user_msg = "yes"
        resp = make_response(conflict_resolution="accept_proposed")
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        assert result.pending_conflict is None
        assert result.state.get(FieldPath.HAS_CHILDREN).value is False
        assert result.state.get(FieldPath.CHILDREN_NAMES).status == FieldStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_keep_existing_discards_held_updates(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.HAS_CHILDREN, True)
        session.state.set(FieldPath.CHILDREN_NAMES, ["Alice"])
        from app.domain.session import PendingConflict
        from app.domain.updates import ValidatedUpdate
        held = [ValidatedUpdate(path=FieldPath.HAS_CHILDREN, value=False, confidence="high")]
        session.pending_conflict = PendingConflict.from_validated_updates(
            "Test conflict", held
        )
        await store.save(session)
        sid = session.session_id

        user_msg = "no keep it"
        resp = make_response(conflict_resolution="keep_existing")
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, user_msg, store, client)

        assert result.pending_conflict is None
        assert result.state.get(FieldPath.HAS_CHILDREN).value is True  # kept


# ---------------------------------------------------------------------------
# Test 8 (spec §15.8): Next-question logic
# ---------------------------------------------------------------------------

class TestNextQuestion:
    @pytest.mark.asyncio
    async def test_skips_to_next_unfilled_field(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.FULL_NAME, "Jane")
        session.state.set(FieldPath.HOME_ADDRESS, "1 High St")
        await store.save(session)
        sid = session.session_id

        # Empty response — no updates
        resp = make_response()
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, "hello", store, client)

        # Should ask about COVERS_WORLDWIDE_ASSETS (third field in order)
        assert "worldwide" in result.reply.lower() or "assets" in result.reply.lower()

    @pytest.mark.asyncio
    async def test_complete_state_shows_summary(self):
        store = SessionStore()
        session = await make_session(store)
        # Fill everything
        session.state.set(FieldPath.FULL_NAME, "Jane")
        session.state.set(FieldPath.HOME_ADDRESS, "1 High St")
        session.state.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
        session.state.set(FieldPath.HAS_CHILDREN, False)
        session.state.set(FieldPath.EXECUTOR_NAME, "James")
        session.state.set(FieldPath.EXECUTOR_RELATIONSHIP, "brother")
        session.state.set(FieldPath.SPECIFIC_GIFTS, [])
        session.state.set(FieldPath.ADDITIONAL_WISHES, "")
        await store.save(session)
        sid = session.session_id

        resp = make_response()
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, "ok", store, client)

        assert "confirm" in result.reply.lower()


# ---------------------------------------------------------------------------
# Test 9 (spec §15.9): Provider errors leave state untouched, correct codes
# ---------------------------------------------------------------------------

class TestProviderErrors:
    @pytest.mark.asyncio
    async def test_rate_limit_code(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.FULL_NAME, "Jane")
        await store.save(session)
        sid = session.session_id

        class RateLimitClient:
            def extract(self, req): raise LLMRateLimited("429")

        result = await process_turn(sid, "hello", store, RateLimitClient())
        assert result.error["code"] == "llm_rate_limited"
        updated = await store.get(sid)
        assert updated.state.get(FieldPath.FULL_NAME).value == "Jane"

    @pytest.mark.asyncio
    async def test_unavailable_code(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        class UnavailableClient:
            def extract(self, req): raise LLMUnavailable("503")

        result = await process_turn(sid, "hello", store, UnavailableClient())
        assert result.error["code"] == "llm_unavailable"

    @pytest.mark.asyncio
    async def test_config_error_code(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        class ConfigErrClient:
            def extract(self, req): raise LLMConfigError("no key")

        result = await process_turn(sid, "hello", store, ConfigErrClient())
        assert result.error["code"] == "llm_not_configured"

    @pytest.mark.asyncio
    async def test_malformed_code(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        result = await process_turn(sid, "hello", store, ScriptedLLMClient([]))
        assert result.error["code"] == "llm_malformed"


# ---------------------------------------------------------------------------
# Confirm intent marks all PROVIDED as CONFIRMED
# ---------------------------------------------------------------------------

class TestConfirmIntent:
    @pytest.mark.asyncio
    async def test_confirm_with_complete_state_marks_confirmed(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.FULL_NAME, "Jane")
        session.state.set(FieldPath.HOME_ADDRESS, "1 High St")
        session.state.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
        session.state.set(FieldPath.HAS_CHILDREN, False)
        session.state.set(FieldPath.EXECUTOR_NAME, "James")
        session.state.set(FieldPath.EXECUTOR_RELATIONSHIP, "brother")
        session.state.set(FieldPath.SPECIFIC_GIFTS, [])
        session.state.set(FieldPath.ADDITIONAL_WISHES, "")
        await store.save(session)
        sid = session.session_id

        resp = make_response(intent="confirm")
        client = ScriptedLLMClient([resp])
        result = await process_turn(sid, "confirm", store, client)

        assert result.state.all_confirmed() is True


# ---------------------------------------------------------------------------
# Test 11 (spec §15.11): Direct edit (PATCH /state)
# ---------------------------------------------------------------------------

class TestDirectEdit:
    @pytest.mark.asyncio
    async def test_direct_edit_applies_value(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        result = await process_direct_edit(
            sid,
            [{"field": "full_name", "value_text": "Jane Smith"}],
            store,
        )
        assert result.state.get(FieldPath.FULL_NAME).value == "Jane Smith"

    @pytest.mark.asyncio
    async def test_direct_edit_logged_as_user_edit(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        await process_direct_edit(
            sid,
            [{"field": "full_name", "value_text": "Jane Smith"}],
            store,
        )
        updated = await store.get(sid)
        assert updated.audit_log[-1].source == "user_edit"

    @pytest.mark.asyncio
    async def test_direct_clear_resets_field(self):
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.FULL_NAME, "Jane")
        await store.save(session)
        sid = session.session_id

        result = await process_direct_edit(
            sid,
            [{"field": "full_name", "clear": True}],
            store,
        )
        assert result.state.get(FieldPath.FULL_NAME).status == FieldStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_direct_edit_rejects_unknown_field(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        result = await process_direct_edit(
            sid,
            [{"field": "not_a_field", "value_text": "x"}],
            store,
        )
        assert any("not_a_field" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_direct_edit_conflict_detection(self):
        """Direct edits also go through conflict detection."""
        store = SessionStore()
        session = await make_session(store)
        session.state.set(FieldPath.HAS_CHILDREN, True)
        session.state.set(FieldPath.CHILDREN_NAMES, ["Alice"])
        await store.save(session)
        sid = session.session_id

        result = await process_direct_edit(
            sid,
            [{"field": "has_children", "value_bool": False}],
            store,
        )
        assert result.pending_conflict is not None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_log_records_changes(self):
        store = SessionStore()
        session = await make_session(store)
        sid = session.session_id

        user_msg = "My name is Jane Smith."
        resp = make_response(updates=[
            upd("full_name", value_text="Jane Smith", evidence="Jane Smith"),
        ])
        client = ScriptedLLMClient([resp])
        await process_turn(sid, user_msg, store, client)

        updated = await store.get(sid)
        assert len(updated.audit_log) >= 1
        entry = updated.audit_log[-1]
        assert entry.field == "full_name"
        assert entry.new_value == "Jane Smith"
        assert entry.source == "llm"
