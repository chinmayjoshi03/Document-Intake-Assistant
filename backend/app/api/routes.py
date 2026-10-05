"""
HTTP route handlers — pure I/O, no business logic.

All logic lives in domain/turn.py. Routes only:
  1. Validate the HTTP request
  2. Call the appropriate domain function
  3. Map the result to an HTTP response (including error codes)
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.api.schemas import (
    DirectEditRequest,
    ErrorDetail,
    ErrorResponse,
    FieldView,
    HealthResponse,
    MessageView,
    PendingConflictView,
    AuditEntryView,
    SendMessageRequest,
    SessionView,
    TurnResponse,
)
from app.domain.session import Session, SessionStore
from app.domain.state import FieldPath, WishesState
from app.domain.turn import (
    MAX_MESSAGE_LENGTH,
    TurnResult,
    DirectEditResult,
    process_direct_edit,
    process_turn,
)
from app.domain.questions import greeting_message
from app.documents.renderer import render_document
from app.llm.base import LLMClient

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Dependency injection helpers (set by main.py at startup)
# ---------------------------------------------------------------------------

_store: SessionStore | None = None
_llm: LLMClient | None = None
_llm_provider: str = "gemini"
_llm_configured: bool = False


def set_dependencies(
    store: SessionStore,
    llm: LLMClient | None,
    llm_provider: str,
    llm_configured: bool,
) -> None:
    global _store, _llm, _llm_provider, _llm_configured
    _store = store
    _llm = llm
    _llm_provider = llm_provider
    _llm_configured = llm_configured


def get_store() -> SessionStore:
    assert _store is not None
    return _store


def get_llm() -> LLMClient | None:
    return _llm


# ---------------------------------------------------------------------------
# Converters
# ---------------------------------------------------------------------------

def _fields_from_state(state: WishesState) -> list:
    """Flatten WishesState into a list of FieldView objects."""
    views = []
    for path in FieldPath:
        field = state.get(path)
        views.append(FieldView(
            path=path.value,
            value=field.value,
            status=field.status.value,
        ))
    return views


def _session_view(
    session: Session,
    document_markdown: str,
    missing_fields: list,
    is_complete: bool,
    pending_conflict: Any,
    warnings: list,
    audit_log: list,
) -> SessionView:
    messages = [
        MessageView(role=m.role, text=m.text, timestamp=m.timestamp)
        for m in session.messages
    ]
    audit = [
        AuditEntryView(
            field=e.field,
            old_value=e.old_value,
            new_value=e.new_value,
            source=e.source,
            timestamp=e.timestamp,
        )
        for e in audit_log
    ]
    pc = PendingConflictView(description=pending_conflict.description) if pending_conflict else None

    return SessionView(
        session_id=session.session_id,
        messages=messages,
        fields=_fields_from_state(session.state),
        document_markdown=document_markdown,
        missing_fields=missing_fields,
        is_complete=is_complete,
        pending_conflict=pc,
        warnings=warnings,
        audit_log=audit,
    )


def _turn_response(result: TurnResult, session: Session) -> TurnResponse:
    messages = [
        MessageView(role=m.role, text=m.text, timestamp=m.timestamp)
        for m in result.messages
    ]
    audit = [
        AuditEntryView(
            field=e.field,
            old_value=e.old_value,
            new_value=e.new_value,
            source=e.source,
            timestamp=e.timestamp,
        )
        for e in result.audit_log
    ]
    pc = PendingConflictView(description=result.pending_conflict.description) \
        if result.pending_conflict else None

    return TurnResponse(
        session_id=result.session_id,
        reply=result.reply,
        messages=messages,
        fields=_fields_from_state(result.state),
        document_markdown=result.document_markdown,
        missing_fields=result.missing_fields,
        is_complete=result.is_complete,
        pending_conflict=pc,
        warnings=result.warnings,
        audit_log=audit,
    )


def _error_response(code: str, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content={"error": {"code": code, "message": message}},
    )


def _map_error_code(code: str) -> int:
    mapping = {
        "llm_rate_limited": 429,
        "llm_unavailable": 503,
        "llm_malformed": 502,
        "llm_not_configured": 503,
        "session_not_found": 404,
        "validation_error": 422,
    }
    return mapping.get(code, 500)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        llm_provider=_llm_provider,
        llm_configured=_llm_configured,
    )


@router.post("/sessions", response_model=SessionView, status_code=status.HTTP_201_CREATED)
async def create_session(store: SessionStore = Depends(get_store)) -> SessionView:
    """
    Create a new session. Returns the greeting message as the first assistant
    message. No LLM call is made here — greeting is generated by code.
    """
    session = await store.create()
    greeting = greeting_message()
    session.add_message("assistant", greeting)
    await store.save(session)

    doc = render_document(session.state, date.today())
    missing = [p.value for p in session.state.missing_paths()]

    return _session_view(
        session=session,
        document_markdown=doc,
        missing_fields=missing,
        is_complete=False,
        pending_conflict=None,
        warnings=[],
        audit_log=[],
    )


@router.get("/sessions/{session_id}", response_model=SessionView)
async def get_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> SessionView:
    session = await store.get(session_id)
    if session is None:
        return _error_response("session_not_found", f"Session '{session_id}' not found.", 404)

    doc = render_document(session.state, date.today())
    missing = [p.value for p in session.state.missing_paths()]

    return _session_view(
        session=session,
        document_markdown=doc,
        missing_fields=missing,
        is_complete=session.state.is_complete(),
        pending_conflict=session.pending_conflict,
        warnings=[],
        audit_log=session.audit_log[-20:],
    )


@router.post("/sessions/{session_id}/messages", response_model=TurnResponse)
async def send_message(
    session_id: str,
    body: SendMessageRequest,
    store: SessionStore = Depends(get_store),
) -> Any:
    """Send a user message. Returns the assistant reply and updated session state."""
    # Validate before even hitting the domain — return 422 cleanly
    msg = body.message.strip()
    if not msg:
        return _error_response("validation_error", "Message cannot be empty.", 422)
    if len(msg) > MAX_MESSAGE_LENGTH:
        return _error_response(
            "validation_error",
            f"Message too long ({len(msg)} chars). Max {MAX_MESSAGE_LENGTH}.",
            422,
        )

    # Check if LLM is configured (only relevant for gemini provider)
    if _llm is None:
        return _error_response(
            "llm_not_configured",
            "The AI service is not configured. "
            "Set GEMINI_API_KEY in your .env or use LLM_PROVIDER=mock.",
            503,
        )

    session = await store.get(session_id)
    if session is None:
        return _error_response("session_not_found", f"Session '{session_id}' not found.", 404)

    result = await process_turn(session_id, msg, store, _llm)

    if result.error:
        code = result.error["code"]
        http_code = _map_error_code(code)
        return _error_response(code, result.error["message"], http_code)

    # Re-fetch session after turn (it was mutated in-place)
    updated_session = await store.get(session_id)
    return _turn_response(result, updated_session)


@router.patch("/sessions/{session_id}/state", response_model=SessionView)
async def patch_state(
    session_id: str,
    body: DirectEditRequest,
    store: SessionStore = Depends(get_store),
) -> Any:
    """Apply direct field edits from the UI. Same validation as LLM updates, no evidence check."""
    session = await store.get(session_id)
    if session is None:
        return _error_response("session_not_found", f"Session '{session_id}' not found.", 404)

    edits = [item.model_dump() for item in body.updates]
    result = await process_direct_edit(session_id, edits, store)

    updated_session = await store.get(session_id)
    return _session_view(
        session=updated_session,
        document_markdown=result.document_markdown,
        missing_fields=result.missing_fields,
        is_complete=result.is_complete,
        pending_conflict=result.pending_conflict,
        warnings=result.warnings,
        audit_log=result.audit_log,
    )


@router.post("/sessions/{session_id}/reset", response_model=SessionView)
async def reset_session(
    session_id: str,
    store: SessionStore = Depends(get_store),
) -> Any:
    """Reset a session's state and conversation history."""
    session = await store.reset(session_id)
    if session is None:
        return _error_response("session_not_found", f"Session '{session_id}' not found.", 404)

    # Add the greeting again
    greeting = greeting_message()
    session.add_message("assistant", greeting)
    await store.save(session)

    doc = render_document(session.state, date.today())
    return _session_view(
        session=session,
        document_markdown=doc,
        missing_fields=[p.value for p in session.state.missing_paths()],
        is_complete=False,
        pending_conflict=None,
        warnings=[],
        audit_log=[],
    )
