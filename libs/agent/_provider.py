from __future__ import annotations

import json
import os
from typing import Any, Iterator, Protocol, runtime_checkable

import httpx

from ._types import Message, ProviderResponse, StreamChunk, ToolCall


@runtime_checkable
class ProviderAdapter(Protocol):
    """Protocol that any LLM backend adapter must satisfy."""

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> ProviderResponse:
        """Send a non-streaming chat completion request and return the full response."""
        ...

    def stream_complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> Iterator[StreamChunk]:
        """Send a streaming chat completion request and yield response chunks."""
        ...


class OpenAICompatibleAdapter:
    """httpx-driven adapter for OpenAI-compatible chat completions APIs."""

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        model: str = "gpt-4o",
        timeout: float = 60.0,
    ) -> None:
        """
        Create an OpenAI-compatible adapter.

        Args:
            base_url: API base URL (e.g. "https://api.openai.com/v1").
            api_key: API key. Falls back to OPENAI_API_KEY env var. Raises ValueError if neither is set.
            model: Model name to use in requests.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "api_key must be provided or set OPENAI_API_KEY environment variable"
            )
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    def complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> ProviderResponse:
        """Send a non-streaming chat completion request."""
        body = self._build_request_body(messages, tools, tool_choice, stream=False)
        resp = self._client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        )
        resp.raise_for_status()
        return self._parse_response(resp.json())

    def stream_complete(
        self,
        messages: list[Message],
        *,
        tools: list[dict] | None = None,
        tool_choice: str | dict | None = None,
    ) -> Iterator[StreamChunk]:
        """Send a streaming chat completion request, yielding parsed SSE chunks."""
        body = self._build_request_body(messages, tools, tool_choice, stream=True)
        with self._client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=body,
        ) as resp:
            resp.raise_for_status()
            yield from self._parse_sse_stream(resp)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_request_body(
        self,
        messages: list[Message],
        tools: list[dict] | None,
        tool_choice: str | dict | None,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self._messages_to_dicts(messages),
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
        if tool_choice is not None:
            body["tool_choice"] = tool_choice
        return body

    def _messages_to_dicts(self, messages: list[Message]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for msg in messages:
            d: dict[str, Any] = {"role": msg.role}
            if msg.content is not None:
                d["content"] = msg.content
            if msg.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function_name,
                            "arguments": tc.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            if msg.tool_call_id is not None:
                d["tool_call_id"] = msg.tool_call_id
            if msg.name is not None:
                d["name"] = msg.name
            result.append(d)
        return result

    def _parse_response(self, data: dict) -> ProviderResponse:
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content")
        tool_calls = None
        if "tool_calls" in message and message["tool_calls"]:
            tool_calls = [
                ToolCall(
                    id=tc["id"],
                    function_name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                )
                for tc in message["tool_calls"]
            ]
        return ProviderResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", ""),
        )

    def _parse_sse_stream(self, response: httpx.Response) -> Iterator[StreamChunk]:
        """Parse Server-Sent Events from a streaming response, yielding StreamChunks."""
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            data = line[len("data: "):]
            if data.strip() == "[DONE]":
                break
            try:
                chunk_json = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk_json.get("choices", [])
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta", {})
            delta_content = delta.get("content")
            delta_tool_call = None
            if "tool_calls" in delta and delta["tool_calls"]:
                delta_tool_call = delta["tool_calls"][0]
            yield StreamChunk(
                delta_content=delta_content,
                delta_tool_call=delta_tool_call,
                finish_reason=choice.get("finish_reason"),
            )
