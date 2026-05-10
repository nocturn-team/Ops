from __future__ import annotations

import json
import re
from typing import Any, Callable
from uuid import uuid4

from ._provider import OpenAICompatibleAdapter, ProviderAdapter
from ._streaming import StreamIterator
from ._tools import ToolRegistry
from ._types import (
    AfterResponseEvent,
    BeforeRequestEvent,
    Message,
    ProviderResponse,
    ToolCall,
    ToolCallEvent,
    ToolResult,
    ToolResultEvent,
    ToolUseMode,
)

_MAX_TOOL_ITERATIONS = 10

_EVENT_NAMES = {"tool_call", "tool_result", "before_request", "after_response"}

_TOOL_CALL_JSON_BLOCK = re.compile(
    r'```json\s*(\{.*?"tool_call".*?\})\s*```', re.DOTALL
)
_TOOL_CALL_BARE = re.compile(
    r'\{\s*"tool_call"\s*:\s*\{[^}]+\}\s*\}', re.DOTALL
)


class Session:
    """Maintains conversation history for multi-turn chat."""

    def __init__(self, system_prompt: str | None = None) -> None:
        self.messages: list[Message] = []
        if system_prompt:
            self.messages.append(Message(role="system", content=system_prompt))

    def add(self, message: Message) -> None:
        """Append a message to the conversation history."""
        self.messages.append(message)


