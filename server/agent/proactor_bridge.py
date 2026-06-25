"""Run Claude Agent SDK on a dedicated Windows Proactor event loop thread."""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from queue import Empty, Queue
from typing import Any

from agent.runner import AgentSession
from asyncio_platform import configure_asyncio_for_platform, install_quiet_proactor_handler

logger = logging.getLogger(__name__)

MsgQueueItem = tuple[str, Any]


class AgentProactorBridge:
    """Streams SDK messages to the uvicorn loop while the agent runs on a worker loop."""

    def __init__(self, agent: AgentSession, main_loop: asyncio.AbstractEventLoop) -> None:
        self.agent = agent
        self.main_loop = main_loop
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._msg_queue: Queue[MsgQueueItem] = Queue()

    def resolve_permission(self, request_id: str, result: Any) -> bool:
        loop = self.worker_loop
        if loop is None or not loop.is_running():
            return self.agent.resolve_permission(request_id, result)

        fut: concurrent.futures.Future[bool] = concurrent.futures.Future()

        def _resolve() -> None:
            fut.set_result(self.agent.resolve_permission(request_id, result))

        loop.call_soon_threadsafe(_resolve)
        try:
            return fut.result(timeout=10.0)
        except concurrent.futures.TimeoutError:
            return False

    async def stream(
        self,
        prompt: str,
        on_permission: Callable[[str, dict[str, Any]], Awaitable[None]],
        *,
        follow_up: bool = False,
    ) -> AsyncIterator[Any]:
        if sys.platform != "win32":
            stream = self.agent.send_follow_up(prompt) if follow_up else self.agent.send_prompt(prompt)
            async for message in stream:
                yield message
            return

        async def bridged_permission(req_id: str, pending: dict[str, Any]) -> None:
            fut = asyncio.run_coroutine_threadsafe(on_permission(req_id, pending), self.main_loop)
            await asyncio.wrap_future(fut)

        self.agent.on_permission_request = bridged_permission

        self._thread = threading.Thread(
            target=self._worker_run,
            args=(prompt, follow_up),
            name=f"agent-{self.agent.session_id}",
            daemon=True,
        )
        self._thread.start()

        try:
            while True:
                try:
                    kind, payload = self._msg_queue.get(timeout=0.05)
                except Empty:
                    if self._thread and not self._thread.is_alive() and self._msg_queue.empty():
                        break
                    await asyncio.sleep(0.02)
                    continue

                if kind == "msg":
                    yield payload
                elif kind == "done":
                    return
                elif kind == "error":
                    raise payload
        finally:
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=30.0)

    def _worker_run(self, prompt: str, follow_up: bool) -> None:
        configure_asyncio_for_platform()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        install_quiet_proactor_handler(loop)
        self.worker_loop = loop

        async def _run() -> None:
            try:
                stream = (
                    self.agent.send_follow_up(prompt)
                    if follow_up
                    else self.agent.send_prompt(prompt)
                )
                async for message in stream:
                    self._msg_queue.put(("msg", message))
                self._msg_queue.put(("done", None))
            except Exception as exc:
                logger.exception("Agent worker failed session=%s", self.agent.session_id)
                self._msg_queue.put(("error", exc))
            finally:
                try:
                    await self.agent.close()
                except (OSError, FileNotFoundError) as exc:
                    logger.warning("Agent close in worker (ignored): %s", exc)
                except Exception as exc:
                    logger.warning("Agent close in worker: %s", exc)

        try:
            loop.run_until_complete(_run())
        finally:
            loop.close()
            self.worker_loop = None


async def stream_agent_prompt(
    agent: AgentSession,
    prompt: str,
    *,
    on_permission: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> AsyncIterator[Any]:
    main_loop = asyncio.get_running_loop()
    bridge = AgentProactorBridge(agent, main_loop)
    agent._proactor_bridge = bridge  # type: ignore[attr-defined]
    try:
        async for message in bridge.stream(prompt, on_permission, follow_up=False):
            yield message
    finally:
        agent._proactor_bridge = None  # type: ignore[attr-defined]


async def stream_agent_follow_up(
    agent: AgentSession,
    content: str,
) -> AsyncIterator[Any]:
    async def _noop(_req_id: str, _pending: dict[str, Any]) -> None:
        return None

    main_loop = asyncio.get_running_loop()
    bridge = AgentProactorBridge(agent, main_loop)
    agent._proactor_bridge = bridge  # type: ignore[attr-defined]
    try:
        async for message in bridge.stream(content, _noop, follow_up=True):
            yield message
    finally:
        agent._proactor_bridge = None  # type: ignore[attr-defined]


def resolve_agent_permission(agent: AgentSession | None, request_id: str, result: Any) -> bool:
    if agent is None:
        return False
    bridge = getattr(agent, "_proactor_bridge", None)
    if bridge is not None:
        return bridge.resolve_permission(request_id, result)
    return agent.resolve_permission(request_id, result)
