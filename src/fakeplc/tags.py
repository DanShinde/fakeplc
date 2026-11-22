"""The tag table: current values plus a journal of client writes."""

from __future__ import annotations

import builtins
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import PLCConfig, TagSpec
from .types import coerce

__all__ = ["TagTable", "UnknownTagError", "Write"]


class UnknownTagError(KeyError):
    """Raised for a tag name that is not in the configuration."""

    def __str__(self) -> str:
        return f"unknown tag {self.args[0]!r}"


@dataclass(frozen=True)
class Write:
    """One write made by a client (OPC UA or Modbus) to a tag."""

    tag: str
    value: Any
    protocol: str
    timestamp: datetime
    scan: int


class TagTable:
    """Thread safe store of tag values, shared by every protocol."""

    def __init__(self, config: PLCConfig) -> None:
        self.config = config
        self.specs: dict[str, TagSpec] = config.tags
        self._lock = threading.RLock()
        self._values: dict[str, Any] = {}
        self._journal: list[Write] = []
        self._dirty: set[str] = set()
        self._changed = threading.Condition(self._lock)
        self.scan_count = 0
        self.reset()

    def __contains__(self, name: object) -> bool:
        return name in self.specs

    def __iter__(self) -> Iterator[str]:
        return iter(self.specs)

    def spec(self, name: str) -> TagSpec:
        try:
            return self.specs[name]
        except KeyError:
            raise UnknownTagError(name) from None

    def reset(self) -> None:
        """Restore start values and clear the journal."""
        with self._lock:
            self._values = {name: spec.value for name, spec in self.specs.items()}
            self._journal.clear()
            self._dirty = set(self.specs)
            self.scan_count = 0
            self._changed.notify_all()

    def get(self, name: str) -> Any:
        spec = self.spec(name)
        with self._lock:
            return self._values[spec.name]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def set(self, name: str, value: Any, protocol: str | None = None, dirty: bool = True) -> Any:
        """Store a value. ``protocol`` records the write in the journal."""
        spec = self.spec(name)
        value = coerce(spec.type, value)
        with self._lock:
            self._values[name] = value
            if dirty:
                self._dirty.add(name)
            if protocol is not None:
                self._journal.append(Write(name, value, protocol, datetime.now(timezone.utc), self.scan_count))
            self._changed.notify_all()
        return value

    def set_many(self, values: dict[str, Any], protocol: str | None = None) -> None:
        coerced = {name: coerce(self.spec(name).type, value) for name, value in values.items()}
        with self._lock:
            for name, value in coerced.items():
                self.set(name, value, protocol)

    def take_dirty(self) -> builtins.set[str]:
        with self._lock:
            dirty, self._dirty = self._dirty, set()
            return dirty

    def journal(self, tag: str | None = None, protocol: str | None = None) -> list[Write]:
        if tag is not None:
            self.spec(tag)
        with self._lock:
            return [
                w
                for w in self._journal
                if (tag is None or w.tag == tag) and (protocol is None or w.protocol == protocol)
            ]

    def clear_journal(self) -> None:
        with self._lock:
            self._journal.clear()

    def wait(self, predicate: Any, timeout: float) -> bool:
        """Block until ``predicate()`` is true or the timeout passes."""
        deadline = time.monotonic() + timeout
        with self._lock:
            while not predicate():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._changed.wait(min(remaining, 0.05))
            return True
