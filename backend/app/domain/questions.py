"""
Question bank and next-question selection.

Design rationale: questions are chosen deterministically by code, never by the
LLM. This ensures a consistent interview flow and prevents the model from
deciding what to ask next (which would let it control the conversation path).

Canonical order mirrors the field order in the spec.
"""

from __future__ import annotations

from typing import Optional, Tuple

from app.domain.state import FieldPath, FieldStatus, WishesState

# ---------------------------------------------------------------------------
# Question bank — one question string per FieldPath
# ---------------------------------------------------------------------------

QUESTION_BANK: dict = {
    FieldPath.FULL_NAME: (
        "What is your full name?"
    ),
    FieldPath.HOME_ADDRESS: (
        "What is your home address? (Please include the full postal address.)"
    ),
    FieldPath.COVERS_WORLDWIDE_ASSETS: (
        "Should this document cover your worldwide assets, "
        "or only assets in your country of residence? (yes for worldwide / no for country only)"
    ),
    FieldPath.HAS_CHILDREN: (
        "Do you have any children? (yes / no)"
    ),
    FieldPath.CHILDREN_NAMES: (
        "What are the names of your children? "
        "(Please list them separated by commas.)"
    ),
    FieldPath.EXECUTOR_NAME: (
        "Who would you like to appoint as your executor? "
        "(Please give their full name.)"
    ),
    FieldPath.EXECUTOR_RELATIONSHIP: (
        "What is your executor's relationship to you? "
        "(e.g. brother, sister, friend, solicitor)"
    ),
    FieldPath.SPECIFIC_GIFTS: (
        "Would you like to leave any specific gifts to named individuals? "
        "If so, please describe them. If not, just say 'none'."
    ),
    FieldPath.ADDITIONAL_WISHES: (
        "Do you have any additional wishes you'd like to record? "
        "If not, just say 'none'."
    ),
}

# Canonical question order — must match the order defined in the spec
CANONICAL_ORDER: list = [
    FieldPath.FULL_NAME,
    FieldPath.HOME_ADDRESS,
    FieldPath.COVERS_WORLDWIDE_ASSETS,
    FieldPath.HAS_CHILDREN,
    FieldPath.CHILDREN_NAMES,
    FieldPath.EXECUTOR_NAME,
    FieldPath.EXECUTOR_RELATIONSHIP,
    FieldPath.SPECIFIC_GIFTS,
    FieldPath.ADDITIONAL_WISHES,
]


# ---------------------------------------------------------------------------
# Next-question logic
# ---------------------------------------------------------------------------

def next_question(state: WishesState) -> Tuple[Optional[FieldPath], str]:
    """
    Return (FieldPath, question_text) for the first field that still needs
    an answer, in canonical order.

    Rules:
    - Skip any field whose status is PROVIDED or CONFIRMED.
    - Skip CHILDREN_NAMES when has_children is False.
    - Skip CHILDREN_NAMES when has_children is still UNKNOWN
      (don't ask for names before we know if there are children).
    - Return (None, "") when everything is filled.
    """
    for path in CANONICAL_ORDER:
        if path == FieldPath.CHILDREN_NAMES:
            hc = state.get(FieldPath.HAS_CHILDREN)
            # Only ask for names when has_children is explicitly True
            if hc.value is not True:
                continue

        field = state.get(path)
        if field.status == FieldStatus.UNKNOWN:
            return path, QUESTION_BANK[path]

    return None, ""


def greeting_message() -> str:
    """
    Opening message sent when a session is created — no LLM call needed.
    The first question is always full_name (canonical order, empty state).
    """
    first_question = QUESTION_BANK[FieldPath.FULL_NAME]
    return (
        "Hello! I'll help you put together a Personal Wishes Document. "
        "I'll ask you a few questions — you can correct any answer at any time.\n\n"
        f"{first_question}"
    )


def summary_message(state: WishesState) -> str:
    """
    Summary shown when all fields are provided but not yet confirmed.
    Lists every field value so the user can spot errors before confirming.
    """
    from app.documents.renderer import _UNKNOWN

    def _fval(path: FieldPath) -> str:
        f = state.get(path)
        if f.status == FieldStatus.UNKNOWN or f.value is None:
            return _UNKNOWN
        if isinstance(f.value, bool):
            return "Yes" if f.value else "No"
        if isinstance(f.value, list):
            return ", ".join(f.value) if f.value else "None"
        return str(f.value)

    lines = [
        "Here's a summary of everything I've collected. "
        "Please check it carefully and let me know if anything needs changing. "
        "If everything looks correct, just say **'confirm'**.\n",
        f"- **Full name:** {_fval(FieldPath.FULL_NAME)}",
        f"- **Home address:** {_fval(FieldPath.HOME_ADDRESS)}",
        f"- **Worldwide assets:** {_fval(FieldPath.COVERS_WORLDWIDE_ASSETS)}",
        f"- **Has children:** {_fval(FieldPath.HAS_CHILDREN)}",
    ]

    if state.get(FieldPath.HAS_CHILDREN).value is True:
        lines.append(f"- **Children's names:** {_fval(FieldPath.CHILDREN_NAMES)}")

    lines += [
        f"- **Executor name:** {_fval(FieldPath.EXECUTOR_NAME)}",
        f"- **Executor relationship:** {_fval(FieldPath.EXECUTOR_RELATIONSHIP)}",
        f"- **Specific gifts:** {_fval(FieldPath.SPECIFIC_GIFTS)}",
        f"- **Additional wishes:** {_fval(FieldPath.ADDITIONAL_WISHES)}",
    ]

    return "\n".join(lines)


def confirmed_message() -> str:
    """Message shown once all fields are confirmed."""
    return (
        "Your Personal Wishes Document draft is ready — you can see it in the "
        "Draft tab on the right. You can still correct any field at any time "
        "by typing in the chat or editing the field directly."
    )
