"""
FastAPI application factory.

Responsibilities:
  - Create the FastAPI app instance
  - Set up lifespan (create SessionStore, select LLM provider)
  - Mount the frontend as static files at /
  - Register the API router at /api
  - Handle provider selection: if gemini + no key, start normally but mark unconfigured

Dependency rule: this file wires everything together; it imports from api/,
domain/, llm/, and config. No business logic lives here.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.api.routes import router, set_dependencies
from app.domain.session import SessionStore

logger = logging.getLogger(__name__)

# Absolute path to the frontend directory (two levels up from this file)
FRONTEND_DIR = Path(__file__).parent.parent.parent / "frontend"


def _build_llm_client() -> Any:
    """
    Instantiate the LLM client based on settings.

    Returns None if the provider key is missing — the app starts normally
    but chat requests will return llm_not_configured.
    """
    if settings.llm_provider == "mock":
        from app.llm.mock_client import MockLLMClient
        logger.info("LLM provider: mock (offline mode)")
        return MockLLMClient()

    # Groq provider
    if settings.llm_provider == "groq":
        if not settings.groq_api_key:
            logger.warning(
                "LLM_PROVIDER=groq but GROQ_API_KEY is not set. "
                "Chat will return llm_not_configured until a key is provided."
            )
            return None
        try:
            from app.llm.groq_client import GroqClient
            client = GroqClient(settings)
            logger.info("LLM provider: groq (model=%s)", settings.groq_model)
            return client
        except Exception as exc:
            logger.error("Failed to initialise Groq client: %s", exc)
            return None

    # Gemini provider (fallback)
    if not settings.gemini_api_key:
        logger.warning(
            "LLM_PROVIDER=gemini but GEMINI_API_KEY is not set. "
            "Chat will return llm_not_configured until a key is provided."
        )
        return None

    try:
        from app.llm.gemini_client import GeminiClient
        client = GeminiClient(settings)
        logger.info("LLM provider: gemini (model=%s)", settings.gemini_model)
        return client
    except Exception as exc:
        logger.error("Failed to initialise Gemini client: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Set up shared singletons at startup; clean up on shutdown."""
    store = SessionStore()
    llm = _build_llm_client()
    llm_configured = settings.llm_configured

    set_dependencies(
        store=store,
        llm=llm,
        llm_provider=settings.llm_provider,
        llm_configured=llm_configured,
    )
    logger.info(
        "App started. Provider=%s configured=%s",
        settings.llm_provider,
        llm_configured,
    )
    yield
    # Nothing to clean up for in-memory store
    logger.info("App shutting down.")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Document Intake Assistant",
        description=(
            "Conversational assistant that collects information for a fictional "
            "Personal Wishes Document. The LLM proposes — the code decides."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # API routes
    app.include_router(router)

    # Serve the frontend as static files.
    # The /api routes take precedence because they are registered first.
    # Fall back to index.html for unknown paths via html=True.
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
    else:
        logger.warning("Frontend directory not found at %s — UI will not be served.", FRONTEND_DIR)

    return app


# WSGI/ASGI entry point for uvicorn
app = create_app()
