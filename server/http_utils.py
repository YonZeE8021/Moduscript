"""HTTP response helpers."""

from __future__ import annotations

import re
from urllib.parse import quote


def content_disposition_attachment(filename: str, *, fallback: str = "mod.jar") -> str:
    """Build a latin-1-safe Content-Disposition header with optional UTF-8 filename."""
    safe = filename or fallback
    try:
        safe.encode("latin-1")
        return f'attachment; filename="{safe}"'
    except UnicodeEncodeError:
        ascii_name = safe.encode("ascii", "ignore").decode("ascii").strip() or fallback
        encoded = quote(safe, safe="")
        return f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'


def artifact_slug(
    task_title: str,
    *,
    payload: dict | None = None,
    session_id: str = "",
) -> str:
    """Return an ASCII-safe base name for artifact files."""
    mod_id = (payload or {}).get("mod_id") or ""
    if mod_id:
        cleaned = re.sub(r"[^\w\-]+", "", str(mod_id).lower()).strip("-_")[:40]
        if cleaned:
            return cleaned

    sid = re.sub(r"^sess-", "", session_id or "")[:12]
    if sid:
        return sid

    ascii_title = (
        re.sub(r"[^\w\-]+", "_", task_title.encode("ascii", "ignore").decode("ascii"))
        .strip("_")[:40]
    )
    return ascii_title or "mod"
