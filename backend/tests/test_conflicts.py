"""
Tests for domain/conflicts.py — cross-field conflict detection and resolution.

Covers: children inconsistency (both directions), same-field duplicate,
accept_proposed resolution (including children_names clear), keep_existing.
"""

from __future__ import annotations

import pytest

from app.domain.conflicts import detect_conflicts, resolve_conflict
from app.domain.updates import ValidatedUpdate
from app.domain.state import FieldPath, FieldStatus, WishesState


def make_update(path: FieldPath, value, confidence: str = "high") -> ValidatedUpdate:
    return ValidatedUpdate(path=path, value=value, confidence=confidence)


def state_with_children_names() -> WishesState:
    s = WishesState()
    s.set(FieldPath.HAS_CHILDREN, True)
    s.set(FieldPath.CHILDREN_NAMES, ["Alice", "Bob"])
    return s


def state_with_no_children() -> WishesState:
    s = WishesState()
    s.set(FieldPath.HAS_CHILDREN, False)
    return s


# ---------------------------------------------------------------------------
# No conflict
# ---------------------------------------------------------------------------

class TestNoConflict:
    def test_unrelated_fields_no_conflict(self):
        state = WishesState()
        updates = [
            make_update(FieldPath.FULL_NAME, "Jane"),
            make_update(FieldPath.HOME_ADDRESS, "1 High St"),
        ]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is False
        assert result.safe_updates == updates

    def test_has_children_true_with_names_no_conflict(self):
        state = WishesState()
        updates = [
            make_update(FieldPath.HAS_CHILDREN, True),
            make_update(FieldPath.CHILDREN_NAMES, ["Alice"]),
        ]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is False

    def test_has_children_false_no_names_no_conflict(self):
        state = WishesState()
        updates = [make_update(FieldPath.HAS_CHILDREN, False)]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is False


# ---------------------------------------------------------------------------
# Rule 1: has_children / children_names inconsistency
# ---------------------------------------------------------------------------

class TestChildrenConflict:
    def test_has_children_false_with_existing_names_creates_conflict(self):
        """Setting has_children=False when names are already in state → conflict."""
        state = state_with_children_names()
        updates = [make_update(FieldPath.HAS_CHILDREN, False)]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is True
        assert FieldPath.HAS_CHILDREN in [u.path for u in result.held_updates]
        assert "no children" in result.description.lower()

    def test_children_names_proposed_while_has_children_false_creates_conflict(self):
        """Proposing names when state says has_children=False → conflict."""
        state = state_with_no_children()
        updates = [make_update(FieldPath.CHILDREN_NAMES, ["Charlie"])]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is True
        assert FieldPath.CHILDREN_NAMES in [u.path for u in result.held_updates]

    def test_safe_updates_not_held(self):
        """Other updates in the same batch should be safe to apply."""
        state = state_with_children_names()
        updates = [
            make_update(FieldPath.HAS_CHILDREN, False),
            make_update(FieldPath.FULL_NAME, "Jane"),
        ]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is True
        safe_paths = [u.path for u in result.safe_updates]
        assert FieldPath.FULL_NAME in safe_paths
        assert FieldPath.HAS_CHILDREN not in safe_paths


# ---------------------------------------------------------------------------
# Rule 2: same field, two different values in one batch
# ---------------------------------------------------------------------------

class TestDuplicateFieldConflict:
    def test_same_field_different_values_conflict(self):
        state = WishesState()
        updates = [
            make_update(FieldPath.FULL_NAME, "Jane Smith"),
            make_update(FieldPath.FULL_NAME, "Jane Doe"),
        ]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is True
        assert "full_name" in result.description

    def test_same_field_same_value_no_conflict(self):
        """Duplicate path with identical value is not a conflict."""
        state = WishesState()
        updates = [
            make_update(FieldPath.FULL_NAME, "Jane Smith"),
            make_update(FieldPath.FULL_NAME, "Jane Smith"),
        ]
        result = detect_conflicts(state, updates)
        assert result.has_conflict is False


# ---------------------------------------------------------------------------
# Resolution: accept_proposed
# ---------------------------------------------------------------------------

class TestAcceptProposed:
    def test_accept_applies_held_updates(self):
        held = [make_update(FieldPath.HAS_CHILDREN, False)]
        state = state_with_children_names()
        to_apply, msg = resolve_conflict(held, "accept_proposed", state)
        paths = [u.path for u in to_apply]
        assert FieldPath.HAS_CHILDREN in paths

    def test_accept_has_children_false_clears_children_names(self):
        """Accepting has_children=False must add a sentinel to clear children_names."""
        held = [make_update(FieldPath.HAS_CHILDREN, False)]
        state = state_with_children_names()
        to_apply, msg = resolve_conflict(held, "accept_proposed", state)
        paths = [u.path for u in to_apply]
        assert FieldPath.CHILDREN_NAMES in paths
        # The children_names update should have value=None (clear sentinel)
        cn_update = next(u for u in to_apply if u.path == FieldPath.CHILDREN_NAMES)
        assert cn_update.value is None

    def test_accept_non_children_conflict(self):
        """Accepting a duplicate-field conflict just returns the held updates."""
        held = [make_update(FieldPath.FULL_NAME, "Jane Doe")]
        state = WishesState()
        to_apply, msg = resolve_conflict(held, "accept_proposed", state)
        assert len(to_apply) == 1
        assert to_apply[0].path == FieldPath.FULL_NAME


# ---------------------------------------------------------------------------
# Resolution: keep_existing
# ---------------------------------------------------------------------------

class TestKeepExisting:
    def test_keep_existing_returns_empty(self):
        held = [make_update(FieldPath.HAS_CHILDREN, False)]
        state = state_with_children_names()
        to_apply, msg = resolve_conflict(held, "keep_existing", state)
        assert to_apply == []
        assert "existing" in msg.lower()

    def test_invalid_resolution_raises(self):
        with pytest.raises(ValueError):
            resolve_conflict([], "invalid_resolution", WishesState())
