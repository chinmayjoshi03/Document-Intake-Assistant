"""
Tests for domain/updates.py — update validation logic.

Covers: valid updates, unknown field, wrong type, evidence check,
low confidence, multi-field batch, empty-string additional_wishes,
empty list specific_gifts, direct-edit skips evidence check.
"""

from __future__ import annotations

import pytest

from app.domain.updates import (
    ProposedUpdate,
    validate_update,
    validate_updates,
)
from app.domain.state import FieldPath


MSG = "My name is Jane Smith and my brother James is my executor."


def make_update(**kwargs) -> ProposedUpdate:
    defaults = dict(
        field="full_name",
        value_text=None,
        value_bool=None,
        value_list=None,
        confidence="high",
        evidence="Jane Smith",
    )
    defaults.update(kwargs)
    return ProposedUpdate(**defaults)


# ---------------------------------------------------------------------------
# Valid updates
# ---------------------------------------------------------------------------

class TestValidUpdates:
    def test_valid_string_field(self):
        upd = make_update(field="full_name", value_text="Jane Smith", evidence="Jane Smith")
        v, r = validate_update(upd, MSG, None)
        assert v is not None
        assert r is None
        assert v.path == FieldPath.FULL_NAME
        assert v.value == "Jane Smith"

    def test_valid_bool_field(self):
        upd = make_update(
            field="has_children", value_text=None, value_bool=False,
            evidence="no", confidence="high",
        )
        v, r = validate_update(upd, "no", FieldPath.HAS_CHILDREN)
        assert v is not None
        assert v.path == FieldPath.HAS_CHILDREN
        assert v.value is False

    def test_valid_list_field(self):
        upd = make_update(
            field="specific_gifts", value_text=None, value_list=["gold watch"],
            evidence="gold watch", confidence="high",
        )
        v, r = validate_update(upd, "I want to leave a gold watch.", None)
        assert v is not None
        assert v.value == ["gold watch"]

    def test_valid_empty_list(self):
        """Empty list for specific_gifts is valid — means 'no gifts'."""
        upd = make_update(
            field="specific_gifts", value_text=None, value_list=[],
            evidence="no gifts", confidence="high",
        )
        v, r = validate_update(upd, "I have no gifts to leave.", None)
        assert v is not None
        assert v.value == []

    def test_valid_empty_string_additional_wishes(self):
        """Empty string for additional_wishes is valid — means 'none'."""
        upd = make_update(
            field="additional_wishes", value_text="", evidence="none",
        )
        v, r = validate_update(upd, "none", None)
        assert v is not None
        assert v.value == ""

    def test_string_is_trimmed(self):
        upd = make_update(
            field="full_name", value_text="  Jane Smith  ", evidence="Jane Smith",
        )
        v, r = validate_update(upd, MSG, None)
        assert v is not None
        assert v.value == "Jane Smith"

    def test_executor_name(self):
        upd = make_update(
            field="executor.name", value_text="James", evidence="James",
        )
        v, r = validate_update(upd, MSG, None)
        assert v is not None
        assert v.path == FieldPath.EXECUTOR_NAME


# ---------------------------------------------------------------------------
# Unknown field
# ---------------------------------------------------------------------------

class TestUnknownField:
    def test_unknown_field_rejected(self):
        upd = make_update(field="favourite_colour", value_text="blue", evidence="blue")
        v, r = validate_update(upd, "My favourite colour is blue.", None)
        assert v is None
        assert r is not None
        assert "unknown field path" in r.reason

    def test_valid_updates_in_batch_still_applied(self):
        """One bad update must not block the valid ones."""
        bad = make_update(field="not_a_field", value_text="x", evidence="x")
        good = make_update(field="full_name", value_text="Jane Smith", evidence="Jane Smith")
        valid, rejected = validate_updates([bad, good], MSG, None)
        assert len(valid) == 1
        assert valid[0].path == FieldPath.FULL_NAME
        assert len(rejected) == 1


# ---------------------------------------------------------------------------
# Wrong type
# ---------------------------------------------------------------------------

