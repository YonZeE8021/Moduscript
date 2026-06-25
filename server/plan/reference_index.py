"""Clone, index, search and read reference mod sources for plan mode."""

from __future__ import annotations

import io
import json
import logging
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

RefScope = Literal["plan", "session"]

import httpx

from config import USERS_DIR
from plan.reference_config import (
    CLONE_TIMEOUT_SEC,
    ENTRY_POINT_MAX,
    INDEX_MAX_FILES,
    KEY_FILENAMES,
    MAX_SEARCH_HITS,
    SKIP_DIR_NAMES,
    SNIPPET_MAX_BYTES,
    SNIPPET_MAX_LINES,
)
from storage.file_io import ensure_dir, read_json, write_json

logger = logging.getLogger(__name__)

RefStepCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _emit_ref_step(on_step: RefStepCallback | None, data: dict[str, Any]) -> None:
    if not on_step:
        return
    result = on_step(data)
    if result is not None:
        await result

TEXT_EXTENSIONS = frozenset(
    {
        ".java",
        ".kt",
        ".kts",
        ".json",
        ".toml",
        ".gradle",
        ".properties",
        ".md",
        ".txt",
        ".xml",
        ".yml",
        ".yaml",
        ".mcmeta",
    }
)

ENTRY_HINTS = re.compile(
    r"@Mod\b|ModInitializer|DedicatedServerModInitializer|"
    r"implements\s+ModInitializer|@Mod\.Entrypoint",
    re.IGNORECASE,
)

GITHUB_REPO_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/#?]+)", re.IGNORECASE)


def parse_github_repo(source_url: str) -> tuple[str, str] | None:
    url = source_url.strip().rstrip("/")
    match = GITHUB_REPO_RE.match(url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def clone_github_archive(source_url: str, dest: Path) -> None:
    parsed = parse_github_repo(source_url)
    if not parsed:
        raise ValueError("not a GitHub repository URL")
    owner, repo = parsed
    last_err = "github archive download failed"
    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        for branch in ("main", "master"):
            zip_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
            try:
                res = client.get(zip_url)
                if res.status_code != 200:
                    last_err = f"HTTP {res.status_code} for branch {branch}"
                    continue
                with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
                    root_prefix = None
                    for name in zf.namelist():
                        if "/" in name:
                            root_prefix = name.split("/", 1)[0]
                            break
                    if not root_prefix:
                        raise RuntimeError("empty GitHub archive")
                    ensure_dir(dest)
                    for name in zf.namelist():
                        if name.endswith("/") or not name.startswith(f"{root_prefix}/"):
                            continue
                        rel = name[len(root_prefix) + 1 :]
                        if not rel:
                            continue
                        target = dest / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(zf.read(name))
                return
            except Exception as exc:
                last_err = str(exc)
    raise RuntimeError(last_err[:500])


def refs_base_dir(user_id: str, scope_id: str, *, scope: RefScope = "plan") -> Path:
    sub = "plans" if scope == "plan" else "sessions"
    return USERS_DIR / user_id / sub / "refs" / scope_id


def ref_project_dir(
    user_id: str, scope_id: str, project_id: str, *, scope: RefScope = "plan"
) -> Path:
    return refs_base_dir(user_id, scope_id, scope=scope) / project_id


def _resolve_repo_path(
    user_id: str, scope_id: str, project_id: str, *, scope: RefScope = "plan"
) -> Path:
    return ref_project_dir(user_id, scope_id, project_id, scope=scope) / "repo"


def _resolve_in_repo(repo: Path, rel_path: str) -> Path:
    rel = rel_path.strip().replace("\\", "/").lstrip("/")
    target = (repo / rel).resolve()
    repo_resolved = repo.resolve()
    if not str(target).startswith(str(repo_resolved)):
        raise ValueError("invalid path")
    return target


def clone_reference_repo(source_url: str, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    ensure_dir(dest.parent)
    url = source_url.strip().rstrip("/")

    if parse_github_repo(url):
        try:
            clone_github_archive(url, dest)
            return
        except Exception as zip_exc:
            logger.warning("GitHub zip download failed for %s: %s", url, zip_exc)

    cmd = ["git", "clone", "--depth", "1", "--single-branch", url, str(dest)]
    if sys.platform == "win32":
        cmd = ["git", "-c", "http.sslBackend=schannel", *cmd[1:]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CLONE_TIMEOUT_SEC)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "未找到 git 可执行文件；请安装 Git，或改用 GitHub 源 URL（支持 zip 下载）"
        ) from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "git clone failed").strip()
        raise RuntimeError(err[:500])


