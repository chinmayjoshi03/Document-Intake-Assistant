"""
Cross-field consistency rules (conflict detection and resolution).

Design rationale: conflicts are detected *after* individual update validation
but *before* any state mutation. The conflicting updates are held in the
session's pending_conflict rather than applied, and the assistant asks the
user to resolve the contradiction explicitly.

Two rules are implemented:
  1. has_children / children_names inconsistency
  2. Same field given two different values in a single message
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from app.domain.state import FieldPath, WishesState
from app.domain.updates import ValidatedUpdate


@dataclass
class ConflictResult:
    """Outcome of running conflict detection on a batch of validated updates."""
    has_conflict: bool
    description: str
    # Updates that caused the conflict — held until user resolves it
    held_updates: List[ValidatedUpdate] = field(default_factory=list)
    # Updates that are safe to apply immediately (not involved in the conflict)
    safe_updates: List[ValidatedUpdate] = field(default_factory=list)


def detect_conflicts(
    state: WishesState,
    proposed: List[ValidatedUpdate],
) -> ConflictResult:
    """
    Check proposed updates for cross-field and within-batch conflicts.

    Returns a ConflictResult.  If has_conflict is False, safe_updates == proposed.
    If has_conflict is True, held_updates contains conflicting items and
    safe_updates contains any non-conflicting items from the same batch.
    """
    # --- Rule 2: same field, two different values in one batch ---
    seen: dict = {}
    duplicate_paths: set = set()
    for upd in proposed:
        if upd.path in seen:
            if seen[upd.path] != upd.value:
                duplicate_paths.add(upd.path)
        else:
            seen[upd.path] = upd.value

    if duplicate_paths:
        path_names = ", ".join(p.value for p in duplicate_paths)
        conflicting = [u for u in proposed if u.path in duplicate_paths]
        safe = [u for u in proposed if u.path not in duplicate_paths]
        return ConflictResult(
            has_conflict=True,
            description=(
                f"You gave two different values for the same field(s) in one "
                f"message: {path_names}. Which value is correct?"
            ),
            held_updates=conflicting,
            safe_updates=safe,
        )

    # --- Rule 1: has_children / children_names inconsistency ---
    proposed_map = {u.path: u for u in proposed}

    # Collect effective values (proposed overrides existing state)
    def effective_has_children() -> Optional[bool]:
        if FieldPath.HAS_CHILDREN in proposed_map:
            return proposed_map[FieldPath.HAS_CHILDREN].value
        f = state.get(FieldPath.HAS_CHILDREN)
        return f.value  # may be None

    def effective_children_names() -> Optional[list]:
        if FieldPath.CHILDREN_NAMES in proposed_map:
            return proposed_map[FieldPath.CHILDREN_NAMES].value
        f = state.get(FieldPath.CHILDREN_NAMES)
        return f.value  # may be None

    hc = effective_has_children()
    cn = effective_children_names()

    # Case A: setting has_children=False while children names exist or are proposed
    if hc is False:
        existing_names = state.get(FieldPath.CHILDREN_NAMES).value
        has_existing_names = existing_names and len(existing_names) > 0
        proposing_names = (
            FieldPath.CHILDREN_NAMES in proposed_map
            and proposed_map[FieldPath.CHILDREN_NAMES].value
        )
        if has_existing_names or proposing_names:
            conflicting = [u for u in proposed if u.path in (
                FieldPath.HAS_CHILDREN, FieldPath.CHILDREN_NAMES
            )]
            safe = [u for u in proposed if u.path not in (
                FieldPath.HAS_CHILDREN, FieldPath.CHILDREN_NAMES
            )]
            existing_list = existing_names or proposed_map.get(
                FieldPath.CHILDREN_NAMES, ValidatedUpdate(
                    path=FieldPath.CHILDREN_NAMES, value=[], confidence="high"
                )
            ).value
            return ConflictResult(
                has_conflict=True,
                description=(
                    f"You said you have no children, but children's names are "
                    f"recorded ({existing_list}). "
                    f"Should I clear the children's names and mark 'no children'?"
                ),
                held_updates=conflicting,
                safe_updates=safe,
            )

    # Case B: proposing children_names while has_children is False
    if cn is not None and FieldPath.CHILDREN_NAMES in proposed_map:
        current_hc = state.get(FieldPath.HAS_CHILDREN).value
        if current_hc is False:
            conflicting = [u for u in proposed if u.path in (
                FieldPath.CHILDREN_NAMES,
            )]
            safe = [u for u in proposed if u.path != FieldPath.CHILDREN_NAMES]
            return ConflictResult(
                has_conflict=True,
                description=(
                    "You provided children's names but previously said you have "
                    "no children. Do you have children after all?"
                ),
                held_updates=conflicting,
                safe_updates=safe,
            )

    return ConflictResult(
        has_conflict=False,
        description="",
        held_updates=[],
        safe_updates=proposed,
    )


def resolve_conflict(
    held_updates: List[ValidatedUpdate],
    resolution: str,
    state: WishesState,
) -> Tuple[List[ValidatedUpdate], str]:
    """
    Apply conflict resolution chosen by the user.

    resolution: "accept_proposed" | "keep_existing"

    Returns (updates_to_apply, human_readable_summary).
    When accept_proposed and has_children=False is in held:
      - apply it and clear children_names.
    When keep_existing:
      - discard held updates, return empty list.
    """
    if resolution == "keep_existing":
        return [], "Keeping the existing values — no changes made."

    if resolution == "accept_proposed":
        updates_to_apply = list(held_updates)
        summary_parts = []

        # If has_children=False is being accepted, ensure children_names is cleared
        has_children_update = next(
            (u for u in updates_to_apply if u.path == FieldPath.HAS_CHILDREN and u.value is False),
            None,
        )
        if has_children_update:
            # Synthesise a clear for children_names
            from app.domain.updates import ValidatedUpdate as VU
            clear_update = VU(
                path=FieldPath.CHILDREN_NAMES,
                value=None,   # sentinel: means clear
                confidence="high",
            )
            # Replace any existing children_names update with the clear sentinel
            updates_to_apply = [
                u for u in updates_to_apply if u.path != FieldPath.CHILDREN_NAMES
            ]
            updates_to_apply.append(clear_update)
            summary_parts.append("children's names cleared")

        return updates_to_apply, "Accepted the proposed values. " + "; ".join(summary_parts)

    raise ValueError(f"Unknown resolution: {resolution!r}")
