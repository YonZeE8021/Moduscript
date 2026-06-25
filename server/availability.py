"""Availability helpers: capacity limits and shared runtime state."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class SessionCapacityError(RuntimeError):
    """Raised when the server cannot accept another concurrent agent workload."""


class AgentCapacityLimiter:
    """Tracks concurrent agent workloads (setup, run, follow-up)."""

    def __init__(self, max_active: int) -> None:
        self._max_active = max_active
        self._active = 0
        self._lock = asyncio.Lock()

    def reconfigure(self, max_active: int) -> None:
        self._max_active = max_active
        self._active = 0

    @property
    def unlimited(self) -> bool:
        return self._max_active <= 0

    async def has_capacity(self) -> bool:
        if self.unlimited:
            return True
        async with self._lock:
            return self._active < self._max_active

    async def try_acquire(self) -> bool:
        if self.unlimited:
            return True
        async with self._lock:
            if self._active >= self._max_active:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        if self.unlimited:
            return
        async with self._lock:
            self._active = max(0, self._active - 1)

    def stats(self) -> dict[str, int]:
        return {
            "max_active": self._max_active,
            "active": self._active,
        }


class GradleCapacityLimiter:
    """Limits concurrent Gradle build subprocesses."""

    def __init__(self, max_concurrent: int) -> None:
        self._max_concurrent = max_concurrent
        self._semaphore: asyncio.Semaphore | None = (
            asyncio.Semaphore(max_concurrent) if max_concurrent > 0 else None
        )

    def reconfigure(self, max_concurrent: int) -> None:
        self._max_concurrent = max_concurrent
        self._semaphore = (
            asyncio.Semaphore(max_concurrent) if max_concurrent > 0 else None
        )

    async def acquire(self) -> None:
        if self._semaphore is not None:
            await self._semaphore.acquire()

    def release(self) -> None:
        if self._semaphore is not None:
            self._semaphore.release()

    def stats(self) -> dict[str, int]:
        if self._semaphore is None:
            return {"max_concurrent": 0, "available": -1}
        return {
            "max_concurrent": self._max_concurrent,
            "available": self._semaphore._value,  # noqa: SLF001 — observability only
        }


class RuntimeState:
    """Process-wide flags for health checks and graceful shutdown."""

    accepting_traffic: bool = True

    @classmethod
    def drain(cls) -> None:
        cls.accepting_traffic = False
        logger.info("RuntimeState: no longer accepting new agent workloads")


agent_capacity = AgentCapacityLimiter(0)
gradle_capacity = GradleCapacityLimiter(0)


def configure_capacity(*, max_active_sessions: int, max_gradle_builds: int) -> None:
    agent_capacity.reconfigure(max_active_sessions)
    gradle_capacity.reconfigure(max_gradle_builds)
    if max_active_sessions > 0:
        logger.info("Agent capacity limit: %s concurrent workloads", max_active_sessions)
    if max_gradle_builds > 0:
        logger.info("Gradle capacity limit: %s concurrent builds", max_gradle_builds)
