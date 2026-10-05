"""
Offline mock LLM client for development and demo without an API key.

This is NOT a replacement for the real LLM in production. It uses simple
regex heuristics to demonstrate that the app works end-to-end without
making any network calls. It covers the most common patterns in the
conversation flow but will not handle complex or ambiguous phrasings.

Labelled clearly as a stand-in so reviewers understand its purpose.
"""

from __future__ import annotations

import re
from typing import Optional

from app.llm.base import (
    AmbiguitySchema,
    ExtractionRequest,
    ExtractionResponse,
    ProposedUpdateSchema,
)
from app.domain.state import FieldPath


# ---------------------------------------------------------------------------
# Heuristic extraction rules
# ---------------------------------------------------------------------------

def _try_yes_no(
    text: str, focus_field: Optional[str]
) -> Optional[ProposedUpdateSchema]:
    """Resolve a bare 'yes' or 'no' against the focus field."""
    stripped = text.strip().lower().rstrip(".,!")
    if stripped not in ("yes", "no", "yeah", "nope", "nah", "yep"):
        return None
    if focus_field is None:
        return None

    bool_fields = {
        FieldPath.COVERS_WORLDWIDE_ASSETS.value,
        FieldPath.HAS_CHILDREN.value,
    }
    if focus_field not in bool_fields:
        return None

    value = stripped in ("yes", "yeah", "yep")
    return ProposedUpdateSchema(
        field=focus_field,
        value_bool=value,
        confidence="high",
        evidence=text.strip(),
    )


def _try_none_answer(
    text: str, focus_field: Optional[str]
) -> Optional[ProposedUpdateSchema]:
    """Handle 'none' / 'no gifts' / 'nothing' for list or text fields."""
    stripped = text.strip().lower().rstrip(".,!")
    none_patterns = r"^(none|no gifts?|nothing(?: else)?|no additional wishes?|n/a)$"
    if not re.match(none_patterns, stripped):
        return None
    if focus_field is None:
        return None

    list_fields = {FieldPath.SPECIFIC_GIFTS.value, FieldPath.CHILDREN_NAMES.value}
    text_fields = {FieldPath.ADDITIONAL_WISHES.value}

    if focus_field in list_fields:
        return ProposedUpdateSchema(
            field=focus_field,
            value_list=[],
            confidence="high",
            evidence=text.strip(),
        )
    if focus_field in text_fields:
        return ProposedUpdateSchema(
            field=focus_field,
            value_text="",
            confidence="high",
            evidence=text.strip(),
        )
    return None


