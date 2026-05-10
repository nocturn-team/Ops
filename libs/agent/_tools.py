from __future__ import annotations

import inspect
import json
import re
import traceback
from typing import Any, Callable, get_type_hints

from ._types import ToolCall, ToolResult


def _python_type_to_json(py_type: Any) -> dict | str:
    """Map a Python type annotation to a JSON Schema type string or dict."""
    origin = getattr(py_type, "__origin__", None)

    if py_type is str:
        return "string"
    if py_type is int:
        return "integer"
    if py_type is float:
        return "number"
    if py_type is bool:
        return "boolean"

    if origin is list:
        args = getattr(py_type, "__args__", None)
        if args:
            return {"type": "array", "items": _python_type_to_json(args[0])}
        return {"type": "array"}
    if origin is dict:
        return {"type": "object"}

    # Handle Union / X | None (Optional[X])
    type_args = getattr(py_type, "__args__", None)
    if type_args:
        non_none = [a for a in type_args if a is not type(None)]
        if len(non_none) == 1 and len(type_args) == 2:
            inner = _python_type_to_json(non_none[0])
            if isinstance(inner, str):
                return {"anyOf": [{"type": inner}, {"type": "null"}]}
            return {"anyOf": [inner, {"type": "null"}]}

    return "string"


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Parse a Google-style docstring into (summary, {param: description})."""
    if not doc:
        return ("", {})
    lines = doc.strip().splitlines()
    summary_parts: list[str] = []
    arg_descriptions: dict[str, str] = {}
    in_args = False
    current_arg: str | None = None
    current_desc: list[str] = []

    def _flush_arg() -> None:
        nonlocal current_arg, current_desc
        if current_arg:
            arg_descriptions[current_arg] = " ".join(current_desc).strip()
        current_arg = None
        current_desc = []

    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("args:") or stripped.lower().startswith("arguments:"):
            _flush_arg()
            in_args = True
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                match = re.match(r"(\w+)\s*:\s*(.*)", rest)
                if match:
                    _flush_arg()
                    current_arg = match.group(1)
                    current_desc = [match.group(2)]
            continue
        if in_args and stripped.lower().startswith("returns:"):
            _flush_arg()
            in_args = False
            continue
        if in_args:
            match = re.match(r"(\w+)\s*:\s*(.*)", stripped)
            if match:
                _flush_arg()
                current_arg = match.group(1)
                current_desc = [match.group(2)]
            elif current_arg and stripped:
                current_desc.append(stripped)
        else:
            if stripped:
                summary_parts.append(stripped)

    _flush_arg()
    return (" ".join(summary_parts), arg_descriptions)


class ToolRegistry:
    """Inspects Python functions and produces OpenAI-compatible tool schemas and prompt descriptions."""

    def __init__(self, functions: list[Callable]) -> None:
        """
        Create a registry by introspecting the given functions.

        Args:
            functions: List of Python callables to register as tools.
        """
        self._functions: dict[str, Callable] = {}
        self._schemas: list[dict] = []
        self._descriptions: str = ""
        for fn in functions:
            name = fn.__name__
            self._functions[name] = fn
            self._schemas.append(self._inspect_function(fn))
        self._descriptions = self._build_prompt_descriptions()

    def get_openai_schemas(self) -> list[dict]:
        """Return OpenAI-format tool schemas for native tool_use mode."""
        return self._schemas

    def get_prompt_descriptions(self) -> str:
        """Return a formatted text block describing all tools for prompt injection mode."""
        return self._descriptions

    def execute(self, call: ToolCall) -> ToolResult:
        """
        Execute a tool call by name with the given arguments.

        Args:
            call: The ToolCall to execute.

        Returns:
            A ToolResult with the stringified return value, or an error message.
        """
        fn = self._functions.get(call.function_name)
        if fn is None:
            return ToolResult(
                tool_call_id=call.id,
                function_name=call.function_name,
                content=f"Error: unknown function '{call.function_name}'",
            )
        try:
            args = json.loads(call.arguments) if call.arguments else {}
            result = fn(**args)
            return ToolResult(
                tool_call_id=call.id,
                function_name=call.function_name,
                content=str(result) if result is not None else "",
            )
        except Exception:
            return ToolResult(
                tool_call_id=call.id,
                function_name=call.function_name,
                content=f"Error: {traceback.format_exc()}",
            )

    def _inspect_function(self, fn: Callable) -> dict:
        name = fn.__name__
        doc = inspect.getdoc(fn) or ""
        summary, arg_descriptions = _parse_docstring(doc)

        try:
            sig = inspect.signature(fn)
        except (ValueError, TypeError):
            sig = inspect.Signature()

        try:
            hints = get_type_hints(fn)
        except Exception:
            hints = {}

        properties: dict[str, Any] = {}
        required: list[str] = []

        for param_name, param in sig.parameters.items():
            py_type = hints.get(param_name, str)
            json_type = _python_type_to_json(py_type)
            if isinstance(json_type, str):
                prop: dict[str, Any] = {"type": json_type}
            else:
                prop = json_type

            if param_name in arg_descriptions:
                prop["description"] = arg_descriptions[param_name]

            if param.default is inspect.Parameter.empty:
                required.append(param_name)
            else:
                prop["default"] = param.default

            properties[param_name] = prop

        return {
            "type": "function",
            "function": {
                "name": name,
                "description": summary,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    def _build_prompt_descriptions(self) -> str:
        if not self._schemas:
            return ""
        parts: list[str] = ["You have access to the following tools:\n"]
        for schema in self._schemas:
            fn = schema["function"]
            name = fn["name"]
            desc = fn.get("description", "")
            params = fn.get("parameters", {}).get("properties", {})
            required = fn.get("parameters", {}).get("required", [])

            param_parts: list[str] = []
            for pname, pval in params.items():
                ptype = pval.get("type", "any")
                is_req = "required" if pname in required else "optional"
                pdesc = pval.get("description", "")
                s = f"{pname}: {ptype}"
                if "default" in pval:
                    s += f" = {pval['default']}"
                s += f" ({is_req})"
                if pdesc:
                    s += f" — {pdesc}"
                param_parts.append(s)

            parts.append(f"## {name}({', '.join(param_parts)})")
            if desc:
                parts.append(f"{desc}\n")

        parts.append(
            'To call a tool, respond with a JSON block in this exact format:\n'
            '```json\n'
            '{"tool_call": {"name": "function_name", "arguments": {"arg1": "value1"}}}\n'
            '```\n'
            "After receiving the tool result, you may call another tool or provide your final answer."
        )
        return "\n".join(parts)