def build_index(repo_path: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    entry_points: list[str] = []
    key_files: list[str] = []

    if not repo_path.is_dir():
        return {"files": [], "entry_points": [], "key_files": [], "file_count": 0}

    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_path).as_posix()
        parts = rel.split("/")
        if any(p in SKIP_DIR_NAMES for p in parts):
            continue
        if len(files) >= INDEX_MAX_FILES:
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 1_000_000:
            continue
        suffix = path.suffix.lower()
        if path.name in KEY_FILENAMES:
            key_files.append(rel)
        if suffix in TEXT_EXTENSIONS:
            files.append({"path": rel, "size": size})
            if suffix in (".java", ".kt") and len(entry_points) < ENTRY_POINT_MAX:
                try:
                    head = path.read_text(encoding="utf-8", errors="ignore")[:4000]
                    if ENTRY_HINTS.search(head):
                        entry_points.append(rel)
                except OSError:
                    pass

    return {
        "files": files,
        "entry_points": entry_points[:ENTRY_POINT_MAX],
        "key_files": key_files[:30],
        "file_count": len(files),
    }


def save_index(
    user_id: str,
    scope_id: str,
    project_id: str,
    index: dict[str, Any],
    *,
    scope: RefScope = "plan",
) -> None:
    base = ref_project_dir(user_id, scope_id, project_id, scope=scope)
    ensure_dir(base)
    write_json(base / "index.json", index)


def load_index(
    user_id: str, scope_id: str, project_id: str, *, scope: RefScope = "plan"
) -> dict[str, Any] | None:
    path = ref_project_dir(user_id, scope_id, project_id, scope=scope) / "index.json"
    return read_json(path)


def search_reference(
    user_id: str,
    scope_id: str,
    project_id: str,
    query: str,
    *,
    scope: RefScope = "plan",
) -> list[dict[str, Any]]:
    repo = _resolve_repo_path(user_id, scope_id, project_id, scope=scope)
    if not repo.is_dir():
        return []
    q = query.strip().lower()
    if not q:
        return []
    hits: list[dict[str, Any]] = []
    terms = [t for t in re.split(r"\s+", q) if t]

    for path in repo.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        if any(p in SKIP_DIR_NAMES for p in path.relative_to(repo).parts):
            continue
        try:
            if path.stat().st_size > 200_000:
                continue
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        rel = path.relative_to(repo).as_posix()
        for i, line in enumerate(lines, 1):
            lower = line.lower()
            if not all(t in lower for t in terms):
                continue
            ctx_start = max(0, i - 2)
            ctx_end = min(len(lines), i + 2)
            context = "\n".join(lines[ctx_start:ctx_end])
            hits.append({"path": rel, "line": i, "context": context})
            if len(hits) >= MAX_SEARCH_HITS:
                return hits
    return hits


def read_snippet(
    user_id: str,
    scope_id: str,
    project_id: str,
    path: str,
    *,
    start: int = 1,
    end: int | None = None,
    scope: RefScope = "plan",
) -> dict[str, Any]:
    repo = _resolve_repo_path(user_id, scope_id, project_id, scope=scope)
    target = _resolve_in_repo(repo, path)
    if not target.is_file():
        raise ValueError("file not found")
    size = target.stat().st_size
    if size > SNIPPET_MAX_BYTES * 4:
        raise ValueError("file too large")
    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    s = max(1, start)
    e = end if end is not None else s + SNIPPET_MAX_LINES - 1
    e = min(len(lines), e)
    if e - s + 1 > SNIPPET_MAX_LINES:
        e = s + SNIPPET_MAX_LINES - 1
    snippet = "\n".join(lines[s - 1 : e])
    if len(snippet.encode("utf-8")) > SNIPPET_MAX_BYTES:
        snippet = snippet.encode("utf-8")[:SNIPPET_MAX_BYTES].decode("utf-8", errors="ignore")
    return {
        "path": path,
        "start": s,
        "end": e,
        "content": snippet,
    }


