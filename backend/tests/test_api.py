"""
Tests for api/routes.py — all HTTP endpoints.

Uses FastAPI TestClient (httpx-backed, synchronous) with the mock LLM client
so no network calls are made.

Covers (spec §15.11):
- Create session → greeting returned
- Send message (mock) → reply + state update
- Get session → current state
- PATCH state → direct field edit
- Reset → state cleared, greeting re-sent
- 404 for unknown session
- 422 for empty message and over-long message
- Health endpoint
- Error response format consistency
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.domain.session import SessionStore
from app.domain.state import FieldPath, WishesState
from app.llm.base import ExtractionResponse, ProposedUpdateSchema
from tests.conftest import ScriptedLLMClient


# ---------------------------------------------------------------------------
# App fixture with injected mock dependencies
# ---------------------------------------------------------------------------

def make_test_app(llm=None, llm_configured: bool = True):
    """Build a fresh app instance with injected test dependencies."""
    from app.main import create_app
    from app.api.routes import set_dependencies

    app = create_app()
    store = SessionStore()

    # We need to replace the lifespan-set dependencies with our test ones
    # Patch set_dependencies to install test deps after the lifespan runs
    with TestClient(app) as client:
        set_dependencies(
            store=store,
            llm=llm,
            llm_provider="mock" if llm is not None else "gemini",
            llm_configured=llm_configured,
        )
        yield client, store


def _make_response(
    updates=None,
    intent: str = "answer",
    acknowledgement: str = "Got it.",
) -> ExtractionResponse:
    return ExtractionResponse(
        intent=intent,
        updates=updates or [],
        ambiguities=[],
        conflict_resolution=None,
        acknowledgement=acknowledgement,
    )


def _upd(field: str, **kwargs) -> ProposedUpdateSchema:
    defaults = dict(
        field=field, value_text=None, value_bool=None,
        value_list=None, confidence="high", evidence="",
    )
    defaults.update(kwargs)
    return ProposedUpdateSchema(**defaults)


# ---------------------------------------------------------------------------
# Helper: create a fresh client + session in one step
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_and_session():
    """Yields (TestClient, session_id, store) with a mock LLM."""
    from app.main import create_app
    from app.api.routes import set_dependencies

    app = create_app()
    store = SessionStore()
    mock_llm = ScriptedLLMClient([])   # tests add responses as needed

    # Override lifespan deps before first request
    import asyncio

    with TestClient(app, raise_server_exceptions=True) as client:
        set_dependencies(
            store=store,
            llm=mock_llm,
            llm_provider="mock",
            llm_configured=True,
        )
        resp = client.post("/api/sessions")
        assert resp.status_code == 201
        session_id = resp.json()["session_id"]
        yield client, session_id, store, mock_llm


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self):
        from app.main import create_app
        from app.api.routes import set_dependencies
        app = create_app()
        store = SessionStore()
        with TestClient(app) as client:
            set_dependencies(store=store, llm=None, llm_provider="mock", llm_configured=True)
            r = client.get("/api/health")
            assert r.status_code == 200
            data = r.json()
            assert data["status"] == "ok"
            assert "llm_provider" in data
            assert "llm_configured" in data

    def test_health_reports_unconfigured_when_no_key(self):
        from app.main import create_app
        from app.api.routes import set_dependencies
        app = create_app()
        store = SessionStore()
        with TestClient(app) as client:
            set_dependencies(store=store, llm=None, llm_provider="gemini", llm_configured=False)
            r = client.get("/api/health")
            data = r.json()
            assert data["llm_configured"] is False
            assert data["llm_provider"] == "gemini"


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    def test_create_session_returns_greeting(self, client_and_session):
        client, session_id, store, llm = client_and_session
        # Session was already created by the fixture
        r = client.get(f"/api/sessions/{session_id}")
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == session_id
        # Greeting should be the first (and only) message
        msgs = data["messages"]
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert "name" in msgs[0]["text"].lower()  # asks for full_name first

    def test_get_unknown_session_404(self, client_and_session):
        client, _, _, _ = client_and_session
        r = client.get("/api/sessions/does-not-exist")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "session_not_found"

    def test_reset_clears_state(self, client_and_session):
        client, session_id, store, llm = client_and_session

        # Add a field via direct edit
        r = client.patch(
            f"/api/sessions/{session_id}/state",
            json={"updates": [{"field": "full_name", "value_text": "Jane"}]},
        )
        assert r.status_code == 200

        # Reset
        r = client.post(f"/api/sessions/{session_id}/reset")
        assert r.status_code == 200
        data = r.json()
        # full_name should be unknown again
        fn = next(f for f in data["fields"] if f["path"] == "full_name")
        assert fn["status"] == "unknown"

    def test_reset_re_sends_greeting(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.post(f"/api/sessions/{session_id}/reset")
        msgs = r.json()["messages"]
        assert len(msgs) >= 1
        assert msgs[-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Sending messages
# ---------------------------------------------------------------------------

class TestSendMessage:
    def test_send_message_returns_reply(self, client_and_session):
        client, session_id, store, mock_llm = client_and_session
        mock_llm._queue.append(_make_response(
            updates=[_upd("full_name", value_text="Jane Smith", evidence="Jane Smith")]
        ))
        r = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "My name is Jane Smith."},
        )
        assert r.status_code == 200
        data = r.json()
        assert "reply" in data
        assert data["reply"]  # non-empty
        fn = next(f for f in data["fields"] if f["path"] == "full_name")
        assert fn["value"] == "Jane Smith"
        assert fn["status"] == "provided"

    def test_send_empty_message_422(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": ""},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"

    def test_send_over_long_message_422(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "x" * 2001},
        )
        assert r.status_code == 422

    def test_send_to_unknown_session_404(self, client_and_session):
        client, _, _, _ = client_and_session
        r = client.post(
            "/api/sessions/bad-id/messages",
            json={"message": "hello"},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "session_not_found"

    def test_messages_appended_to_history(self, client_and_session):
        client, session_id, store, mock_llm = client_and_session
        mock_llm._queue.append(_make_response())
        r = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": "hello"},
        )
        data = r.json()
        roles = [m["role"] for m in data["messages"]]
        assert "user" in roles
        assert "assistant" in roles


# ---------------------------------------------------------------------------
# LLM not configured
# ---------------------------------------------------------------------------

class TestLLMNotConfigured:
    def test_returns_503_when_llm_none(self):
        from app.main import create_app
        from app.api.routes import set_dependencies
        import asyncio
        app = create_app()
        store = SessionStore()
        with TestClient(app) as client:
            set_dependencies(store=store, llm=None, llm_provider="gemini", llm_configured=False)
            # Create session manually
            session = asyncio.get_event_loop().run_until_complete(store.create())
            r = client.post(
                f"/api/sessions/{session.session_id}/messages",
                json={"message": "hello"},
            )
            assert r.status_code == 503
            assert r.json()["error"]["code"] == "llm_not_configured"


# ---------------------------------------------------------------------------
# Direct state edit (PATCH)
# ---------------------------------------------------------------------------

class TestDirectEdit:
    def test_patch_applies_value(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.patch(
            f"/api/sessions/{session_id}/state",
            json={"updates": [{"field": "full_name", "value_text": "Jane Smith"}]},
        )
        assert r.status_code == 200
        fn = next(f for f in r.json()["fields"] if f["path"] == "full_name")
        assert fn["value"] == "Jane Smith"

    def test_patch_clear_resets_field(self, client_and_session):
        client, session_id, _, _ = client_and_session
        # Set first
        client.patch(
            f"/api/sessions/{session_id}/state",
            json={"updates": [{"field": "full_name", "value_text": "Jane"}]},
        )
        # Clear
        r = client.patch(
            f"/api/sessions/{session_id}/state",
            json={"updates": [{"field": "full_name", "clear": True}]},
        )
        fn = next(f for f in r.json()["fields"] if f["path"] == "full_name")
        assert fn["status"] == "unknown"

    def test_patch_bool_field(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.patch(
            f"/api/sessions/{session_id}/state",
            json={"updates": [{"field": "has_children", "value_bool": False}]},
        )
        hc = next(f for f in r.json()["fields"] if f["path"] == "has_children")
        assert hc["value"] is False

    def test_patch_list_field(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.patch(
            f"/api/sessions/{session_id}/state",
            json={"updates": [{"field": "specific_gifts", "value_list": ["gold watch"]}]},
        )
        sg = next(f for f in r.json()["fields"] if f["path"] == "specific_gifts")
        assert sg["value"] == ["gold watch"]

    def test_patch_unknown_session_404(self, client_and_session):
        client, _, _, _ = client_and_session
        r = client.patch(
            "/api/sessions/bad-id/state",
            json={"updates": [{"field": "full_name", "value_text": "x"}]},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Error format consistency
# ---------------------------------------------------------------------------

class TestErrorFormat:
    def _assert_error_shape(self, response):
        data = response.json()
        assert "error" in data
        assert "code" in data["error"]
        assert "message" in data["error"]
        assert isinstance(data["error"]["code"], str)
        assert isinstance(data["error"]["message"], str)

    def test_404_has_error_shape(self, client_and_session):
        client, _, _, _ = client_and_session
        r = client.get("/api/sessions/nonexistent")
        self._assert_error_shape(r)

    def test_422_has_error_shape(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"message": ""},
        )
        self._assert_error_shape(r)


# ---------------------------------------------------------------------------
# Document markdown in response
# ---------------------------------------------------------------------------

class TestDocumentInResponse:
    def test_session_view_has_document_markdown(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.get(f"/api/sessions/{session_id}")
        assert "document_markdown" in r.json()
        assert "FICTIONAL DOCUMENT" in r.json()["document_markdown"]

    def test_document_updates_after_edit(self, client_and_session):
        client, session_id, _, _ = client_and_session
        r = client.patch(
            f"/api/sessions/{session_id}/state",
            json={"updates": [{"field": "full_name", "value_text": "Jane Smith"}]},
        )
        assert "Jane Smith" in r.json()["document_markdown"]