def _try_name_pattern(text: str) -> Optional[ProposedUpdateSchema]:
    """'my name is X' → full_name."""
    m = re.search(r"my name is ([A-Z][a-zA-Z\s\-']+)", text, re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        return ProposedUpdateSchema(
            field=FieldPath.FULL_NAME.value,
            value_text=name,
            confidence="high",
            evidence=m.group(0),
        )
    # Also catch "I am X" at start of sentence
    m2 = re.search(r"\bI(?:'m| am) ([A-Z][a-zA-Z\s\-']+?)(?:\.|,|$)", text, re.IGNORECASE)
    if m2:
        name = m2.group(1).strip()
        if len(name.split()) >= 2:  # require at least two words to avoid "I am fine"
            return ProposedUpdateSchema(
                field=FieldPath.FULL_NAME.value,
                value_text=name,
                confidence="high",
                evidence=m2.group(0).strip(),
            )
    return None


def _try_address_pattern(text: str) -> Optional[ProposedUpdateSchema]:
    """'I live at X' / 'my address is X' → home_address."""
    m = re.search(
        r"(?:I live at|my (?:home )?address is)\s+(.+?)(?:\.|$)",
        text, re.IGNORECASE
    )
    if m:
        addr = m.group(1).strip()
        return ProposedUpdateSchema(
            field=FieldPath.HOME_ADDRESS.value,
            value_text=addr,
            confidence="high",
            evidence=m.group(0).strip(),
        )
    return None


def _try_executor_pattern(text: str) -> list:
    """
    'my [relationship] [Name] is my executor' or 'I appoint [Name] as executor'
    → executor.name + executor.relationship.
    """
    updates = []

    # "my brother James" / "my sister Sarah Smith"
    m = re.search(
        r"my (brother|sister|friend|partner|spouse|wife|husband|solicitor|"
        r"daughter|son|mother|father|colleague|nephew|niece|uncle|aunt)\s+"
        r"([A-Z][a-zA-Z\s\-']+?)(?:\s+(?:is|as|will be|to be)\s+(?:my\s+)?executor|[.,]|$)",
        text, re.IGNORECASE,
    )
    if m:
        relationship = m.group(1).strip()
        name = m.group(2).strip()
        updates.append(ProposedUpdateSchema(
            field=FieldPath.EXECUTOR_NAME.value,
            value_text=name,
            confidence="high",
            evidence=m.group(0).strip(),
        ))
        updates.append(ProposedUpdateSchema(
            field=FieldPath.EXECUTOR_RELATIONSHIP.value,
            value_text=relationship,
            confidence="high",
            evidence=m.group(1),
        ))
        return updates

    # "appoint / name X as executor"
    m2 = re.search(
        r"(?:appoint|name)\s+([A-Z][a-zA-Z\s\-']+?)\s+as\s+(?:my\s+)?executor",
        text, re.IGNORECASE,
    )
    if m2:
        name = m2.group(1).strip()
        updates.append(ProposedUpdateSchema(
            field=FieldPath.EXECUTOR_NAME.value,
            value_text=name,
            confidence="high",
            evidence=m2.group(0).strip(),
        ))
    return updates


def _try_children_names(text: str) -> Optional[ProposedUpdateSchema]:
    """
    Comma-separated list of names after 'children are' / 'their names are'.
    """
    m = re.search(
        r"(?:(?:my )?children(?:'s names?)? (?:are|:)|their names? (?:are|:))\s*"
        r"([A-Za-z][A-Za-z\s,and\-']+)",
        text, re.IGNORECASE,
    )
    if m:
        raw = m.group(1)
        # Split on commas and "and"
        names = [n.strip() for n in re.split(r",|\band\b", raw) if n.strip()]
        if names:
            return ProposedUpdateSchema(
                field=FieldPath.CHILDREN_NAMES.value,
                value_list=names,
                confidence="high",
                evidence=m.group(0).strip(),
            )
    return None


def _try_gifts(text: str) -> Optional[ProposedUpdateSchema]:
    """
    Simple gift extraction: 'I want to leave / give X to Y'
    or a comma list after 'gifts:'.
    """
    # List-style: "my gifts are: X, Y"
    m = re.search(r"(?:gifts?|bequests?)\s*(?:are|:)\s*(.+?)(?:\.|$)", text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        items = [i.strip() for i in re.split(r",|;", raw) if i.strip()]
        if items:
            return ProposedUpdateSchema(
                field=FieldPath.SPECIFIC_GIFTS.value,
                value_list=items,
                confidence="high",
                evidence=m.group(0).strip(),
            )

    # Single item: "leave my watch to Tom"
    m2 = re.search(
        r"(?:leave|give|bequeath)\s+(?:my\s+)?(.+?)\s+to\s+([A-Z][a-zA-Z\s]+)",
        text, re.IGNORECASE,
    )
    if m2:
        gift = f"{m2.group(1).strip()} to {m2.group(2).strip()}"
        return ProposedUpdateSchema(
            field=FieldPath.SPECIFIC_GIFTS.value,
            value_list=[gift],
            confidence="high",
            evidence=m2.group(0).strip(),
        )
    return None


def _try_additional_wishes(text: str) -> Optional[ProposedUpdateSchema]:
    """'my additional wishes are: X' or 'I also want ...'"""
    m = re.search(
        r"(?:additional wishes?|also wish|also want)\s*(?:are|:)?\s*(.+?)(?:\.|$)",
        text, re.IGNORECASE,
    )
    if m:
        wish = m.group(1).strip()
        if wish:
            return ProposedUpdateSchema(
                field=FieldPath.ADDITIONAL_WISHES.value,
                value_text=wish,
                confidence="high",
                evidence=m.group(0).strip(),
            )
    return None


def _try_worldwide(text: str, focus_field: Optional[str]) -> Optional[ProposedUpdateSchema]:
    """Detect yes/no for worldwide assets."""
    stripped = text.strip().lower().rstrip(".,!")

    # Explicit mention
    if re.search(r"worldwide|global|international|all assets", text, re.IGNORECASE):
        return ProposedUpdateSchema(
            field=FieldPath.COVERS_WORLDWIDE_ASSETS.value,
            value_bool=True,
            confidence="high",
            evidence=re.search(
                r"worldwide|global|international|all assets", text, re.IGNORECASE
            ).group(0),
        )
    if re.search(r"country only|domestic|local assets?|not worldwide", text, re.IGNORECASE):
        return ProposedUpdateSchema(
            field=FieldPath.COVERS_WORLDWIDE_ASSETS.value,
            value_bool=False,
            confidence="high",
            evidence=re.search(
                r"country only|domestic|local assets?|not worldwide", text, re.IGNORECASE
            ).group(0),
        )
    return None


def _detect_intent(text: str) -> str:
    """Rough intent classification."""
    low = text.lower()
    if re.search(r"\b(confirm|confirmed|that(?:'s| is) correct|looks? (?:good|right|correct))\b", low):
        return "confirm"
    if re.search(r"\b(actually|correction|no,?\s+it(?:'s| is)|change|update|wrong)\b", low):
        return "correction"
    return "answer"


# ---------------------------------------------------------------------------
# MockLLMClient
# ---------------------------------------------------------------------------

class MockLLMClient:
    """
    Stand-in LLM client using heuristic regex rules.

    Fully functional for demo purposes without a Gemini API key.
    Does not cover all edge cases — use GeminiClient for production.
    """

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        text = request.user_message
        focus = request.focus_field
        updates: list = []
        ambiguities: list = []

        # --- Resolve bare yes/no ---
        yn = _try_yes_no(text, focus)
        if yn:
            return ExtractionResponse(
                intent="answer",
                updates=[yn],
                acknowledgement="Got it.",
            )

        # --- Resolve 'none' answers ---
        none_upd = _try_none_answer(text, focus)
        if none_upd:
            return ExtractionResponse(
                intent="answer",
                updates=[none_upd],
                acknowledgement="Noted.",
            )

        # --- Detect intent ---
        intent = _detect_intent(text)

        # --- Run all extractors ---
        name_upd = _try_name_pattern(text)
        if name_upd:
            updates.append(name_upd)

        addr_upd = _try_address_pattern(text)
        if addr_upd:
            updates.append(addr_upd)

        executor_updates = _try_executor_pattern(text)
        updates.extend(executor_updates)

        children_upd = _try_children_names(text)
        if children_upd:
            updates.append(children_upd)

        gifts_upd = _try_gifts(text)
        if gifts_upd:
            updates.append(gifts_upd)

        wishes_upd = _try_additional_wishes(text)
        if wishes_upd:
            updates.append(wishes_upd)

        worldwide_upd = _try_worldwide(text, focus)
        if worldwide_upd:
            updates.append(worldwide_upd)

        # --- Conflict resolution ---
        conflict_resolution = None
        if request.pending_conflict:
            low = text.lower()
            if re.search(r"\b(yes|accept|go ahead|clear|ok|okay|correct)\b", low):
                conflict_resolution = "accept_proposed"
            elif re.search(r"\b(no|keep|don'?t change|leave|cancel)\b", low):
                conflict_resolution = "keep_existing"

        # --- Build acknowledgement ---
        if updates:
            ack = "Thanks, I've noted that."
        elif ambiguities:
            ack = "I need a bit more detail."
        else:
            ack = ""

        return ExtractionResponse(
            intent=intent,
            updates=updates,
            ambiguities=ambiguities,
            conflict_resolution=conflict_resolution,
            acknowledgement=ack,
        )
