"""Thread-safe JSON file I/O with optional locking."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

_locks: dict[str, threading.Lock] = {}
_global_lock = threading.Lock()


def _get_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _global_lock:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file():
        return default
    with _get_lock(path):
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return default
        return json.loads(text)


def write_json(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    last_exc: OSError | None = None
    with _get_lock(path):
        for attempt in range(3):
            tmp = path.with_suffix(path.suffix + ".tmp")
            try:
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(path)
                return
            except PermissionError as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise
            except OSError as exc:
                last_exc = exc
                raise
    if last_exc is not None:
        raise last_exc


def list_json_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))
