"""
Structured, run-aware logging.

Every pipeline node logs a start/complete/failure event tagged with the
run_id so that a run's full execution trace can be reconstructed from logs
alone. Logs are emitted both as human-readable text and (optionally)
collected in-memory per run for exposure via the API.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any

_LOGGER = logging.getLogger("cloud_pilot")


def configure_logging(level: str = "INFO") -> None:
    if _LOGGER.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _LOGGER.addHandler(handler)
    _LOGGER.setLevel(level.upper())
    _LOGGER.propagate = False


@dataclass
class LogEvent:
    run_id: str
    node: str
    status: str
    message: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "node": self.node,
            "status": self.status,
            "message": self.message,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }


class RunLogStore:
    """In-memory, thread-safe store of log events keyed by run_id."""

    def __init__(self) -> None:
        self._events: dict[str, list[LogEvent]] = defaultdict(list)
        self._lock = Lock()

    def add(self, event: LogEvent) -> None:
        with self._lock:
            self._events[event.run_id].append(event)
        line = f"[run={event.run_id}] {event.node} {event.status}: {event.message}"
        _LOGGER.info(json.dumps(event.to_dict()) if False else line)

    def get(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._events.get(run_id, [])]


run_log_store = RunLogStore()


class NodeTimer:
    """Context manager that logs start/success/failure with duration for a node."""

    def __init__(self, run_id: str, node: str) -> None:
        self.run_id = run_id
        self.node = node
        self._start = 0.0

    def __enter__(self) -> "NodeTimer":
        self._start = time.perf_counter()
        run_log_store.add(LogEvent(self.run_id, self.node, "started", f"{self.node} started"))
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        duration_ms = (time.perf_counter() - self._start) * 1000
        if exc_type is None:
            run_log_store.add(
                LogEvent(self.run_id, self.node, "completed", f"{self.node} completed", duration_ms=duration_ms)
            )
        else:
            run_log_store.add(
                LogEvent(
                    self.run_id,
                    self.node,
                    "failed",
                    f"{self.node} raised {exc_type.__name__}: {exc}",
                    duration_ms=duration_ms,
                )
            )
        # Do not swallow the exception.
        return False

    def info(self, message: str) -> None:
        run_log_store.add(LogEvent(self.run_id, self.node, "info", message))
