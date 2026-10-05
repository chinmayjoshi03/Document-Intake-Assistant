"""
API request/response schemas.

These are the public contract. All business types (WishesState, Session, etc.)
are mapped to these flat, documented Pydantic models so the /docs page is
accurate and the frontend has a stable contract to code against.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------

class FieldView(BaseModel):
    """One field in the state table."""

    path: str
    value: Any = None
    status: str  # "unknown" | "provided" | "confirmed"


class AuditEntryView(BaseModel):
    """One audit log entry."""

    field: str
    old_value: Any = None
    new_value: Any = None
    source: str   # "llm" | "user_edit"
    timestamp: datetime


class MessageView(BaseModel):
    """One conversation message."""

    role: str   # "user" | "assistant"
    text: str
    timestamp: datetime


class PendingConflictView(BaseModel):
    description: str


# ---------------------------------------------------------------------------
# SessionView — returned by most endpoints
# ---------------------------------------------------------------------------

class SessionView(BaseModel):
    """Full session snapshot returned by GET/POST/PATCH/reset endpoints."""

    session_id: str
    messages: List[MessageView]
    fields: List[FieldView]           # all nine fields (flattened)
    document_markdown: str
    missing_fields: List[str]
    is_complete: bool
    pending_conflict: Optional[PendingConflictView] = None
    warnings: List[str] = Field(default_factory=list)
    audit_log: List[AuditEntryView] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# TurnResponse — returned by POST /messages
# ---------------------------------------------------------------------------

class TurnResponse(SessionView):
    """Extends SessionView with the assistant's reply for this turn."""

    reply: str


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------

class SendMessageRequest(BaseModel):
    message: str = Field(description="User message, 1–2000 characters.")


class DirectEditItem(BaseModel):
    """One field update in a PATCH /state request."""

    field: str
    value_text: Optional[str] = None
    value_bool: Optional[bool] = None
    value_list: Optional[List[str]] = None
    clear: bool = False     # if True, reset field to UNKNOWN regardless of value slots


class DirectEditRequest(BaseModel):
    updates: List[DirectEditItem]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    llm_provider: str
    llm_configured: bool


# ---------------------------------------------------------------------------
# Error
# ---------------------------------------------------------------------------

class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
