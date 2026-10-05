"""
Deterministic document renderer.

render_document(state, generated_on) -> str

Design rationale: the LLM never writes the document. This is a pure function
of the structured state. The same state always produces the same output,
making it trivially testable and immune to model hallucination.

The date is passed in so callers control the clock — no hidden datetime.now().
"""

from __future__ import annotations

from datetime import date
from typing import List

from app.domain.state import FieldPath, FieldStatus, WishesState

_DISCLAIMER = (
    "⚠️  FICTIONAL DOCUMENT. NOT LEGAL ADVICE. "
    "For demonstration purposes only."
)

_UNKNOWN = "[Not yet provided]"


def _val(state: WishesState, path: FieldPath, fallback: str = _UNKNOWN) -> str:
    """Return the string representation of a field value, or the fallback."""
    field = state.get(path)
    if field.status == FieldStatus.UNKNOWN or field.value is None:
        return fallback
    return str(field.value)


def _bool_val(state: WishesState, path: FieldPath) -> str:
    """Human-readable Yes / No / placeholder for boolean fields."""
    field = state.get(path)
    if field.status == FieldStatus.UNKNOWN or field.value is None:
        return _UNKNOWN
    return "Yes" if field.value else "No"


def _list_val(state: WishesState, path: FieldPath, empty_msg: str) -> str:
    """Render a list field; return empty_msg when the list is explicitly empty."""
    field = state.get(path)
    if field.status == FieldStatus.UNKNOWN or field.value is None:
        return _UNKNOWN
    items: List[str] = field.value  # type: ignore[assignment]
    if not items:
        return empty_msg
    return "\n".join(f"- {item}" for item in items)


def _draft_status(state: WishesState) -> str:
    """One-line summary of how complete the draft is."""
    from app.domain.state import FieldPath as FP, FieldStatus as FS

    applicable = [
        p for p in FP
        if not (p == FP.CHILDREN_NAMES and state.has_children.value is False)
    ]
    total = len(applicable)
    confirmed = sum(1 for p in applicable if state.get(p).status == FS.CONFIRMED)
    provided = sum(1 for p in applicable if state.get(p).status == FS.PROVIDED)
    filled = confirmed + provided

    if confirmed == total:
        completeness = "All fields confirmed."
    elif filled == total:
        completeness = "All fields provided — awaiting confirmation."
    else:
        missing = total - filled
        completeness = f"{missing} field(s) still needed."

    return f"Fields: {provided} provided, {confirmed} confirmed (of {total} applicable). {completeness}"


def render_document(state: WishesState, generated_on: date) -> str:
    """
    Render the Personal Wishes Document as Markdown.

    Pure function — no I/O, no randomness, no LLM calls.
    Unknown values render as '[Not yet provided]'.
    """
    lines: List[str] = []

    # --- Top disclaimer ---
    lines.append(f"> {_DISCLAIMER}")
    lines.append("")

    # --- Title ---
    lines.append("# Personal Wishes Document (Draft)")
    lines.append("")
    lines.append(f"*Generated on: {generated_on.isoformat()}*")
    lines.append("")

    # --- Draft status ---
    lines.append(f"**Draft status:** {_draft_status(state)}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 1: Personal Details ---
    lines.append("## 1. Personal Details")
    lines.append("")
    lines.append(f"**Full name:** {_val(state, FieldPath.FULL_NAME)}")
    lines.append("")
    lines.append(f"**Home address:** {_val(state, FieldPath.HOME_ADDRESS)}")
    lines.append("")

    # --- Section 2: Scope of Assets ---
    lines.append("## 2. Scope of Assets")
    lines.append("")
    worldwide = state.get(FieldPath.COVERS_WORLDWIDE_ASSETS)
    if worldwide.status == FieldStatus.UNKNOWN or worldwide.value is None:
        lines.append(f"This document covers worldwide assets: {_UNKNOWN}")
    elif worldwide.value:
        lines.append("This document covers **worldwide assets**.")
    else:
        lines.append("This document covers assets in the **country of residence only**.")
    lines.append("")

    # --- Section 3: Children ---
    lines.append("## 3. Children")
    lines.append("")
    has_children = state.get(FieldPath.HAS_CHILDREN)
    if has_children.status == FieldStatus.UNKNOWN or has_children.value is None:
        lines.append(f"Whether I have children: {_UNKNOWN}")
    elif not has_children.value:
        lines.append("I have no children.")
    else:
        lines.append("I have children.")
        lines.append("")
        lines.append("**Names of children:**")
        lines.append("")
        names_field = state.get(FieldPath.CHILDREN_NAMES)
        if names_field.status == FieldStatus.UNKNOWN or names_field.value is None:
            lines.append(_UNKNOWN)
        elif not names_field.value:
            lines.append("*(No names provided)*")
        else:
            for name in names_field.value:  # type: ignore[union-attr]
                lines.append(f"- {name}")
    lines.append("")

    # --- Section 4: Executor ---
    lines.append("## 4. Executor")
    lines.append("")
    lines.append(
        f"I appoint **{_val(state, FieldPath.EXECUTOR_NAME)}** "
        f"(my {_val(state, FieldPath.EXECUTOR_RELATIONSHIP)}) as my executor."
    )
    lines.append("")

    # --- Section 5: Specific Gifts ---
    lines.append("## 5. Specific Gifts")
    lines.append("")
    gifts = state.get(FieldPath.SPECIFIC_GIFTS)
    if gifts.status == FieldStatus.UNKNOWN or gifts.value is None:
        lines.append(_UNKNOWN)
    elif not gifts.value:
        lines.append("No specific gifts specified.")
    else:
        for gift in gifts.value:  # type: ignore[union-attr]
            lines.append(f"- {gift}")
    lines.append("")

    # --- Section 6: Additional Wishes ---
    lines.append("## 6. Additional Wishes")
    lines.append("")
    wishes = state.get(FieldPath.ADDITIONAL_WISHES)
    if wishes.status == FieldStatus.UNKNOWN or wishes.value is None:
        lines.append(_UNKNOWN)
    elif wishes.value == "":
        lines.append("None specified.")
    else:
        lines.append(str(wishes.value))
    lines.append("")

    # --- Footer disclaimer ---
    lines.append("---")
    lines.append("")
    lines.append(f"> {_DISCLAIMER}")
    lines.append("")
    lines.append(
        "*This document has no legal effect. It is a draft produced by an "
        "automated intake tool for illustration purposes only. Consult a "
        "qualified legal professional for any estate planning needs.*"
    )

    return "\n".join(lines)
