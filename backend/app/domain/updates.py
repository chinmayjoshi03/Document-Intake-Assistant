"""
Validation of proposed updates from the LLM before they touch state.

Design rationale: the LLM is an untrusted proposer. Every update goes through
this module before application. One bad update must not block the valid ones —
each is validated independently and the results are partitioned into
(valid, rejected). Callers decide what to do with rejections (log as warnings).

The evidence check is a cheap but effective defence against hallucination:
if the LLM claims the user said "James" but the word "James" doesn't appear
in the user's message (case-insensitive), we reject that update.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from app.domain.state import FieldPath, FieldStatus, WishesState

# Fields whose type is bool
_BOOL_FIELDS = {
    FieldPath.COVERS_WORLDWIDE_ASSETS,
    FieldPath.HAS_CHILDREN,
}

# Fields whose type is list[str]
_LIST_FIELDS = {
    FieldPath.CHILDREN_NAMES,
    FieldPath.SPECIFIC_GIFTS,
}

# Fields whose type is str (including additional_wishes which may be empty)
_STR_FIELDS = {
    FieldPath.FULL_NAME,
    FieldPath.HOME_ADDRESS,
    FieldPath.EXECUTOR_NAME,
    FieldPath.EXECUTOR_RELATIONSHIP,
    FieldPath.ADDITIONAL_WISHES,
}

# Fields where an empty string is a valid "none" answer
_ALLOWS_EMPTY_STR = {FieldPath.ADDITIONAL_WISHES}


@dataclass
class ProposedUpdate:
    """
    A raw update from the LLM, before validation.
    Exactly one of value_text / value_bool / value_list should be set.
    """
    field: str
    value_text: Optional[str] = None
    value_bool: Optional[bool] = None
    value_list: Optional[List[str]] = None
    confidence: str = "high"   # "high" | "low"
    evidence: str = ""


@dataclass
class ValidatedUpdate:
    """A proposal that passed all checks and is ready to apply to state."""
    path: FieldPath
    value: Any
    confidence: str


@dataclass
class RejectedUpdate:
    """A proposal that failed one or more checks."""
    raw: ProposedUpdate
    reason: str


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------

def _resolve_field_path(field_str: str) -> Optional[FieldPath]:
    """Return the matching FieldPath or None if field_str is not a valid path."""
    try:
        return FieldPath(field_str)
    except ValueError:
        return None


def _check_value_slots(update: ProposedUpdate) -> Optional[str]:
    """Exactly one value slot must be set."""
    set_slots = sum([
        update.value_text is not None,
        update.value_bool is not None,
        update.value_list is not None,
    ])
    if set_slots == 0:
        return "no value slot is set"
    if set_slots > 1:
        return f"{set_slots} value slots are set; exactly one required"
    return None


def _check_type_matches(
    path: FieldPath,
    update: ProposedUpdate,
) -> Optional[str]:
    """The set slot must match the field's expected type."""
    if path in _BOOL_FIELDS:
        if update.value_bool is None:
            return f"field '{path.value}' expects a bool but value_bool is not set"
    elif path in _LIST_FIELDS:
        if update.value_list is None:
            return f"field '{path.value}' expects a list but value_list is not set"
    elif path in _STR_FIELDS:
        if update.value_text is None:
            return f"field '{path.value}' expects a string but value_text is not set"
    return None


def _check_non_empty(
    path: FieldPath,
    update: ProposedUpdate,
) -> Optional[str]:
    """String values must be non-empty (trimmed), except additional_wishes."""
    if path in _STR_FIELDS and update.value_text is not None:
        trimmed = update.value_text.strip()
        if not trimmed and path not in _ALLOWS_EMPTY_STR:
            return f"field '{path.value}' string value is empty after trimming"
    return None


def _check_evidence(
    update: ProposedUpdate,
    user_message: str,
    focus_field: Optional[FieldPath],
) -> Optional[str]:
    """
    Evidence must be a case-insensitive substring of the user message.

    Exception: bare "yes"/"no"/"none" answers are allowed when the evidence
    equals the whole user message (short replies for boolean/empty fields).
    Also skip the check for empty-string additional_wishes where evidence
    may reasonably be the whole short reply.
    """
    evidence = update.evidence.strip()
    msg_lower = user_message.lower()

    if not evidence:
        # No evidence at all is treated as low-confidence by callers,
        # but we don't reject here — that's the confidence check's job.
        return None

    if evidence.lower() in msg_lower:
        return None  # substring found — all good

    # Allow evidence == full user message (bare "yes" / "no" / "none")
    if evidence.lower() == user_message.strip().lower():
        return None

    return (
        f"evidence '{evidence}' is not a substring of the user message "
        f"(possible hallucination)"
    )


def _get_value(path: FieldPath, update: ProposedUpdate) -> Any:
    """Extract and normalise the validated value."""
    if path in _BOOL_FIELDS:
        return update.value_bool
    if path in _LIST_FIELDS:
        return update.value_list
    # str field — trim it
    assert update.value_text is not None
    return update.value_text.strip()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_update(
    update: ProposedUpdate,
    user_message: str,
    focus_field: Optional[FieldPath],
    skip_evidence_check: bool = False,
) -> Tuple[Optional[ValidatedUpdate], Optional[RejectedUpdate]]:
    """
    Validate a single proposed update.

    Returns (ValidatedUpdate, None) on success or (None, RejectedUpdate) on
    failure. Low-confidence updates are returned as RejectedUpdate so they
    surface as ambiguities rather than state changes.

    skip_evidence_check: set True for direct UI edits (PATCH /state).
    """
    # 1. Field path must be valid
    path = _resolve_field_path(update.field)
    if path is None:
        return None, RejectedUpdate(
            raw=update,
            reason=f"unknown field path '{update.field}'",
        )

    # 2. Exactly one value slot
    slot_error = _check_value_slots(update)
    if slot_error:
        return None, RejectedUpdate(raw=update, reason=slot_error)

    # 3. Type must match the field
    type_error = _check_type_matches(path, update)
    if type_error:
        return None, RejectedUpdate(raw=update, reason=type_error)

    # 4. Non-empty string (except additional_wishes)
    empty_error = _check_non_empty(path, update)
    if empty_error:
        return None, RejectedUpdate(raw=update, reason=empty_error)

    # 5. Evidence check (LLM-sourced only)
    if not skip_evidence_check:
        evidence_error = _check_evidence(update, user_message, focus_field)
        if evidence_error:
            return None, RejectedUpdate(raw=update, reason=evidence_error)

    # 6. Low-confidence → treat as ambiguity (not applied)
    if update.confidence == "low":
        return None, RejectedUpdate(
            raw=update,
            reason="confidence is low — needs clarification before applying",
        )

    value = _get_value(path, update)
    return ValidatedUpdate(path=path, value=value, confidence=update.confidence), None


def validate_updates(
    updates: List[ProposedUpdate],
    user_message: str,
    focus_field: Optional[FieldPath],
    skip_evidence_check: bool = False,
) -> Tuple[List[ValidatedUpdate], List[RejectedUpdate]]:
    """
    Validate a batch of proposed updates.

    Each is validated independently — one failure doesn't block the rest.
    """
    valid: List[ValidatedUpdate] = []
    rejected: List[RejectedUpdate] = []

    for upd in updates:
        v, r = validate_update(upd, user_message, focus_field, skip_evidence_check)
        if v:
            valid.append(v)
        if r:
            rejected.append(r)

    return valid, rejected
