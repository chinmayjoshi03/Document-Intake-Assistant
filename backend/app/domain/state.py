"""
Core domain model for the Personal Wishes Document intake.

Design rationale: every leaf field carries an explicit status so that
"not asked yet", "user said it", and "user confirmed it" are never conflated.
An empty list/string with status PROVIDED means the user explicitly said none;
value=None with UNKNOWN means we haven't asked or received an answer.

The structured state — not conversation history — is the single source of truth.
"""

from __future__ import annotations

from enum import Enum
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------

class FieldStatus(str, Enum):
    UNKNOWN = "unknown"      # nothing captured, value is None
    PROVIDED = "provided"    # user stated it, not yet confirmed at review step
    CONFIRMED = "confirmed"  # user confirmed at the review step


# ---------------------------------------------------------------------------
# Generic typed field wrapper
# ---------------------------------------------------------------------------

T = TypeVar("T")


class Field_(BaseModel, Generic[T]):
    """
    Wraps any leaf value with an explicit status.

    Using Field_ (trailing underscore) to avoid shadowing pydantic.Field.
    """

    value: Optional[T] = None
    status: FieldStatus = FieldStatus.UNKNOWN

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Nested executor model
# ---------------------------------------------------------------------------

class ExecutorState(BaseModel):
    """Executor sub-document: name and relationship to the user."""

    name: Field_[str] = Field(default_factory=Field_)
    relationship: Field_[str] = Field(default_factory=Field_)


# ---------------------------------------------------------------------------
# FieldPath enum — canonical dotted paths, exactly nine
# ---------------------------------------------------------------------------

class FieldPath(str, Enum):
    FULL_NAME = "full_name"
    HOME_ADDRESS = "home_address"
    COVERS_WORLDWIDE_ASSETS = "covers_worldwide_assets"
    HAS_CHILDREN = "has_children"
    CHILDREN_NAMES = "children_names"
    EXECUTOR_NAME = "executor.name"
    EXECUTOR_RELATIONSHIP = "executor.relationship"
    SPECIFIC_GIFTS = "specific_gifts"
    ADDITIONAL_WISHES = "additional_wishes"


# ---------------------------------------------------------------------------
# Main state model
# ---------------------------------------------------------------------------

class WishesState(BaseModel):
    """
    All nine fields of the Personal Wishes Document.

    Helpers provide uniform get/set/clear access by FieldPath, so callers
    never need to know the internal nesting structure.
    """

    full_name: Field_[str] = Field(default_factory=Field_)
    home_address: Field_[str] = Field(default_factory=Field_)
    covers_worldwide_assets: Field_[bool] = Field(default_factory=Field_)
    has_children: Field_[bool] = Field(default_factory=Field_)
    children_names: Field_[List[str]] = Field(default_factory=Field_)
    executor: ExecutorState = Field(default_factory=ExecutorState)
    specific_gifts: Field_[List[str]] = Field(default_factory=Field_)
    additional_wishes: Field_[str] = Field(default_factory=Field_)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get(self, path: FieldPath) -> Field_:  # type: ignore[type-arg]
        """Return the Field_ wrapper for the given path."""
        if path == FieldPath.FULL_NAME:
            return self.full_name
        if path == FieldPath.HOME_ADDRESS:
            return self.home_address
        if path == FieldPath.COVERS_WORLDWIDE_ASSETS:
            return self.covers_worldwide_assets
        if path == FieldPath.HAS_CHILDREN:
            return self.has_children
        if path == FieldPath.CHILDREN_NAMES:
            return self.children_names
        if path == FieldPath.EXECUTOR_NAME:
            return self.executor.name
        if path == FieldPath.EXECUTOR_RELATIONSHIP:
            return self.executor.relationship
        if path == FieldPath.SPECIFIC_GIFTS:
            return self.specific_gifts
        if path == FieldPath.ADDITIONAL_WISHES:
            return self.additional_wishes
        raise ValueError(f"Unknown FieldPath: {path}")

    def set(self, path: FieldPath, value: object, status: FieldStatus = FieldStatus.PROVIDED) -> None:
        """
        Set a field's value and status.

        Correcting a CONFIRMED field resets it to PROVIDED — the user
        must re-confirm at the review step.
        """
        field = self.get(path)
        if field.status == FieldStatus.CONFIRMED and status == FieldStatus.PROVIDED:
            # Correction: drop back to provided
            pass
        field.value = value  # type: ignore[assignment]
        field.status = status

    def clear(self, path: FieldPath) -> None:
        """Reset a field to UNKNOWN / None."""
        field = self.get(path)
        field.value = None
        field.status = FieldStatus.UNKNOWN

    # ------------------------------------------------------------------
    # Completeness helpers
    # ------------------------------------------------------------------

    def missing_paths(self) -> List[FieldPath]:
        """
        Return paths that still need an answer, in canonical order.

        children_names is only required when has_children is True.
        When has_children is False it is "not applicable" and not missing.
        """
        required = list(FieldPath)
        result = []
        for path in required:
            if path == FieldPath.CHILDREN_NAMES:
                # Skip if has_children is explicitly False
                if self.has_children.value is False:
                    continue
                # Skip if has_children is not yet answered (not missing yet)
                if self.has_children.status == FieldStatus.UNKNOWN:
                    continue
            field = self.get(path)
            if field.status == FieldStatus.UNKNOWN:
                result.append(path)
        return result

    def is_complete(self) -> bool:
        """True when every applicable field has been provided or confirmed."""
        return len(self.missing_paths()) == 0

    def all_confirmed(self) -> bool:
        """True when every applicable field is CONFIRMED."""
        for path in FieldPath:
            if path == FieldPath.CHILDREN_NAMES and self.has_children.value is False:
                continue
            if self.get(path).status != FieldStatus.CONFIRMED:
                return False
        return True

    def confirm_all_provided(self) -> None:
        """
        Mark every PROVIDED field as CONFIRMED.

        Called when the user confirms the complete summary.
        """
        for path in FieldPath:
            if path == FieldPath.CHILDREN_NAMES and self.has_children.value is False:
                continue
            field = self.get(path)
            if field.status == FieldStatus.PROVIDED:
                field.status = FieldStatus.CONFIRMED
