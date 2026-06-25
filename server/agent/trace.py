"""Structured Agent execution trace (JSONL)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_log import sanitize_detail
from config import (
    AGENT_TRACE_ENABLED,
    AGENT_TRACE_LOG_DELTAS,
    AGENT_TRACE_PATH,
    AGENT_TRACE_PREVIEW_LEN,
)

logger = logging.getLogger(__name__)

_lock = threading.Lock()

_SENSITIVE_KEYS = frozenset({"prompt", "readable_blueprint", "final_prompt", "blueprint"})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return {}
    cleaned = sanitize_detail(detail)
    out: dict[str, Any] = {}
    for key, value in cleaned.items():
        if key in _SENSITIVE_KEYS:
            continue
        if isinstance(value, str) and len(value) > AGENT_TRACE_PREVIEW_LEN:
            out[key] = value[:AGENT_TRACE_PREVIEW_LEN] + "…"
        else:
            out[key] = value
    return out


def trace_event(
    session_id: str,
    event: str,
    *,
    owner_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    if not AGENT_TRACE_ENABLED:
        return
    if event.endswith(".delta") and not AGENT_TRACE_LOG_DELTAS:
        return

    row = {
        "ts": _utc_now(),
        "session_id": session_id,
        "owner_id": owner_id,
        "event": event,
        "detail": _truncate_detail(detail),
    }
    AGENT_TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False) + "\n"
    with _lock:
        with AGENT_TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line)
    logger.info(
        "agent_trace session=%s event=%s detail_keys=%s",
        session_id,
        event,
        list(row["detail"].keys()),
    )
