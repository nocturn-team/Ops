"""SharedContext — cross-agent data store for the DevOps pipeline.

Acts as a key-value store with typed accessors.  Every agent reads/writes
through this store so the Orchestrator can persist and inspect state at
any point in the pipeline.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any, TypeVar

from .types import PipelineRun

T = TypeVar("T")


class SharedContext:
    """Thread-safe shared state for a single pipeline run.

    Wraps a ``PipelineRun`` and provides helpers for serialisation and
    cross-agent data sharing.

    Example::

        ctx = SharedContext()
        ctx.run.bug_report = bug_report
        ctx.set("custom_key", {"extra": "data"})
        ctx.save("state.json")
    """

    def __init__(self, run: PipelineRun | None = None) -> None:
        self._lock = threading.Lock()
        self.run = run or PipelineRun()
        self._extras: dict[str, Any] = {}

    # -- typed accessors for extra data ------------------------------------

    def set(self, key: str, value: Any) -> None:
        """Store an arbitrary value in the shared context."""
        with self._lock:
            self._extras[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from the shared context."""
        with self._lock:
            return self._extras.get(key, default)

    def delete(self, key: str) -> None:
        """Remove a key from the shared context."""
        with self._lock:
            self._extras.pop(key, None)

    # -- pipeline run shortcuts --------------------------------------------

    @property
    def trace_id(self) -> str:
        return self.run.trace_id

    def transition(self, new_state: "PipelineState") -> None:  # noqa: F821
        """Transition the pipeline to a new state (thread-safe)."""
        from .types import PipelineState, _now

        with self._lock:
            old = self.run.state
            self.run.state = new_state
            self.run.updated_at = _now()
            self._extras.setdefault("state_history", []).append(
                {"from": old.value, "to": new_state.value, "at": self.run.updated_at}
            )

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialise the full context (run + extras) to a plain dict."""
        run_dict = self._serialise_dataclass(self.run)
        return {"run": run_dict, "extras": self._extras}

    def save(self, path: str | Path) -> None:
        """Persist context to a JSON file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False, default=str)

    @classmethod
    def load(cls, path: str | Path) -> SharedContext:
        """Load context from a previously saved JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ctx = cls()
        # Restore extras directly
        ctx._extras = data.get("extras", {})
        # Run is restored as a raw dict — full deserialisation is left to
        # the caller if needed (dataclass reconstruction is non-trivial for
        # nested types with enums).  For inspection / resumption the dict
        # is sufficient.
        ctx.set("_raw_run", data.get("run", {}))
        return ctx

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _serialise_dataclass(obj: Any) -> Any:
        """Recursively convert dataclasses / enums to JSON-friendly dicts."""
        if hasattr(obj, "__dataclass_fields__"):
            return {
                f.name: SharedContext._serialise_dataclass(getattr(obj, f.name))
                for f in fields(obj)
            }
        if isinstance(obj, list):
            return [SharedContext._serialise_dataclass(item) for item in obj]
        if isinstance(obj, dict):
            return {k: SharedContext._serialise_dataclass(v) for k, v in obj.items()}
        if hasattr(obj, "value"):  # Enum
            return obj.value
        return obj
