"""Platform-specific asyncio configuration (Windows subprocess support)."""

from __future__ import annotations

import asyncio
import logging
import sys

logger = logging.getLogger(__name__)

_PROACTOR_TEARDOWN_ERRORS = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


def quiet_proactor_exception_handler(
    loop: asyncio.AbstractEventLoop,
    context: dict,
) -> None:
    exc = context.get("exception")
    if isinstance(exc, _PROACTOR_TEARDOWN_ERRORS):
        logger.debug("Ignored proactor teardown: %s", exc)
        return
    loop.default_exception_handler(context)


def install_quiet_proactor_handler(loop: asyncio.AbstractEventLoop | None = None) -> None:
    """Suppress benign Windows pipe teardown errors in asyncio callbacks."""
    if sys.platform != "win32":
        return
    target = loop
    if target is None:
        try:
            target = asyncio.get_running_loop()
        except RuntimeError:
            return
    target.set_exception_handler(quiet_proactor_exception_handler)


def configure_asyncio_for_platform() -> None:
    """Use ProactorEventLoop on Windows so asyncio subprocess works with Claude SDK."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
