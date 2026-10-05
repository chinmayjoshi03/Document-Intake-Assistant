"""
Tests for domain/questions.py — question selection logic.

Covers: canonical order, skipping provided/confirmed fields,
children_names conditional, complete state, greeting/summary/confirmed messages.
"""

from __future__ import annotations

import pytest

from app.domain.questions import (
    CANONICAL_ORDER,
    QUESTION_BANK,
    greeting_message,
    next_question,
    confirmed_message,
    summary_message,
)
from app.domain.state import FieldPath, FieldStatus, WishesState


def fill_up_to(state: WishesState, stop_before: FieldPath, has_children: bool = False) -> None:
    """Fill all fields in canonical order up to (but not including) stop_before."""
    for path in CANONICAL_ORDER:
        if path == stop_before:
            break
        if path == FieldPath.CHILDREN_NAMES:
            if has_children:
                state.set(path, ["Alice"])
            continue
        if path == FieldPath.HAS_CHILDREN:
            state.set(path, has_children)
            continue
        if path in (FieldPath.COVERS_WORLDWIDE_ASSETS,):
            state.set(path, True)
            continue
        if path in (FieldPath.SPECIFIC_GIFTS,):
            state.set(path, [])
            continue
        if path == FieldPath.ADDITIONAL_WISHES:
            state.set(path, "")
            continue
        state.set(path, f"value_for_{path.value}")


def make_complete_state(has_children: bool = False) -> WishesState:
    s = WishesState()
    s.set(FieldPath.FULL_NAME, "Jane Smith")
    s.set(FieldPath.HOME_ADDRESS, "1 High St")
    s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
    s.set(FieldPath.HAS_CHILDREN, has_children)
    if has_children:
        s.set(FieldPath.CHILDREN_NAMES, ["Alice"])
    s.set(FieldPath.EXECUTOR_NAME, "James")
    s.set(FieldPath.EXECUTOR_RELATIONSHIP, "brother")
    s.set(FieldPath.SPECIFIC_GIFTS, [])
    s.set(FieldPath.ADDITIONAL_WISHES, "")
    return s


# ---------------------------------------------------------------------------
# Canonical order
# ---------------------------------------------------------------------------

class TestCanonicalOrder:
    def test_first_question_is_full_name(self):
        path, _ = next_question(WishesState())
        assert path == FieldPath.FULL_NAME

    def test_second_question_is_home_address(self):
        s = WishesState()
        s.set(FieldPath.FULL_NAME, "Jane")
        path, _ = next_question(s)
        assert path == FieldPath.HOME_ADDRESS

    def test_follows_canonical_order(self):
        """Each successive fill should advance to the next canonical path."""
        s = WishesState()
        expected_order = [
            FieldPath.FULL_NAME,
            FieldPath.HOME_ADDRESS,
            FieldPath.COVERS_WORLDWIDE_ASSETS,
            FieldPath.HAS_CHILDREN,
            # CHILDREN_NAMES skipped because has_children will be False
            FieldPath.EXECUTOR_NAME,
            FieldPath.EXECUTOR_RELATIONSHIP,
            FieldPath.SPECIFIC_GIFTS,
            FieldPath.ADDITIONAL_WISHES,
        ]

        # Answer each question in order and assert the next one is correct
        for expected in expected_order:
            path, _ = next_question(s)
            assert path == expected, f"expected {expected}, got {path}"
            # Fill the field with a valid value
            if expected == FieldPath.COVERS_WORLDWIDE_ASSETS:
                s.set(expected, True)
            elif expected == FieldPath.HAS_CHILDREN:
                s.set(expected, False)
            elif expected == FieldPath.SPECIFIC_GIFTS:
                s.set(expected, [])
            elif expected == FieldPath.ADDITIONAL_WISHES:
                s.set(expected, "")
            else:
                s.set(expected, f"value_{expected.value}")


# ---------------------------------------------------------------------------
# Skipping filled fields
# ---------------------------------------------------------------------------

class TestSkipFilledFields:
    def test_skips_provided_field(self):
        s = WishesState()
        s.set(FieldPath.FULL_NAME, "Jane")
        path, _ = next_question(s)
        assert path != FieldPath.FULL_NAME

    def test_skips_confirmed_field(self):
        s = WishesState()
        s.set(FieldPath.FULL_NAME, "Jane", FieldStatus.CONFIRMED)
        path, _ = next_question(s)
        assert path != FieldPath.FULL_NAME

    def test_skips_multiple_provided_fields(self):
        s = WishesState()
        s.set(FieldPath.FULL_NAME, "Jane")
        s.set(FieldPath.HOME_ADDRESS, "1 High St")
        path, _ = next_question(s)
        assert path == FieldPath.COVERS_WORLDWIDE_ASSETS