class TestWrongType:
    def test_bool_field_with_text_slot(self):
        upd = make_update(
            field="has_children", value_text="yes", value_bool=None, evidence="yes",
        )
        v, r = validate_update(upd, "yes", FieldPath.HAS_CHILDREN)
        assert v is None
        assert "bool" in r.reason

    def test_str_field_with_bool_slot(self):
        upd = make_update(
            field="full_name", value_text=None, value_bool=True, evidence="true",
        )
        v, r = validate_update(upd, "true", None)
        assert v is None
        assert "string" in r.reason

    def test_list_field_with_text_slot(self):
        upd = make_update(
            field="specific_gifts", value_text="watch", value_list=None, evidence="watch",
        )
        v, r = validate_update(upd, "watch", None)
        assert v is None
        assert "list" in r.reason


# ---------------------------------------------------------------------------
# Multiple value slots set
# ---------------------------------------------------------------------------

class TestMultipleSlots:
    def test_two_slots_set_rejected(self):
        upd = make_update(
            field="full_name", value_text="Jane", value_bool=True, evidence="Jane",
        )
        v, r = validate_update(upd, MSG, None)
        assert v is None
        assert "2 value slots" in r.reason

    def test_no_slots_set_rejected(self):
        upd = make_update(
            field="full_name", value_text=None, value_bool=None,
            value_list=None, evidence="Jane",
        )
        v, r = validate_update(upd, MSG, None)
        assert v is None
        assert "no value slot" in r.reason


# ---------------------------------------------------------------------------
# Evidence check
# ---------------------------------------------------------------------------

class TestEvidenceCheck:
    def test_hallucinated_evidence_rejected(self):
        """Evidence that doesn't appear in the user message is rejected."""
        upd = make_update(
            field="full_name", value_text="Jane Smith",
            evidence="Jane Smith Hallucinated Surname",
        )
        v, r = validate_update(upd, "My name is Jane.", None)
        assert v is None
        assert "hallucination" in r.reason.lower() or "not a substring" in r.reason.lower()

    def test_case_insensitive_match(self):
        upd = make_update(
            field="full_name", value_text="Jane Smith", evidence="jane smith",
        )
        v, r = validate_update(upd, "My name is Jane Smith.", None)
        assert v is not None

    def test_bare_yes_allowed_as_evidence(self):
        """A bare 'yes' answer is allowed when evidence == full message."""
        upd = make_update(
            field="has_children", value_text=None, value_bool=True,
            evidence="yes",
        )
        v, r = validate_update(upd, "yes", FieldPath.HAS_CHILDREN)
        assert v is not None

    def test_bare_no_allowed_as_evidence(self):
        upd = make_update(
            field="has_children", value_text=None, value_bool=False,
            evidence="no",
        )
        v, r = validate_update(upd, "no", FieldPath.HAS_CHILDREN)
        assert v is not None

    def test_evidence_skipped_for_direct_edit(self):
        """UI direct edits bypass the evidence check."""
        upd = make_update(
            field="full_name", value_text="Jane Smith",
            evidence="this_is_not_in_the_message",
        )
        v, r = validate_update(upd, "some other message", None, skip_evidence_check=True)
        assert v is not None


# ---------------------------------------------------------------------------
# Low confidence
# ---------------------------------------------------------------------------

class TestLowConfidence:
    def test_low_confidence_not_applied(self):
        upd = make_update(
            field="full_name", value_text="Jane Smith",
            evidence="Jane Smith", confidence="low",
        )
        v, r = validate_update(upd, MSG, None)
        assert v is None
        assert r is not None
        assert "low" in r.reason


# ---------------------------------------------------------------------------
# Empty string for non-additional_wishes field
# ---------------------------------------------------------------------------

class TestEmptyStringNonPermissive:
    def test_empty_name_rejected(self):
        upd = make_update(field="full_name", value_text="   ", evidence="   ")
        v, r = validate_update(upd, "   ", None)
        assert v is None
        assert "empty" in r.reason
