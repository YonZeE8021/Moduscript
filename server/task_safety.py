"""Safe asyncio background task helpers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def spawn_task(
    coro: Coroutine[Any, Any, T],
    *,
    name: str,
    logger: logging.Logger | None = None,
) -> asyncio.Task[T]:
    """Create a background task and log unhandled exceptions."""
    log = logger or logging.getLogger(__name__)
    task = asyncio.create_task(coro, name=name)

    def _done(t: asyncio.Task[T]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.error("Background task %s failed: %s", name, exc, exc_info=exc)

    task.add_done_callback(_done)
    return task
