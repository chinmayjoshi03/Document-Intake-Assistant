"""
LLM abstraction layer — protocol + shared request/response models + errors.

Design rationale: the LLM is an untrusted extraction component. It receives
the current state and conversation context and returns *proposed* updates.
It never writes the document and never decides what question comes next.

Using a Protocol rather than an ABC means we can swap providers (or inject
test doubles) without any inheritance coupling.

Flat typed value slots (value_text / value_bool / value_list) are used
instead of Union types because Gemini's structured-output schema handles
discriminated unions poorly. Exactly one slot is set per update; validation
enforces this.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base class for all LLM interaction errors."""


class LLMRateLimited(LLMError):
    """Provider returned HTTP 429 — caller should surface a 429 to the client."""


class LLMUnavailable(LLMError):
    """Provider returned 5xx, timed out, or had a network error."""


class LLMConfigError(LLMError):
    """Missing or invalid API key (401/403) or misconfigured provider."""


class LLMMalformedResponse(LLMError):
    """
    Provider returned output that could not be parsed after one repair retry.
    Caller should surface a 502.
    """


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------

class ExtractionRequest(BaseModel):
    """Everything the LLM needs to extract updates from one user message."""

    state_json: str = Field(
        description="Current WishesState serialised as JSON (for context)."
    )
    recent_messages: List[dict] = Field(
        default_factory=list,
        description="Last ≤10 turns [{role, text}] for conversation context.",
    )
    user_message: str = Field(
        description="The latest user message to extract updates from."
    )
    focus_field: Optional[str] = Field(
        default=None,
        description=(
            "Dotted FieldPath the assistant most recently asked about. "
            "Used to interpret bare yes/no/none replies."
        ),
    )
    pending_conflict: Optional[str] = Field(
        default=None,
        description="Human-readable description of an unresolved conflict, if any.",
    )


# ---------------------------------------------------------------------------
# Response model — returned by every LLMClient implementation
# ---------------------------------------------------------------------------

class ProposedUpdateSchema(BaseModel):
    """
    One proposed field update from the LLM.

    Flat slots instead of a Union because Gemini's JSON schema mode handles
    discriminated unions unreliably. Exactly one slot should be set; the
    validator in domain/updates.py enforces this.
    """

    field: str = Field(description="Dotted FieldPath, e.g. 'executor.name'.")
    value_text: Optional[str] = Field(
        default=None, description="String value for text fields."
    )
    value_bool: Optional[bool] = Field(
        default=None, description="Boolean value for bool fields."
    )
    value_list: Optional[List[str]] = Field(
        default=None, description="List value for list fields."
    )
    confidence: Literal["high", "low"] = Field(
        default="high",
        description="high = apply; low = surface as ambiguity for clarification.",
    )
    evidence: str = Field(
        description=(
            "Exact substring of the user message supporting this update. "
            "Required — used to detect hallucination."
        )
    )


class AmbiguitySchema(BaseModel):
    """A field the LLM could not confidently extract."""

    field: str = Field(description="Dotted FieldPath this ambiguity relates to.")
    reason: str = Field(description="Why the value could not be extracted.")
    clarifying_question: str = Field(
        description="The question to ask the user to resolve the ambiguity."
    )


class ExtractionResponse(BaseModel):
    """
    Structured output from the LLM extraction step.

    The LLM fills this; application code decides what to do with it.
    """

    intent: Literal["answer", "correction", "confirm", "other"] = Field(
        description=(
            "answer = user provided new information; "
            "correction = user is correcting an earlier answer; "
            "confirm = user is confirming the summary; "
            "other = chitchat, out-of-scope, etc."
        )
    )
    updates: List[ProposedUpdateSchema] = Field(
        default_factory=list,
        description="Proposed field updates to validate and apply.",
    )
    ambiguities: List[AmbiguitySchema] = Field(
        default_factory=list,
        description="Fields that were unclear and need clarification.",
    )
    conflict_resolution: Optional[Literal["accept_proposed", "keep_existing"]] = Field(
        default=None,
        description=(
            "Set when a pending_conflict was provided and the user resolved it. "
            "null when no conflict was pending or it remains unresolved."
        ),
    )
    acknowledgement: str = Field(
        default="",
        description=(
            "One short sentence acknowledging what the user said. "
            "Must not contain a question — the next question is chosen by code."
        ),
    )


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMClient(Protocol):
    """
    Provider-agnostic extraction interface.

    Implementations: GeminiClient (real), MockLLMClient (offline demo),
    ScriptedLLMClient (deterministic tests).
    """

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        """
        Extract proposed updates from a user message given current context.

        Must raise an LLMError subclass on failure — never return None.
        Must never mutate the request or any shared state.
        """
        ...
