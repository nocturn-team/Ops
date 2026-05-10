from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    """A single function call request from the LLM."""

    id: str
    function_name: str
    arguments: str  # JSON string


@dataclass
class Message:
    """One message in a conversation history."""

    role: str  # "system" | "user" | "assistant" | "tool"
    content: str | None = None
    tool_calls: list[ToolCall] | None = None  # assistant messages only
    tool_call_id: str | None = None  # tool messages only
    name: str | None = None  # tool messages only


@dataclass
class ToolResult:
    """The result of executing a tool function."""

    tool_call_id: str
    function_name: str
    content: str  # stringified return value or error message


@dataclass
class ProviderResponse:
    """Response from the LLM provider."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str = ""


@dataclass
class StreamChunk:
    """One chunk from a streaming LLM response."""

    delta_content: str | None = None
    delta_tool_call: dict[str, Any] | None = None  # partial tool call delta
    finish_reason: str | None = None


class ToolUseMode:
    """Tool use strategy: native (LLM function calling) or prompt (injected into system prompt)."""

    NATIVE = "native"
    PROMPT = "prompt"


# --- Event types for the hook system ---


@dataclass
class ToolCallEvent:
    """Fired before a tool is executed. Return the event to allow (possibly modified), None to skip."""

    tool_call: ToolCall


@dataclass
class ToolResultEvent:
    """Fired after a tool executes. Return the event to allow (possibly modified result), None to skip sending."""

    tool_call: ToolCall
    result: ToolResult


@dataclass
class BeforeRequestEvent:
    """Fired before sending a request to the LLM. Return the event to allow, None to cancel the request."""

    messages: list[Message]
    tools: list[dict] | None


@dataclass
class AfterResponseEvent:
    """Fired after receiving an LLM response. Return the event to allow, None to suppress."""

    response: ProviderResponse
