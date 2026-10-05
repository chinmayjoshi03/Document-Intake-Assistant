"""
Session model and in-memory session store.

Design rationale: sessions are kept in memory behind a lock. This is an
intentional simplification documented as a known limitation (see
PRODUCTION_NOTES.md). In production, this would be backed by a persistent
store (Redis, PostgreSQL) with proper TTL and ownership checks.

The lock ensures that concurrent requests for the same session are serialised
— important when the frontend polls or the user submits quickly.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.domain.state import FieldPath, WishesState
from app.domain.updates import ValidatedUpdate


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """A single turn in the conversation history."""

    role: Literal["user", "assistant"]
    text: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Audit log entry
# ---------------------------------------------------------------------------

class AuditEntry(BaseModel):
    """
    One field change.  Logged for every state mutation regardless of source.
    Source distinguishes LLM-proposed changes from direct UI edits.
    """

    field: str                          # FieldPath.value (dotted path string)
    old_value: Any = None
    new_value: Any = None
    source: Literal["llm", "user_edit"]
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Pending conflict
# ---------------------------------------------------------------------------

class PendingConflict(BaseModel):
    """
    A conflict that was detected but not yet resolved.

    The held_updates are kept here until the user resolves the conflict via
    accept_proposed or keep_existing.
    """

    description: str
    # Serialise held updates as dicts so Pydantic can round-trip them
    held_updates: List[Dict[str, Any]] = Field(default_factory=list)

    def get_held_updates(self) -> List[ValidatedUpdate]:
        """Deserialise held_updates back into ValidatedUpdate objects."""
        return [
            ValidatedUpdate(
                path=FieldPath(item["path"]),
                value=item["value"],
                confidence=item.get("confidence", "high"),
            )
            for item in self.held_updates
        ]

    @classmethod
    def from_validated_updates(
        cls, description: str, updates: List[ValidatedUpdate]
    ) -> "PendingConflict":
        held = [
            {"path": u.path.value, "value": u.value, "confidence": u.confidence}
            for u in updates
        ]
        return cls(description=description, held_updates=held)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class Session(BaseModel):
    """
    Full session state.

    Mutable in place — callers must hold the SessionStore lock before
    reading or writing, since Pydantic models are not thread/coroutine-safe.
    """

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    state: WishesState = Field(default_factory=WishesState)
    messages: List[Message] = Field(default_factory=list)
    focus_field: Optional[str] = None   # FieldPath.value of last asked question
    pending_conflict: Optional[PendingConflict] = None
    audit_log: List[AuditEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    def add_message(self, role: Literal["user", "assistant"], text: str) -> None:
        self.messages.append(Message(role=role, text=text))

    def log_change(
        self,
        field: FieldPath,
        old_value: Any,
        new_value: Any,
        source: Literal["llm", "user_edit"],
    ) -> None:
        self.audit_log.append(
            AuditEntry(
                field=field.value,
                old_value=old_value,
                new_value=new_value,
                source=source,
            )
        )

    def recent_messages_for_llm(self, limit: int = 10) -> List[dict]:
        """Return the last N messages as plain dicts for the LLM request."""
        tail = self.messages[-limit:]
        return [{"role": m.role, "text": m.text} for m in tail]

    def reset(self) -> None:
        """Reset state and history, preserving the session_id."""
        self.state = WishesState()
        self.messages = []
        self.focus_field = None
        self.pending_conflict = None
        self.audit_log = []


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------

class SessionStore:
    """
    In-memory store for sessions, protected by an asyncio lock.

    Known limitation: state is lost on server restart and not shared across
    workers.  See PRODUCTION_NOTES.md for the production path.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._lock = asyncio.Lock()

    async def create(self) -> Session:
        async with self._lock:
            session = Session()
            self._sessions[session.session_id] = session
            return session

    async def get(self, session_id: str) -> Optional[Session]:
        async with self._lock:
            return self._sessions.get(session_id)

    async def save(self, session: Session) -> None:
        """Persist any in-place mutations back to the store."""
        async with self._lock:
            self._sessions[session.session_id] = session

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                return True
            return False

    async def reset(self, session_id: str) -> Optional[Session]:
        """Reset a session's state and history in place."""
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            session.reset()
            return session
