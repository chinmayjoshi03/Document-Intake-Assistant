"""
Turn orchestrator — one user message in, one assistant reply out.

This is the heart of the "LLM proposes, code decides" architecture.
The pipeline follows the spec §9 exactly:

  1. Validate message (not empty, not too long)
  2. Build ExtractionRequest
  3. Call LLM  (on any LLMError: do not mutate state/history, return error)
  4. Validate each update (domain/updates.py)
  5. Conflict detection (domain/conflicts.py)
  6. Apply surviving updates to state + audit log
  7. If intent==confirm and complete and no conflict: mark all CONFIRMED
  8. Choose reply (code-driven, not LLM-driven)
  9. Append messages, return TurnResult

The LLM never writes the document and never decides what question comes next.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, List, Optional

from app.domain.session import AuditEntry, PendingConflict, Session, SessionStore
from app.domain.state import FieldPath, FieldStatus, WishesState
from app.domain.updates import (
    ProposedUpdate,
    ValidatedUpdate,
    validate_updates,
)
from app.domain.conflicts import detect_conflicts, resolve_conflict
from app.domain.questions import (
    confirmed_message,
    greeting_message,
    next_question,
    summary_message,
)
from app.documents.renderer import render_document
from app.llm.base import (
    ExtractionRequest,
    ExtractionResponse,
    LLMClient,
    LLMError,
)
from app.llm.prompts import build_system_prompt, build_user_prompt  # noqa: F401 (used in gemini)

MAX_MESSAGE_LENGTH = 2000


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class TurnResult:
    """Everything the API layer needs to build a TurnResponse."""

    session_id: str
    reply: str
    state: WishesState
    messages: list
    document_markdown: str
    missing_fields: List[str]
    is_complete: bool
    pending_conflict: Optional[PendingConflict]
    warnings: List[str]
    audit_log: list
    error: Optional[dict] = None   # set on LLMError paths


@dataclass
class DirectEditResult:
    """Result of a PATCH /state operation."""

    session_id: str
    state: WishesState
    messages: list
    document_markdown: str
    missing_fields: List[str]
    is_complete: bool
    pending_conflict: Optional[PendingConflict]
    warnings: List[str]
    audit_log: list


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _apply_update(
    session: Session,
    upd: ValidatedUpdate,
    source: str,
) -> None:
    """
    Apply one validated update to the session state and write to the audit log.

    A value of None is a sentinel meaning 'clear' (used when accepting
    has_children=False to clear children_names).
    """
    path = upd.path
    old_field = session.state.get(path)
    old_value = old_field.value

    if upd.value is None and path == FieldPath.CHILDREN_NAMES:
        # Clear sentinel from conflict resolution
        session.state.clear(path)
        new_value = None
    else:
        session.state.set(path, upd.value)
        new_value = upd.value

    session.log_change(path, old_value, new_value, source)  # type: ignore[arg-type]


def _build_reply(
    session: Session,
    response: ExtractionResponse,
    warnings: List[str],
) -> str:
    """
    Assemble the assistant reply from the LLM acknowledgement + code-chosen question.

    The LLM writes the acknowledgement sentence; code decides what comes next.
    """
    ack = (response.acknowledgement or "").strip()
    # Acknowledgement must not end with a question mark (it should be a statement)
    if ack.endswith("?"):
        ack = ""
    # For off-topic messages (greetings, chitchat, etc.) suppress the ack so the
    # assistant doesn't engage with the off-topic content — just re-ask the question.
    if response.intent == "other":
        ack = ""

    parts: List[str] = []
    if ack:
        parts.append(ack)

    # --- Conflict pending: ask user to resolve it ---
    if session.pending_conflict:
        parts.append(session.pending_conflict.description)
        return " ".join(parts)

    # --- Ambiguities: ask the first clarifying question ---
    if response.ambiguities:
        clarifier = response.ambiguities[0].clarifying_question[:300]
        parts.append(clarifier)
        return " ".join(parts)

    # --- Missing fields: ask the next one ---
    next_path, next_q = next_question(session.state)
    if next_q:
        session.focus_field = next_path.value if next_path else None
        parts.append(next_q)
        return " ".join(parts)

    # --- Complete but not confirmed: show summary ---
    if session.state.is_complete() and not session.state.all_confirmed():
        session.focus_field = None
        parts.append(summary_message(session.state))
        return " ".join(parts)

    # --- Fully confirmed ---
    session.focus_field = None
    parts.append(confirmed_message())
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def process_turn(
    session_id: str,
    user_message: str,
    store: SessionStore,
    llm: LLMClient,
) -> TurnResult:
    """
    Process one user message through the full turn pipeline.

    On any LLMError: state and history are left unchanged; error info is
    returned in TurnResult.error.
    """
    # 1. Basic message validation
    user_message = user_message.strip()
    if not user_message:
        return TurnResult(
            session_id=session_id,
            reply="",
            state=WishesState(),
            messages=[],
            document_markdown="",
            missing_fields=[],
            is_complete=False,
            pending_conflict=None,
            warnings=[],
            audit_log=[],
            error={"code": "validation_error", "message": "Message cannot be empty."},
        )
    if len(user_message) > MAX_MESSAGE_LENGTH:
        return TurnResult(
            session_id=session_id,
            reply="",
            state=WishesState(),
            messages=[],
            document_markdown="",
            missing_fields=[],
            is_complete=False,
            pending_conflict=None,
            warnings=[],
            audit_log=[],
            error={
                "code": "validation_error",
                "message": f"Message too long ({len(user_message)} chars). Max {MAX_MESSAGE_LENGTH}.",
            },
        )

    session = await store.get(session_id)
    if session is None:
        return TurnResult(
            session_id=session_id,
            reply="",
            state=WishesState(),
            messages=[],
            document_markdown="",
            missing_fields=[],
            is_complete=False,
            pending_conflict=None,
            warnings=[],
            audit_log=[],
            error={"code": "session_not_found", "message": f"Session '{session_id}' not found."},
        )

    # 2. Build ExtractionRequest
    import json
    state_snapshot = session.state.model_dump_json()
    request = ExtractionRequest(
        state_json=state_snapshot,
        recent_messages=session.recent_messages_for_llm(),
        user_message=user_message,
        focus_field=session.focus_field,
        pending_conflict=(
            session.pending_conflict.description if session.pending_conflict else None
        ),
    )

    # 3. Call LLM — on failure, return error without mutating state
    try:
        response = llm.extract(request)
    except LLMError as exc:
        return _llm_error_result(session, exc)

    warnings: List[str] = []

    # 5a. Resolve pending conflict first (before processing new updates)
    if session.pending_conflict and response.conflict_resolution in (
        "accept_proposed", "keep_existing"
    ):
        held = session.pending_conflict.get_held_updates()
        updates_to_apply, resolution_msg = resolve_conflict(
            held, response.conflict_resolution, session.state
        )
        session.pending_conflict = None
        for upd in updates_to_apply:
            _apply_update(session, upd, "llm")
        warnings.append(resolution_msg)

    # 4. Validate updates from this message
    raw_updates = [
        ProposedUpdate(
            field=u.field,
            value_text=u.value_text,
            value_bool=u.value_bool,
            value_list=u.value_list,
            confidence=u.confidence,
            evidence=u.evidence,
        )
        for u in response.updates
    ]

    focus_path: Optional[FieldPath] = None
    if session.focus_field:
        try:
            focus_path = FieldPath(session.focus_field)
        except ValueError:
            pass

    valid_updates, rejected_updates = validate_updates(
        raw_updates, user_message, focus_path
    )
    for rej in rejected_updates:
        warnings.append(f"Rejected update for '{rej.raw.field}': {rej.reason}")

    # 5b. Conflict detection on the validated updates
    if valid_updates and not session.pending_conflict:
        conflict_result = detect_conflicts(session.state, valid_updates)
        if conflict_result.has_conflict:
            session.pending_conflict = PendingConflict.from_validated_updates(
                description=conflict_result.description,
                updates=conflict_result.held_updates,
            )
            valid_updates = conflict_result.safe_updates
            warnings.append(f"Conflict detected: {conflict_result.description}")

    # 6. Apply surviving updates
    for upd in valid_updates:
        _apply_update(session, upd, "llm")

    # 7. Confirm all if applicable
    if (
        response.intent == "confirm"
        and session.state.is_complete()
        and not session.pending_conflict
    ):
        session.state.confirm_all_provided()

    # 8. Choose reply
    reply = _build_reply(session, response, warnings)

    # 9. Append messages and save
    session.add_message("user", user_message)
    session.add_message("assistant", reply)
    await store.save(session)

    doc = render_document(session.state, date.today())

    return TurnResult(
        session_id=session_id,
        reply=reply,
        state=session.state,
        messages=session.messages,
        document_markdown=doc,
        missing_fields=[p.value for p in session.state.missing_paths()],
        is_complete=session.state.is_complete(),
        pending_conflict=session.pending_conflict,
        warnings=warnings,
        audit_log=session.audit_log[-20:],
    )


async def process_direct_edit(
    session_id: str,
    edits: list,  # list of dicts: {field, value_text|value_bool|value_list} or {field, clear}
    store: SessionStore,
) -> DirectEditResult:
    """
    Apply direct UI edits (PATCH /state) through the same validator,
    bypassing the evidence check. Logged as source='user_edit'.
    """
    session = await store.get(session_id)
    if session is None:
        return DirectEditResult(
            session_id=session_id,
            state=WishesState(),
            messages=[],
            document_markdown="",
            missing_fields=[],
            is_complete=False,
            pending_conflict=None,
            warnings=["Session not found."],
            audit_log=[],
        )

    warnings: List[str] = []

    for edit in edits:
        field_str = edit.get("field", "")
        clear = edit.get("clear", False)

        if clear:
            try:
                path = FieldPath(field_str)
                old_val = session.state.get(path).value
                session.state.clear(path)
                session.log_change(path, old_val, None, "user_edit")
            except ValueError:
                warnings.append(f"Unknown field path: '{field_str}'")
            continue

        raw = ProposedUpdate(
            field=field_str,
            value_text=edit.get("value_text"),
            value_bool=edit.get("value_bool"),
            value_list=edit.get("value_list"),
            confidence="high",
            evidence="",  # direct edit — evidence check skipped
        )

        v, r = validate_updates([raw], "", None, skip_evidence_check=True)
        if r:
            warnings.append(f"Rejected edit for '{field_str}': {r[0].reason}")
            continue

        # Conflict detection
        conflict_result = detect_conflicts(session.state, v)
        if conflict_result.has_conflict:
            session.pending_conflict = PendingConflict.from_validated_updates(
                description=conflict_result.description,
                updates=conflict_result.held_updates,
            )
            for safe_upd in conflict_result.safe_updates:
                _apply_update(session, safe_upd, "user_edit")
            warnings.append(f"Conflict: {conflict_result.description}")
        else:
            for upd in v:
                _apply_update(session, upd, "user_edit")

    await store.save(session)
    doc = render_document(session.state, date.today())

    return DirectEditResult(
        session_id=session_id,
        state=session.state,
        messages=session.messages,
        document_markdown=doc,
        missing_fields=[p.value for p in session.state.missing_paths()],
        is_complete=session.state.is_complete(),
        pending_conflict=session.pending_conflict,
        warnings=warnings,
        audit_log=session.audit_log[-20:],
    )


def _llm_error_result(session: Session, exc: LLMError) -> TurnResult:
    """Build a TurnResult for an LLM error without mutating session state."""
    from app.llm.base import (
        LLMRateLimited,
        LLMUnavailable,
        LLMConfigError,
        LLMMalformedResponse,
    )

    if isinstance(exc, LLMRateLimited):
        code, msg = "llm_rate_limited", "The AI service is busy. Please try again shortly."
    elif isinstance(exc, LLMConfigError):
        code, msg = "llm_not_configured", (
            "The AI service is not configured. "
            "Set GEMINI_API_KEY in your .env file or use LLM_PROVIDER=mock."
        )
    elif isinstance(exc, LLMMalformedResponse):
        code, msg = "llm_malformed", "The AI returned an unreadable response. Please try again."
    else:
        code, msg = "llm_unavailable", "The AI service is temporarily unavailable."

    doc = render_document(session.state, date.today())
    return TurnResult(
        session_id=session.session_id,
        reply="",
        state=session.state,
        messages=session.messages,
        document_markdown=doc,
        missing_fields=[p.value for p in session.state.missing_paths()],
        is_complete=session.state.is_complete(),
        pending_conflict=session.pending_conflict,
        warnings=[],
        audit_log=session.audit_log[-20:],
        error={"code": code, "message": msg},
    )
