"""
Tests for domain/state.py — the structured state model.

These tests cover: field status lifecycle, get/set/clear helpers,
missing_paths logic (including the children_names conditional),
is_complete, and confirm_all_provided.
"""

from __future__ import annotations

import pytest

from app.domain.state import (
    FieldPath,
    FieldStatus,
    Field_,
    WishesState,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_empty_state() -> WishesState:
    return WishesState()


def fill_all(state: WishesState, *, has_children: bool = False) -> None:
    """Fill every applicable field so the state is complete."""
    state.set(FieldPath.FULL_NAME, "Jane Smith")
    state.set(FieldPath.HOME_ADDRESS, "1 High St, London")
    state.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
    state.set(FieldPath.HAS_CHILDREN, has_children)
    if has_children:
        state.set(FieldPath.CHILDREN_NAMES, ["Alice", "Bob"])
    state.set(FieldPath.EXECUTOR_NAME, "James Smith")
    state.set(FieldPath.EXECUTOR_RELATIONSHIP, "brother")
    state.set(FieldPath.SPECIFIC_GIFTS, [])
    state.set(FieldPath.ADDITIONAL_WISHES, "")


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    def test_all_fields_unknown(self):
        state = make_empty_state()
        for path in FieldPath:
            field = state.get(path)
            assert field.status == FieldStatus.UNKNOWN, f"{path} should be UNKNOWN"
            assert field.value is None, f"{path}.value should be None"

    def test_missing_paths_returns_all_except_children_names(self):
        state = make_empty_state()
        missing = state.missing_paths()
        # children_names is skipped when has_children is UNKNOWN
        assert FieldPath.CHILDREN_NAMES not in missing
        for path in FieldPath:
            if path != FieldPath.CHILDREN_NAMES:
                assert path in missing

    def test_not_complete(self):
        assert make_empty_state().is_complete() is False


# ---------------------------------------------------------------------------
# get / set / clear round-trips
# ---------------------------------------------------------------------------

class TestGetSetClear:
    def test_set_string_field(self):
        state = make_empty_state()
        state.set(FieldPath.FULL_NAME, "Jane")
        f = state.get(FieldPath.FULL_NAME)
        assert f.value == "Jane"
        assert f.status == FieldStatus.PROVIDED

    def test_set_bool_field(self):
        state = make_empty_state()
        state.set(FieldPath.HAS_CHILDREN, False)
        f = state.get(FieldPath.HAS_CHILDREN)
        assert f.value is False
        assert f.status == FieldStatus.PROVIDED

    def test_set_list_field(self):
        state = make_empty_state()
        state.set(FieldPath.SPECIFIC_GIFTS, ["watch", "ring"])
        f = state.get(FieldPath.SPECIFIC_GIFTS)
        assert f.value == ["watch", "ring"]
        assert f.status == FieldStatus.PROVIDED

    def test_set_empty_list_is_provided(self):
        """Empty list means 'no gifts' — that is explicitly PROVIDED, not UNKNOWN."""
        state = make_empty_state()
        state.set(FieldPath.SPECIFIC_GIFTS, [])
        f = state.get(FieldPath.SPECIFIC_GIFTS)
        assert f.value == []
        assert f.status == FieldStatus.PROVIDED

    def test_set_empty_string_is_provided(self):
        """Empty string for additional_wishes means 'none' — explicitly PROVIDED."""
        state = make_empty_state()
        state.set(FieldPath.ADDITIONAL_WISHES, "")
        f = state.get(FieldPath.ADDITIONAL_WISHES)
        assert f.value == ""
        assert f.status == FieldStatus.PROVIDED

    def test_set_executor_fields(self):
        state = make_empty_state()
        state.set(FieldPath.EXECUTOR_NAME, "James")
        state.set(FieldPath.EXECUTOR_RELATIONSHIP, "brother")
        assert state.get(FieldPath.EXECUTOR_NAME).value == "James"
        assert state.get(FieldPath.EXECUTOR_RELATIONSHIP).value == "brother"

    def test_clear_resets_to_unknown(self):
        state = make_empty_state()
        state.set(FieldPath.FULL_NAME, "Jane")
        state.clear(FieldPath.FULL_NAME)
        f = state.get(FieldPath.FULL_NAME)
        assert f.value is None
        assert f.status == FieldStatus.UNKNOWN

    def test_overwrite_provided_stays_provided(self):
        state = make_empty_state()
        state.set(FieldPath.FULL_NAME, "Jane")
        state.set(FieldPath.FULL_NAME, "Jane Smith")
        f = state.get(FieldPath.FULL_NAME)
        assert f.value == "Jane Smith"
        assert f.status == FieldStatus.PROVIDED


# ---------------------------------------------------------------------------
# Correction of CONFIRMED fields
# ---------------------------------------------------------------------------

class TestConfirmedCorrection:
    def test_correcting_confirmed_drops_to_provided(self):
        """
        Correcting a CONFIRMED field must reset it to PROVIDED so it
        goes through the review step again.
        """
        state = make_empty_state()
        state.set(FieldPath.FULL_NAME, "Jane", FieldStatus.CONFIRMED)
        assert state.get(FieldPath.FULL_NAME).status == FieldStatus.CONFIRMED

        # Correction arrives with status PROVIDED
        state.set(FieldPath.FULL_NAME, "Jane Smith", FieldStatus.PROVIDED)
        f = state.get(FieldPath.FULL_NAME)
        assert f.value == "Jane Smith"
        assert f.status == FieldStatus.PROVIDED  # dropped back from CONFIRMED


# ---------------------------------------------------------------------------
# missing_paths / children_names conditional logic
# ---------------------------------------------------------------------------

class TestMissingPaths:
    def test_children_names_required_when_has_children_true(self):
        state = make_empty_state()
        state.set(FieldPath.HAS_CHILDREN, True)
        missing = state.missing_paths()
        assert FieldPath.CHILDREN_NAMES in missing

    def test_children_names_not_required_when_has_children_false(self):
        state = make_empty_state()
        state.set(FieldPath.HAS_CHILDREN, False)
        missing = state.missing_paths()
        assert FieldPath.CHILDREN_NAMES not in missing

    def test_children_names_skipped_while_has_children_unknown(self):
        """Don't ask for children names before we know if there are children."""
        state = make_empty_state()
        missing = state.missing_paths()
        assert FieldPath.CHILDREN_NAMES not in missing

    def test_provided_field_not_in_missing(self):
        state = make_empty_state()
        state.set(FieldPath.FULL_NAME, "Jane")
        assert FieldPath.FULL_NAME not in state.missing_paths()

    def test_confirmed_field_not_in_missing(self):
        state = make_empty_state()
        state.set(FieldPath.FULL_NAME, "Jane", FieldStatus.CONFIRMED)
        assert FieldPath.FULL_NAME not in state.missing_paths()


# ---------------------------------------------------------------------------
# Completeness
# ---------------------------------------------------------------------------

class TestIsComplete:
    def test_complete_without_children(self):
        state = make_empty_state()
        fill_all(state, has_children=False)
        assert state.is_complete() is True

    def test_complete_with_children(self):
        state = make_empty_state()
        fill_all(state, has_children=True)
        assert state.is_complete() is True

    def test_incomplete_missing_one_field(self):
        state = make_empty_state()
        fill_all(state, has_children=False)
        state.clear(FieldPath.EXECUTOR_NAME)
        assert state.is_complete() is False

    def test_incomplete_missing_children_names(self):
        state = make_empty_state()
        fill_all(state, has_children=True)
        state.clear(FieldPath.CHILDREN_NAMES)
        assert state.is_complete() is False


# ---------------------------------------------------------------------------
# confirm_all_provided
# ---------------------------------------------------------------------------

class TestConfirmAll:
    def test_confirm_all_marks_provided_as_confirmed(self):
        state = make_empty_state()
        fill_all(state, has_children=False)
        assert state.all_confirmed() is False
        state.confirm_all_provided()
        assert state.all_confirmed() is True

    def test_confirm_does_not_affect_unknown(self):
        state = make_empty_state()
        state.set(FieldPath.FULL_NAME, "Jane")
        state.confirm_all_provided()
        # FULL_NAME confirmed; others still UNKNOWN
        assert state.get(FieldPath.FULL_NAME).status == FieldStatus.CONFIRMED
        assert state.get(FieldPath.HOME_ADDRESS).status == FieldStatus.UNKNOWN

    def test_all_confirmed_false_with_unknown_fields(self):
        state = make_empty_state()
        assert state.all_confirmed() is False