class Agent:
    """An LLM-backed agent with tools, sessions, and lifecycle hooks."""

    def __init__(
        self,
        *,
        name: str,
        prompt: str,
        tools: list[Callable] | None = None,
        provider: ProviderAdapter | None = None,
        tool_use_feature: str = "native",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o",
    ) -> None:
        """
        Create an Agent.

        Args:
            name: Identifier for this agent.
            prompt: System prompt injected at session creation.
            tools: List of Python functions the agent can call.
            provider: LLM backend adapter. If None, an OpenAICompatibleAdapter is created.
            tool_use_feature: "native" for LLM function calling, "prompt" for prompt injection.
            api_key: API key passed to the default provider (ignored if provider is given).
            base_url: Base URL for the default provider (ignored if provider is given).
            model: Model name for the default provider (ignored if provider is given).
        """
        self.name = name
        self.prompt = prompt
        self.tool_use_feature = tool_use_feature

        self._registry = ToolRegistry(tools or [])
        self._provider = provider or OpenAICompatibleAdapter(
            api_key=api_key, base_url=base_url, model=model,
        )
        self._handlers: dict[str, list[Callable]] = {n: [] for n in _EVENT_NAMES}

    def event(self, name: str) -> Callable:
        """
        Decorator to register an event handler.

        Args:
            name: One of "tool_call", "tool_result", "before_request", "after_response".

        Returns:
            A decorator that registers the handler.

        The handler receives one argument — an Event dataclass instance.
        Return the event (possibly modified) to allow, or None to cancel/skip.
        Returning a different type raises TypeError.

        Example::

            @agent.event("tool_call")
            def on_tool_call(event: ToolCallEvent):
                if event.tool_call.function_name == "dangerous":
                    return None  # skip execution
                return event
        """
        if name not in _EVENT_NAMES:
            raise ValueError(
                f"Unknown event '{name}'. Must be one of: {', '.join(sorted(_EVENT_NAMES))}"
            )

        def decorator(fn: Callable) -> Callable:
            self._handlers[name].append(fn)
            return fn

        return decorator

    def create_session(self) -> Session:
        """Create a new session with the agent's system prompt. Prompt-mode tools are injected here."""
        system_prompt = self.prompt
        if self.tool_use_feature == ToolUseMode.PROMPT:
            desc = self._registry.get_prompt_descriptions()
            if desc:
                system_prompt = f"{system_prompt}\n\n{desc}"
        return Session(system_prompt=system_prompt)

    def chat(
        self,
        message: str,
        session: Session,
        *,
        stream: bool = False,
        tool_choice: str | dict | None = None,
    ) -> str | StreamIterator:
        """
        Send a message and get a response.

        Args:
            message: User message text.
            session: The session to use for conversation history.
            stream: If True, return a StreamIterator; otherwise return a string.
            tool_choice: Override tool choice for native mode.

        Returns:
            The assistant's response text, or a StreamIterator if stream=True.
            Note: event hooks are not fired during streaming.
        """
        session.add(Message(role="user", content=message))

        if self.tool_use_feature == ToolUseMode.NATIVE:
            return self._chat_native(session, stream=stream, tool_choice=tool_choice)
        return self._chat_prompt(session, stream=stream)

    def _emit_event(self, name: str, event: Any) -> Any:
        for handler in self._handlers.get(name, []):
            result = handler(event)
            if result is None:
                return None
            if type(result) is not type(event):
                raise TypeError(
                    f"Handler for '{name}' must return {type(event).__name__} or None, "
                    f"got {type(result).__name__}"
                )
            event = result
        return event

    def _chat_native(
        self,
        session: Session,
        *,
        stream: bool,
        tool_choice: str | dict | None,
    ) -> str | StreamIterator:
        schemas = self._registry.get_openai_schemas() or None
        kwargs: dict[str, Any] = {}
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice

        if stream:
            raw = self._provider.stream_complete(
                session.messages, tools=schemas, **kwargs,
            )
            return StreamIterator(raw)

        for _ in range(_MAX_TOOL_ITERATIONS):
            before = self._emit_event(
                "before_request",
                BeforeRequestEvent(messages=session.messages, tools=schemas),
            )
            if before is None:
                raise RuntimeError("before_request handler cancelled the request")

            resp = self._provider.complete(
                before.messages, tools=before.tools, **kwargs,
            )

            after = self._emit_event("after_response", AfterResponseEvent(response=resp))
            if after is None:
                return ""
            resp = after.response

            if resp.tool_calls:
                session.add(Message(
                    role="assistant",
                    content=resp.content,
                    tool_calls=resp.tool_calls,
                ))
                for tc in resp.tool_calls:
                    call_event = self._emit_event("tool_call", ToolCallEvent(tool_call=tc))
                    if call_event is None:
                        continue
                    tc = call_event.tool_call
                    result = self._registry.execute(tc)
                    result_event = self._emit_event(
                        "tool_result", ToolResultEvent(tool_call=tc, result=result),
                    )
                    if result_event is None:
                        continue
                    session.add(Message(
                        role="tool",
                        content=result_event.result.content,
                        tool_call_id=result_event.result.tool_call_id,
                        name=result_event.result.function_name,
                    ))
                continue
            session.add(Message(role="assistant", content=resp.content))
            return resp.content or ""

        return resp.content or ""

    def _chat_prompt(
        self,
        session: Session,
        *,
        stream: bool,
    ) -> str | StreamIterator:
        if stream:
            raw = self._provider.stream_complete(session.messages)
            return StreamIterator(raw)

        content = ""
        for _ in range(_MAX_TOOL_ITERATIONS):
            before = self._emit_event(
                "before_request",
                BeforeRequestEvent(messages=session.messages, tools=None),
            )
            if before is None:
                raise RuntimeError("before_request handler cancelled the request")

            resp = self._provider.complete(before.messages)

            after = self._emit_event("after_response", AfterResponseEvent(response=resp))
            if after is None:
                return ""
            resp = after.response

            content = resp.content or ""
            tc = self._parse_prompt_tool_call(content)
            if tc is None:
                session.add(Message(role="assistant", content=content))
                return content

            session.add(Message(role="assistant", content=content))

            call_event = self._emit_event("tool_call", ToolCallEvent(tool_call=tc))
            if call_event is None:
                continue
            tc = call_event.tool_call

            result = self._registry.execute(tc)
            result_event = self._emit_event(
                "tool_result", ToolResultEvent(tool_call=tc, result=result),
            )
            if result_event is None:
                continue
            session.add(Message(
                role="tool",
                content=result_event.result.content,
                tool_call_id=result_event.result.tool_call_id,
                name=result_event.result.function_name,
            ))

        return content

    def _parse_prompt_tool_call(self, content: str) -> ToolCall | None:
        match = _TOOL_CALL_JSON_BLOCK.search(content) or _TOOL_CALL_BARE.search(content)
        if not match:
            return None
        raw = match.group(1) if match.lastindex else match.group(0)
        try:
            parsed = json.loads(raw)
            call_data = parsed["tool_call"]
            return ToolCall(
                id=f"prompt_{uuid4().hex[:8]}",
                function_name=call_data["name"],
                arguments=json.dumps(call_data["arguments"]),
            )
        except (json.JSONDecodeError, KeyError):
            return None