# ---------------------------------------------------------------------------
# children_names conditional
# ---------------------------------------------------------------------------

class TestChildrenNamesConditional:
    def test_skips_children_names_when_has_children_false(self):
        s = WishesState()
        s.set(FieldPath.FULL_NAME, "Jane")
        s.set(FieldPath.HOME_ADDRESS, "1 High St")
        s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
        s.set(FieldPath.HAS_CHILDREN, False)
        path, _ = next_question(s)
        # Should jump straight to executor.name
        assert path == FieldPath.EXECUTOR_NAME

    def test_asks_children_names_when_has_children_true(self):
        s = WishesState()
        s.set(FieldPath.FULL_NAME, "Jane")
        s.set(FieldPath.HOME_ADDRESS, "1 High St")
        s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
        s.set(FieldPath.HAS_CHILDREN, True)
        path, _ = next_question(s)
        assert path == FieldPath.CHILDREN_NAMES

    def test_skips_children_names_when_has_children_unknown(self):
        """Don't ask for names before we know if there are children."""
        s = WishesState()
        # Fill everything except HAS_CHILDREN and CHILDREN_NAMES
        s.set(FieldPath.FULL_NAME, "Jane")
        s.set(FieldPath.HOME_ADDRESS, "1 High St")
        s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
        # has_children is UNKNOWN
        path, _ = next_question(s)
        assert path == FieldPath.HAS_CHILDREN  # asks has_children first

    def test_complete_with_has_children_true(self):
        s = make_complete_state(has_children=True)
        path, _ = next_question(s)
        assert path is None


# ---------------------------------------------------------------------------
# Complete state
# ---------------------------------------------------------------------------

class TestCompleteState:
    def test_returns_none_when_complete_no_children(self):
        s = make_complete_state(has_children=False)
        path, text = next_question(s)
        assert path is None
        assert text == ""

    def test_returns_none_when_complete_with_children(self):
        s = make_complete_state(has_children=True)
        path, text = next_question(s)
        assert path is None


# ---------------------------------------------------------------------------
# None answers (empty list / empty string)
# ---------------------------------------------------------------------------

class TestNoneAnswers:
    def test_empty_list_gifts_not_re_asked(self):
        s = WishesState()
        s.set(FieldPath.FULL_NAME, "Jane")
        s.set(FieldPath.HOME_ADDRESS, "1 High St")
        s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
        s.set(FieldPath.HAS_CHILDREN, False)
        s.set(FieldPath.EXECUTOR_NAME, "James")
        s.set(FieldPath.EXECUTOR_RELATIONSHIP, "brother")
        s.set(FieldPath.SPECIFIC_GIFTS, [])  # explicitly "none"
        path, _ = next_question(s)
        assert path == FieldPath.ADDITIONAL_WISHES  # moved on

    def test_empty_str_wishes_not_re_asked(self):
        s = make_complete_state()
        path, _ = next_question(s)
        assert path is None


# ---------------------------------------------------------------------------
# Question bank completeness
# ---------------------------------------------------------------------------

class TestQuestionBank:
    def test_all_paths_have_questions(self):
        for path in FieldPath:
            assert path in QUESTION_BANK, f"Missing question for {path}"

    def test_all_questions_non_empty(self):
        for path, q in QUESTION_BANK.items():
            assert q.strip(), f"Empty question for {path}"


# ---------------------------------------------------------------------------
# Greeting / summary / confirmed messages
# ---------------------------------------------------------------------------

class TestMessages:
    def test_greeting_contains_first_question(self):
        msg = greeting_message()
        first_q = QUESTION_BANK[FieldPath.FULL_NAME]
        assert first_q in msg

    def test_summary_contains_all_field_names(self):
        s = make_complete_state(has_children=True)
        msg = summary_message(s)
        assert "Full name" in msg
        assert "Home address" in msg
        assert "Executor" in msg
        assert "confirm" in msg.lower()

    def test_summary_shows_children_when_has_children_true(self):
        s = make_complete_state(has_children=True)
        msg = summary_message(s)
        assert "Alice" in msg

    def test_summary_hides_children_names_when_has_children_false(self):
        s = make_complete_state(has_children=False)
        msg = summary_message(s)
        assert "Children's names" not in msg

    def test_confirmed_message_non_empty(self):
        msg = confirmed_message()
        assert len(msg) > 10
