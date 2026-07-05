"""Simple publish/subscribe event bus for decoupled components."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

Handler = Callable[[dict[str, Any]], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, event: str, handler: Handler) -> None:
        self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        if handler in self._subscribers.get(event, []):
            self._subscribers[event].remove(handler)

    def publish(self, event: str, payload: dict[str, Any] | None = None) -> int:
        handlers = list(self._subscribers.get(event, []))
        for handler in handlers:
            try:
                handler(payload or {})
            except Exception:  # noqa: BLE001 - a bad subscriber must not break publishing
                continue
        return len(handlers)


bus = EventBus()
