from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    """A function-call request returned by the LLM."""

    name: str
    arguments: str  # raw JSON string, as returned by the provider
    call_id: str | None = None


@dataclass
class LLMResult:
    """The outcome of one LLM turn: a plain-text reply, or a tool call."""

    text: str | None = None
    tool_call: ToolCall | None = None


class LLMUnavailableError(Exception):
    """Raised when the LLM provider is unreachable, times out, or errors."""


class LLMClient(Protocol):
    """Provider-agnostic interface for a single chat completion turn."""

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult: ...


@dataclass
class FakeLLMClient:
    """Test double for `LLMClient`.

    Configure `result` to have `complete()` return it, or `error` to have
    `complete()` raise it instead. Either attribute can be reassigned between
    calls to simulate different behavior on successive turns.
    """

    result: LLMResult | None = None
    error: LLMUnavailableError | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResult:
        self.calls.append({"messages": messages, "tools": tools})
        if self.error is not None:
            raise self.error
        assert self.result is not None, "FakeLLMClient has no configured result or error"
        return self.result
