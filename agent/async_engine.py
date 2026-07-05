"""Asynchronous engine for fast, concurrent operations.

Provides an async task runner with bounded concurrency, gather-with-timeout
helpers, and an async retry wrapper. Lets the agent run many I/O-bound calls
(tools, HTTP requests) in parallel.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Iterable, TypeVar

T = TypeVar("T")


async def run_concurrent(
    coros: Iterable[Awaitable[T]], limit: int = 8
) -> list[T]:
    """Run awaitables concurrently with a bounded semaphore."""
    semaphore = asyncio.Semaphore(limit)

    async def _guarded(coro: Awaitable[T]) -> T:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(_guarded(c) for c in coros))


async def gather_with_timeout(
    coros: Iterable[Awaitable[T]], timeout: float
) -> list[T | None]:
    """Gather results, returning None for any that time out."""
    async def _safe(coro: Awaitable[T]) -> T | None:
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            return None

    return await asyncio.gather(*(_safe(c) for c in coros))


async def async_retry(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    attempts: int = 3,
    base_delay: float = 0.5,
    **kwargs: Any,
) -> T:
    """Retry an async callable with exponential backoff."""
    delay = base_delay
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == attempts:
                break
            await asyncio.sleep(delay)
            delay *= 2
    assert last_exc is not None
    raise last_exc


class AsyncTaskRunner:
    """Schedules and runs async background tasks, tracking their results."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()

    def spawn(self, coro: Awaitable[Any]) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self) -> list[Any]:
        """Wait for all spawned tasks to finish and return their results."""
        if not self._tasks:
            return []
        return await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def pending(self) -> int:
        return len(self._tasks)
