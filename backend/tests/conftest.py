"""
Shared test infrastructure.

ScriptedLLMClient: returns queued ExtractionResponse objects loaded from
fixture files so tests are fully deterministic and require no network access.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Deque, List, Union

import pytest

from app.llm.base import ExtractionRequest, ExtractionResponse, LLMMalformedResponse

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class ScriptedLLMClient:
    """
    Deterministic test double for LLMClient.

    Accepts either ExtractionResponse objects or strings (raw text the
    Gemini client would return before parsing) queued in order.  Raises
    LLMMalformedResponse when the queue is empty or a raw string is invalid.
    """

    def __init__(self, responses: List[Union[ExtractionResponse, str]]):
        self._queue: Deque[Union[ExtractionResponse, str]] = deque(responses)
        self.calls: List[ExtractionRequest] = []

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.calls.append(request)
        if not self._queue:
            raise LLMMalformedResponse("ScriptedLLMClient queue exhausted")
        item = self._queue.popleft()
        if isinstance(item, ExtractionResponse):
            return item
        # Raw string — attempt to parse; raise on failure
        try:
            return ExtractionResponse.model_validate_json(item)
        except Exception as exc:
            raise LLMMalformedResponse(f"ScriptedLLMClient parse failure: {exc}") from exc


def load_fixture(name: str) -> dict:
    """Load a fixture JSON file by name (without .json extension)."""
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


def response_from_fixture(name: str) -> ExtractionResponse:
    """Load a fixture and parse it as ExtractionResponse."""
    data = load_fixture(name)
    # Strip meta keys
    payload = {k: v for k, v in data.items() if not k.startswith("_")}
    return ExtractionResponse.model_validate(payload)
