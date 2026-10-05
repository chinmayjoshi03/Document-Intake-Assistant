"""
Application configuration loaded from environment variables.

Why pydantic-settings: gives us type coercion, validation, and .env loading
in one place without boilerplate. All secrets stay out of source control.
"""

from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM provider: "gemini", "groq", or "mock"
    llm_provider: str = Field(default="groq", alias="LLM_PROVIDER")

    # Gemini API key — optional so the app starts without one
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")

    # Gemini model name
    gemini_model: str = Field(default="gemini-2.5-flash", alias="GEMINI_MODEL")

    # Groq API key — optional so the app starts without one
    groq_api_key: Optional[str] = Field(default=None, alias="GROQ_API_KEY")

    # Groq model name — llama-3.3-70b-versatile is a strong general-purpose choice
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")

    # Network timeout for LLM calls in seconds
    llm_timeout_seconds: int = Field(default=20, alias="LLM_TIMEOUT_SECONDS")

    @property
    def llm_configured(self) -> bool:
        """True only when the active provider has a non-empty key."""
        if self.llm_provider == "mock":
            return True
        if self.llm_provider == "groq":
            return bool(self.groq_api_key)
        return bool(self.gemini_api_key)


# Module-level singleton — import this everywhere instead of constructing anew
settings = Settings()
