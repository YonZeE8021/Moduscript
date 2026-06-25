"""Workspace path safety, Gradle build, and read-only file browser helpers."""

from __future__ import annotations

import io
import logging
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.options import is_under_workspace
from config import GRADLE_BUILD_TIMEOUT_SEC
from mod_bootstrap import has_gradlew

logger = logging.getLogger(__name__)

MAX_DIR_ENTRIES = 500
MAX_TEXT_BYTES = 512 * 1024
MAX_ARCHIVE_FILES = 2000
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
ARCHIVE_SKIP_DIRS = {".gradle", ".git", "build", "node_modules", "__pycache__"}

def resolve_workspace_rel_path(workspace: Path, rel_path: str) -> Path:
    workspace = workspace.expanduser().resolve()
    rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
    if rel == ".":
        rel = ""
    parts = [p for p in rel.split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ValueError("invalid path")
    target = (workspace / Path(*parts)).resolve() if parts else workspace
    if not is_under_workspace(target, workspace):
        raise ValueError("invalid path")
    return target


def _tail_text(text: str, max_len: int = 800) -> str:
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return "…" + text[-max_len:]


def run_gradlew_build(workspace: Path) -> dict[str, Any]:
    """Run gradlew build in workspace (blocking)."""
    workspace = workspace.expanduser().resolve()
    if not has_gradlew(workspace):
        raise ValueError("工作区未找到 gradlew，无法编译")

    gradlew_cmd = "gradlew.bat" if sys.platform == "win32" else "./gradlew"
    logger.info("Running %s build in %s", gradlew_cmd, workspace)
    try:
        proc = subprocess.run(
            [gradlew_cmd, "build", "--no-daemon"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=GRADLE_BUILD_TIMEOUT_SEC,
            shell=sys.platform == "win32",
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            f"gradlew build 超时（超过 {GRADLE_BUILD_TIMEOUT_SEC} 秒）"
        ) from exc

    ok = proc.returncode == 0
    stderr = _tail_text(proc.stderr or "")
    stdout = _tail_text(proc.stdout or "")
    message = "编译成功" if ok else (stderr or stdout or f"gradlew build 失败（退出码 {proc.returncode}）")
    return {
        "ok": ok,
        "message": message,
        "returncode": proc.returncode,
    }


def _entry_mtime(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except OSError:
        return None


def list_workspace_entries(workspace: Path, rel_path: str = "") -> dict[str, Any]:
    target = resolve_workspace_rel_path(workspace, rel_path)
    if not target.is_dir():
        raise ValueError("not a directory")

    entries: list[dict[str, Any]] = []
    try:
        children = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError as exc:
        raise ValueError(f"无法读取目录：{exc}") from exc

    for child in children[:MAX_DIR_ENTRIES]:
        try:
            st = child.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": st.st_size if child.is_file() else None,
                "modified": _entry_mtime(child),
            }
        )

    display_path = rel_path.strip().replace("\\", "/").lstrip("/") or "."
    truncated = len(children) > MAX_DIR_ENTRIES
    return {
        "path": display_path,
        "entries": entries,
        "truncated": truncated,
    }


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return True
    try:
        data[:8192].decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def read_workspace_file(workspace: Path, rel_path: str) -> dict[str, Any]:
    target = resolve_workspace_rel_path(workspace, rel_path)
    if not target.is_file():
        raise ValueError("not a file")

    size = target.stat().st_size
    if size > MAX_TEXT_BYTES:
        raise ValueError(f"文件过大（{size} 字节），请使用下载")

    data = target.read_bytes()
    if _looks_binary(data):
        raise ValueError("二进制文件不支持预览，请使用下载")

    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = data.decode("utf-8", errors="replace")

    return {
        "path": rel_path.strip().replace("\\", "/").lstrip("/"),
        "content": content,
        "size": size,
        "truncated": False,
    }


def iter_archive_files(root: Path, base: Path) -> list[tuple[Path, str]]:
    collected: list[tuple[Path, str]] = []
    total_bytes = 0

    def walk(current: Path) -> None:
        nonlocal total_bytes
        if len(collected) >= MAX_ARCHIVE_FILES:
            return
        try:
            items = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for item in items:
            if len(collected) >= MAX_ARCHIVE_FILES:
                return
            rel = item.relative_to(base).as_posix()
            if item.is_dir():
                if item.name in ARCHIVE_SKIP_DIRS:
                    continue
                walk(item)
            elif item.is_file():
                try:
                    sz = item.stat().st_size
                except OSError:
                    continue
                if total_bytes + sz > MAX_ARCHIVE_BYTES:
                    continue
                total_bytes += sz
                collected.append((item, rel))

    walk(root)
    return collected


def build_workspace_zip(workspace: Path, rel_path: str) -> tuple[io.BytesIO, str]:
    target = resolve_workspace_rel_path(workspace, rel_path)
    if not target.exists():
        raise ValueError("path not found")

    buf = io.BytesIO()
    base = target if target.is_dir() else target.parent
    archive_name = target.name if target.is_dir() else f"{target.stem}.zip"

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if target.is_file():
            zf.write(target, arcname=target.name)
        else:
            files = iter_archive_files(target, target)
            if not files:
                raise ValueError("目录为空或超出打包限制")
            for file_path, arc_rel in files:
                zf.write(file_path, arcname=arc_rel)

    buf.seek(0)
    if target.is_dir():
        archive_name = f"{target.name}.zip"
    return buf, archive_name
