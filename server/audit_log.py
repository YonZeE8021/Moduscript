"""Structured operation audit log (JSONL + logging)."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import AUDIT_JSONL_ENABLED, AUDIT_RETENTION_DAYS, LOG_DIR

logger = logging.getLogger("audit")

_audit_lock = threading.Lock()
_audit_path = LOG_DIR / "audit.jsonl"

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "password",
        "password_hash",
        "token",
        "authorization",
        "prompt",
        "readable_blueprint",
        "final_prompt",
        "blueprint",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    if not detail:
        return {}
    out: dict[str, Any] = {}
    for key, value in detail.items():
        lower = key.lower()
        if lower in _SENSITIVE_KEYS or "secret" in lower or lower.endswith("_key"):
            if isinstance(value, str) and value:
                out[f"{key}_present"] = True
                out[f"{key}_len"] = len(value)
            else:
                out[f"{key}_present"] = bool(value)
            continue
        if isinstance(value, dict):
            out[key] = sanitize_detail(value)
        elif isinstance(value, list):
            out[key] = [
                sanitize_detail(v) if isinstance(v, dict) else v for v in value[:20]
            ]
        else:
            out[key] = value
    return out


def _append_jsonl(event: dict[str, Any]) -> None:
    if not AUDIT_JSONL_ENABLED:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with _audit_lock:
        with _audit_path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def record(
    category: str,
    action: str,
    *,
    level: str = "info",
    user_id: str | None = None,
    session_id: str | None = None,
    message: str = "",
    detail: dict[str, Any] | None = None,
) -> None:
    event = {
        "ts": _utc_now(),
        "level": level,
        "category": category,
        "action": action,
        "user_id": user_id,
        "session_id": session_id,
        "message": message,
        "detail": sanitize_detail(detail),
    }
    log_line = f"{category}.{action} user={user_id or '-'} session={session_id or '-'} {message}"
    if level == "error":
        logger.error(log_line)
    elif level == "warn":
        logger.warning(log_line)
    else:
        logger.info(log_line)
    _append_jsonl(event)


def cleanup_old_logs() -> None:
    if AUDIT_RETENTION_DAYS <= 0:
        return
    if not LOG_DIR.is_dir():
        return
    cutoff = datetime.now(timezone.utc).timestamp() - AUDIT_RETENTION_DAYS * 86400
    for path in LOG_DIR.glob("audit*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def _matches_filters(
    event: dict[str, Any],
    *,
    category: str | None,
    action: str | None,
    user_id: str | None,
    session_id: str | None,
    level: str | None,
) -> bool:
    if category and event.get("category") != category:
        return False
    if action and event.get("action") != action:
        return False
    if user_id and event.get("user_id") != user_id:
        return False
    if session_id and event.get("session_id") != session_id:
        return False
    if level and event.get("level") != level:
        return False
    return True


def query_logs(
    *,
    limit: int = 50,
    offset: int = 0,
    category: str | None = None,
    action: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    level: str | None = None,
    max_read_lines: int = 10000,
) -> tuple[list[dict[str, Any]], int]:
    """Return matching events newest-first and total match count (within read window)."""
    if not _audit_path.is_file():
        return [], 0

    with _audit_lock:
        lines = _audit_path.read_text(encoding="utf-8").splitlines()

    if len(lines) > max_read_lines:
        lines = lines[-max_read_lines:]

    events: list[dict[str, Any]] = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not _matches_filters(
            event,
            category=category,
            action=action,
            user_id=user_id,
            session_id=session_id,
            level=level,
        ):
            continue
        events.append(event)

    total = len(events)
    page = events[offset : offset + limit]
    return page, total
