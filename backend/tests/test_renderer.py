"""
Tests for documents/renderer.py — the deterministic document renderer.

Verifies: disclaimer present top and bottom, [Not yet provided] placeholders,
children branches, empty gifts/wishes, full state, deterministic output.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.documents.renderer import render_document, _DISCLAIMER, _UNKNOWN
from app.domain.state import FieldPath, FieldStatus, WishesState


FIXED_DATE = date(2026, 9, 15)


def make_full_state(has_children: bool = False) -> WishesState:
    s = WishesState()
    s.set(FieldPath.FULL_NAME, "Jane Smith")
    s.set(FieldPath.HOME_ADDRESS, "1 High Street, London, EC1A 1BB")
    s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
    s.set(FieldPath.HAS_CHILDREN, has_children)
    if has_children:
        s.set(FieldPath.CHILDREN_NAMES, ["Alice Smith", "Bob Smith"])
    s.set(FieldPath.EXECUTOR_NAME, "James Smith")
    s.set(FieldPath.EXECUTOR_RELATIONSHIP, "brother")
    s.set(FieldPath.SPECIFIC_GIFTS, ["Gold watch to nephew Tom"])
    s.set(FieldPath.ADDITIONAL_WISHES, "Please donate to the Red Cross.")
    return s


# ---------------------------------------------------------------------------
# Disclaimer
# ---------------------------------------------------------------------------

class TestDisclaimer:
    def test_disclaimer_at_top(self):
        doc = render_document(WishesState(), FIXED_DATE)
        # Should appear near the start
        assert _DISCLAIMER in doc[:500]

    def test_disclaimer_at_bottom(self):
        doc = render_document(WishesState(), FIXED_DATE)
        # Should also appear near the end
        assert _DISCLAIMER in doc[-500:]

    def test_disclaimer_appears_twice(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert doc.count(_DISCLAIMER) >= 2

    def test_not_legal_advice_text_present(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert "NOT LEGAL ADVICE" in doc
        assert "FICTIONAL DOCUMENT" in doc


# ---------------------------------------------------------------------------
# Unknown / empty state
# ---------------------------------------------------------------------------

class TestUnknownState:
    def test_all_unknown_renders_placeholders(self):
        doc = render_document(WishesState(), FIXED_DATE)
        # Every section that can be unknown should have the placeholder
        assert _UNKNOWN in doc

    def test_full_name_unknown(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert f"**Full name:** {_UNKNOWN}" in doc

    def test_home_address_unknown(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert f"**Home address:** {_UNKNOWN}" in doc

    def test_executor_unknown(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert _UNKNOWN in doc  # appears in executor section

    def test_no_invented_content(self):
        doc = render_document(WishesState(), FIXED_DATE)
        # Ensure no invented names or addresses appear
        assert "Jane" not in doc
        assert "James" not in doc


# ---------------------------------------------------------------------------
# Children branch
# ---------------------------------------------------------------------------

class TestChildrenBranch:
    def test_no_children(self):
        s = WishesState()
        s.set(FieldPath.HAS_CHILDREN, False)
        doc = render_document(s, FIXED_DATE)
        assert "I have no children" in doc
        # Should NOT show the names section
        assert "Names of children" not in doc

    def test_has_children_with_names(self):
        s = make_full_state(has_children=True)
        doc = render_document(s, FIXED_DATE)
        assert "I have children" in doc
        assert "Alice Smith" in doc
        assert "Bob Smith" in doc

    def test_has_children_unknown(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert _UNKNOWN in doc  # children section shows placeholder

    def test_has_children_names_unknown(self):
        s = WishesState()
        s.set(FieldPath.HAS_CHILDREN, True)
        doc = render_document(s, FIXED_DATE)
        assert "I have children" in doc
        assert _UNKNOWN in doc  # names not yet provided


# ---------------------------------------------------------------------------
# Gifts and wishes
# ---------------------------------------------------------------------------

class TestGiftsAndWishes:
    def test_empty_gifts_list(self):
        s = WishesState()
        s.set(FieldPath.SPECIFIC_GIFTS, [])
        doc = render_document(s, FIXED_DATE)
        assert "No specific gifts specified." in doc

    def test_gifts_with_items(self):
        s = make_full_state()
        doc = render_document(s, FIXED_DATE)
        assert "Gold watch to nephew Tom" in doc

    def test_empty_additional_wishes(self):
        s = WishesState()
        s.set(FieldPath.ADDITIONAL_WISHES, "")
        doc = render_document(s, FIXED_DATE)
        assert "None specified." in doc

    def test_additional_wishes_with_content(self):
        s = make_full_state()
        doc = render_document(s, FIXED_DATE)
        assert "Please donate to the Red Cross." in doc


# ---------------------------------------------------------------------------
# Scope of assets
# ---------------------------------------------------------------------------

class TestScopeOfAssets:
    def test_worldwide_true(self):
        s = WishesState()
        s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, True)
        doc = render_document(s, FIXED_DATE)
        assert "worldwide assets" in doc.lower()

    def test_worldwide_false(self):
        s = WishesState()
        s.set(FieldPath.COVERS_WORLDWIDE_ASSETS, False)
        doc = render_document(s, FIXED_DATE)
        assert "country of residence only" in doc.lower()

    def test_worldwide_unknown(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert _UNKNOWN in doc


# ---------------------------------------------------------------------------
# Full state rendering
# ---------------------------------------------------------------------------

class TestFullState:
    def test_full_state_no_placeholder(self):
        s = make_full_state(has_children=True)
        doc = render_document(s, FIXED_DATE)
        assert _UNKNOWN not in doc

    def test_full_state_has_all_names(self):
        s = make_full_state(has_children=True)
        doc = render_document(s, FIXED_DATE)
        assert "Jane Smith" in doc
        assert "James Smith" in doc
        assert "Alice Smith" in doc

    def test_date_in_output(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert "2026-09-15" in doc


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_state_same_output(self):
        s = make_full_state(has_children=True)
        doc1 = render_document(s, FIXED_DATE)
        doc2 = render_document(s, FIXED_DATE)
        assert doc1 == doc2

    def test_different_date_different_output(self):
        s = make_full_state()
        doc1 = render_document(s, date(2026, 1, 1))
        doc2 = render_document(s, date(2026, 12, 31))
        assert doc1 != doc2


# ---------------------------------------------------------------------------
# Draft status line
# ---------------------------------------------------------------------------

class TestDraftStatus:
    def test_empty_state_shows_fields_needed(self):
        doc = render_document(WishesState(), FIXED_DATE)
        assert "still needed" in doc

    def test_full_state_shows_awaiting_confirmation(self):
        s = make_full_state(has_children=False)
        doc = render_document(s, FIXED_DATE)
        assert "awaiting confirmation" in doc.lower() or "confirmed" in doc.lower()

    def test_confirmed_state_shows_all_confirmed(self):
        s = make_full_state(has_children=False)
        s.confirm_all_provided()
        doc = render_document(s, FIXED_DATE)
        assert "All fields confirmed" in doc