async def materialize_reference(
    user_id: str,
    scope_id: str,
    ref_item: dict[str, Any],
    *,
    scope: RefScope = "plan",
    plan_context: dict[str, Any] | None = None,
    on_step: RefStepCallback | None = None,
) -> dict[str, Any]:
    """Clone repo or decompile jar and build index. Returns status dict."""
    project_id = ref_item.get("project_id") or ref_item.get("slug") or "unknown"
    title = ref_item.get("title") or project_id
    source_url = (ref_item.get("source_url") or "").strip()
    decompile_attempt = bool(ref_item.get("decompile_attempt"))
    base = ref_project_dir(user_id, scope_id, project_id, scope=scope)
    ensure_dir(base)

    if not source_url:
        if decompile_attempt:
            from plan.reference_decompile import DecompileError, materialize_decompiled

            try:
                return await materialize_decompiled(
                    user_id,
                    scope_id,
                    ref_item,
                    scope=scope,
                    plan_context=plan_context or {},
                    on_step=on_step,
                )
            except DecompileError as exc:
                return {
                    "project_id": project_id,
                    "status": "failed",
                    "source_url": "",
                    "source_kind": None,
                    "error": str(exc)[:300],
                    "failure_kind": exc.failure_kind,
                    "title": title,
                    "slug": ref_item.get("slug") or project_id,
                    "degrade_reason": "decompile_failed",
                    "agent_fallback": False,
                }
        return {
            "project_id": project_id,
            "status": "failed",
            "source_url": "",
            "error": "需开启「尝试反编译」或提供开源 source_url",
            "title": title,
        }

    repo = base / "repo"
    try:
        import asyncio

        await _emit_ref_step(
            on_step,
            {
                "project_id": project_id,
                "title": title,
                "step": "git_clone",
                "label": "克隆开源仓库",
                "status": "running",
                "preview": source_url[:120],
            },
        )
        await asyncio.to_thread(clone_reference_repo, source_url, repo)
        await _emit_ref_step(
            on_step,
            {
                "project_id": project_id,
                "title": title,
                "step": "git_clone",
                "label": "克隆开源仓库",
                "status": "ok",
            },
        )
        await _emit_ref_step(
            on_step,
            {
                "project_id": project_id,
                "title": title,
                "step": "build_index",
                "label": "构建源码索引",
                "status": "running",
            },
        )
        index = await asyncio.to_thread(build_index, repo)
        file_count = index.get("file_count") or 0
        if file_count == 0:
            return {
                "project_id": project_id,
                "status": "failed",
                "source_url": source_url,
                "source_kind": "git",
                "error": "仓库未产生可索引源码（0 个文件）",
                "failure_kind": "decompile_error",
                "title": title,
                "slug": ref_item.get("slug") or project_id,
                "degrade_reason": "decompile_failed",
                "agent_fallback": False,
            }
        save_index(user_id, scope_id, project_id, index, scope=scope)
        await _emit_ref_step(
            on_step,
            {
                "project_id": project_id,
                "title": title,
                "step": "build_index",
                "label": "构建源码索引",
                "status": "ok",
                "preview": f"{index.get('file_count') or 0} 个文件",
            },
        )
        return {
            "project_id": project_id,
            "status": "ready",
            "source_kind": "git",
            "mapping": None,
            "source_url": source_url,
            "error": None,
            "title": ref_item.get("title") or project_id,
            "slug": ref_item.get("slug") or project_id,
            "entry_points": index.get("entry_points") or [],
            "key_files": index.get("key_files") or [],
            "file_count": index.get("file_count") or 0,
            "agent_fallback": False,
        }
    except Exception as exc:
        logger.warning("reference materialize failed %s: %s", project_id, exc)
        return {
            "project_id": project_id,
            "status": "failed",
            "source_url": source_url,
            "source_kind": "git",
            "error": str(exc)[:300],
            "title": ref_item.get("title") or project_id,
        }
