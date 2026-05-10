from __future__ import annotations

from typing import Iterator

from ._types import StreamChunk, ToolCall


class _ToolCallAccum:
    __slots__ = ("id", "name", "arguments")

    def __init__(self) -> None:
        self.id: str = ""
        self.name: str = ""
        self.arguments: str = ""


class StreamIterator:
    """Wraps an SSE stream, yielding content deltas and accumulating tool call data."""

    def __init__(self, raw_chunks: Iterator[StreamChunk]) -> None:
        """
        Create a StreamIterator over raw SSE chunks.

        Args:
            raw_chunks: Iterator of StreamChunk from the provider's stream_complete method.
        """
        self._raw = raw_chunks
        self._exhausted = False
        self._tool_call_accums: dict[int, _ToolCallAccum] = {}
        self._assembled_tool_calls: list[ToolCall] | None = None

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        """Yield the next content delta string from the stream."""
        while True:
            chunk = next(self._raw)  # let StopIteration propagate
            self._process_chunk(chunk)
            if chunk.delta_content is not None:
                return chunk.delta_content
            if chunk.finish_reason is not None:
                self._exhausted = True
                self._finalize_tool_calls()
                raise StopIteration

    def _process_chunk(self, chunk: StreamChunk) -> None:
        """Accumulate tool call deltas from a single chunk."""
        if chunk.delta_tool_call is not None:
            tc_delta = chunk.delta_tool_call
            idx = tc_delta.get("index", 0)
            if idx not in self._tool_call_accums:
                self._tool_call_accums[idx] = _ToolCallAccum()
            accum = self._tool_call_accums[idx]
            if "id" in tc_delta and tc_delta["id"]:
                accum.id = tc_delta["id"]
            fn_delta = tc_delta.get("function", {})
            if "name" in fn_delta and fn_delta["name"]:
                accum.name = fn_delta["name"]
            if "arguments" in fn_delta and fn_delta["arguments"]:
                accum.arguments += fn_delta["arguments"]

    def _finalize_tool_calls(self) -> None:
        """Assemble accumulated tool call deltas into ToolCall objects."""
        if self._assembled_tool_calls is not None:
            return
        if not self._tool_call_accums:
            self._assembled_tool_calls = []
            return
        calls = []
        for idx in sorted(self._tool_call_accums):
            accum = self._tool_call_accums[idx]
            calls.append(
                ToolCall(
                    id=accum.id or f"tc_{idx}",
                    function_name=accum.name,
                    arguments=accum.arguments,
                )
            )
        self._assembled_tool_calls = calls

    @property
    def tool_calls(self) -> list[ToolCall] | None:
        """Return assembled tool calls after the stream is fully consumed, or None if still streaming."""
        if not self._exhausted:
            return None
        self._finalize_tool_calls()
        return self._assembled_tool_calls
