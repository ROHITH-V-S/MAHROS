"""Discrete-event simulation clock.

A plain binary-heap event queue. Time is in *simulation minutes* from t=0.
Ties are broken by insertion order so runs are fully reproducible.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(order=True)
class _Entry:
    time: float
    seq: int
    kind: str = field(compare=False)
    payload: Any = field(compare=False, default=None)


class EventQueue:
    def __init__(self) -> None:
        self._heap: list[_Entry] = []
        self._counter = itertools.count()
        self.now: float = 0.0

    def schedule(self, delay: float, kind: str, payload: Any = None) -> None:
        """Schedule `kind` to fire `delay` minutes from now."""
        if delay < 0:
            raise ValueError(f"negative delay {delay} for event {kind}")
        heapq.heappush(
            self._heap, _Entry(self.now + delay, next(self._counter), kind, payload)
        )

    def schedule_at(self, time: float, kind: str, payload: Any = None) -> None:
        heapq.heappush(
            self._heap, _Entry(max(time, self.now), next(self._counter), kind, payload)
        )

    def pop(self) -> _Entry | None:
        if not self._heap:
            return None
        entry = heapq.heappop(self._heap)
        self.now = entry.time
        return entry

    def __len__(self) -> int:
        return len(self._heap)


class Simulator:
    """Minimal event loop. Handlers are registered per event kind."""

    def __init__(self, horizon_minutes: float) -> None:
        self.queue = EventQueue()
        self.horizon = horizon_minutes
        self._handlers: dict[str, Callable[[float, Any], None]] = {}
        self.events_processed = 0

    @property
    def now(self) -> float:
        return self.queue.now

    def on(self, kind: str, handler: Callable[[float, Any], None]) -> None:
        self._handlers[kind] = handler

    def schedule(self, delay: float, kind: str, payload: Any = None) -> None:
        self.queue.schedule(delay, kind, payload)

    def schedule_at(self, time: float, kind: str, payload: Any = None) -> None:
        self.queue.schedule_at(time, kind, payload)

    def run(self) -> None:
        while True:
            entry = self.queue.pop()
            if entry is None or entry.time > self.horizon:
                break
            handler = self._handlers.get(entry.kind)
            if handler is None:
                raise KeyError(f"no handler registered for event kind {entry.kind!r}")
            handler(entry.time, entry.payload)
            self.events_processed += 1
