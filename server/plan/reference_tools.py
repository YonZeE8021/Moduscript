"""Read-only reference source tools for plan LLM tool-calling."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from typing import Any

from plan.reference_config import (
    READ_TOOL_LIST_MAX_ENTRIES,
    READ_TOOL_MAX_BYTES_PER_READ,
    READ_TOOL_MAX_TOTAL_BYTES,
    SKIP_DIR_NAMES,
    SNIPPET_MAX_LINES,
)
from plan.reference_index import (
    load_index,
    read_snippet,
    search_reference,
    _resolve_in_repo,
    _resolve_repo_path,
)

REFERENCE_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_reference_index",
            "description": "Get index metadata for a reference mod (entry points, key files, file count).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "Reference mod project id"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_reference_files",
            "description": "List source files under a reference mod repo, optionally filtered by glob.",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "path_prefix": {
                        "type": "string",
                        "description": "Optional directory prefix, e.g. net/hd/mod",
                    },
                    "glob_pattern": {
                        "type": "string",
                        "description": "Optional glob, e.g. *.java",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_reference_file",
            "description": "Read lines from a file in the reference repo (1-based line numbers).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "path": {"type": "string", "description": "Relative path within repo"},
                    "start_line": {"type": "integer", "description": "First line (default 1)"},
                    "end_line": {"type": "integer", "description": "Last line (inclusive)"},
                },
                "required": ["project_id", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_reference",
            "description": "Search reference source for keywords (space-separated terms).",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["project_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish_reading",
            "description": "Signal that you have read enough reference source for this phase.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of what you learned from the source",
                    },
                },
                "required": ["summary"],
            },
        },
    },
]


@dataclass
class ReferenceToolContext:
    user_id: str
    plan_id: str
    bytes_read: int = 0
    finished: bool = False
    finish_summary: str = ""
    transcript_lines: list[str] = field(default_factory=list)


def _truncate_json(payload: dict[str, Any], *, max_chars: int = 12_000) -> str:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40] + '\n… (truncated, use line ranges to read more)'


def _check_bytes_budget(ctx: ReferenceToolContext, add_bytes: int) -> str | None:
    if ctx.bytes_read + add_bytes > READ_TOOL_MAX_TOTAL_BYTES:
        return (
            f"累计读取已达上限 ({READ_TOOL_MAX_TOTAL_BYTES} bytes)，"
            "请调用 finish_reading 结束阅读。"
        )
    return None


def tool_get_reference_index(user_id: str, plan_id: str, project_id: str) -> dict[str, Any]:
    index = load_index(user_id, plan_id, project_id) or {}
    repo = _resolve_repo_path(user_id, plan_id, project_id)
    return {
        "project_id": project_id,
        "repo_exists": repo.is_dir(),
        "file_count": index.get("file_count") or 0,
        "entry_points": (index.get("entry_points") or [])[:20],
        "key_files": (index.get("key_files") or [])[:30],
    }


def tool_list_reference_files(
    user_id: str,
    plan_id: str,
    project_id: str,
    *,
    path_prefix: str = "",
    glob_pattern: str = "",
) -> dict[str, Any]:
    repo = _resolve_repo_path(user_id, plan_id, project_id)
    if not repo.is_dir():
        return {"error": "repo not found", "files": []}
    prefix = path_prefix.strip().replace("\\", "/").strip("/")
    pattern = glob_pattern.strip() or "*"
    files: list[dict[str, Any]] = []
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo).as_posix()
        parts = rel.split("/")
        if any(p in SKIP_DIR_NAMES for p in parts):
            continue
        if prefix and not rel.startswith(prefix):
            continue
        if not fnmatch.fnmatch(path.name, pattern) and not fnmatch.fnmatch(rel, pattern):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        files.append({"path": rel, "size": size})
        if len(files) >= READ_TOOL_LIST_MAX_ENTRIES:
            break
    return {"project_id": project_id, "count": len(files), "files": files}


def tool_read_reference_file(
    ctx: ReferenceToolContext,
    project_id: str,
    path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict[str, Any]:
    repo = _resolve_repo_path(ctx.user_id, ctx.plan_id, project_id)
    if not repo.is_dir():
        return {"error": "repo not found"}
    try:
        target = _resolve_in_repo(repo, path)
    except ValueError:
        return {"error": "invalid path"}
    if not target.is_file():
        return {"error": "file not found"}
    try:
        size = target.stat().st_size
    except OSError:
        return {"error": "cannot stat file"}
    if size > READ_TOOL_MAX_BYTES_PER_READ * 4:
        return {
            "error": "file too large",
            "hint": "use start_line/end_line to read a smaller range",
            "size": size,
        }
    budget_err = _check_bytes_budget(ctx, min(size, READ_TOOL_MAX_BYTES_PER_READ))
    if budget_err:
        return {"error": budget_err}
    s = max(1, int(start_line or 1))
    e = int(end_line) if end_line is not None else s + SNIPPET_MAX_LINES - 1
    try:
        snip = read_snippet(
            ctx.user_id,
            ctx.plan_id,
            project_id,
            path,
            start=s,
            end=e,
        )
    except ValueError as exc:
        return {"error": str(exc)}
    content = snip.get("content") or ""
    content_bytes = len(content.encode("utf-8"))
    if content_bytes > READ_TOOL_MAX_BYTES_PER_READ:
        content = content.encode("utf-8")[:READ_TOOL_MAX_BYTES_PER_READ].decode("utf-8", errors="ignore")
        content_bytes = READ_TOOL_MAX_BYTES_PER_READ
    ctx.bytes_read += content_bytes
    ctx.transcript_lines.append(f"[{project_id}] {snip['path']}:{snip['start']}-{snip['end']}\n{content[:2000]}")
    return {
        "project_id": project_id,
        "path": snip["path"],
        "start": snip["start"],
        "end": snip["end"],
        "content": content,
        "bytes_read_total": ctx.bytes_read,
    }


def tool_search_reference(
    ctx: ReferenceToolContext,
    project_id: str,
    query: str,
) -> dict[str, Any]:
    hits = search_reference(ctx.user_id, ctx.plan_id, project_id, query)
    preview: list[dict[str, Any]] = []
    for hit in hits[:8]:
        preview.append(
            {
                "path": hit.get("path"),
                "line": hit.get("line"),
                "context": (hit.get("context") or "")[:400],
            }
        )
    if preview:
        ctx.transcript_lines.append(
            f"[{project_id}] search:{query!r} -> "
            + ", ".join(f"{h['path']}:{h['line']}" for h in preview[:3])
        )
    return {"project_id": project_id, "query": query, "hit_count": len(hits), "hits": preview}


def execute_reference_tool(
    ctx: ReferenceToolContext,
    name: str,
    arguments: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    """Run a tool; returns (result_json_str, optional_finding_dict)."""
    finding: dict[str, Any] | None = None
    if name == "finish_reading":
        ctx.finished = True
        ctx.finish_summary = str(arguments.get("summary") or "").strip()
        result = {"ok": True, "summary": ctx.finish_summary}
        return _truncate_json(result), None

    if name == "get_reference_index":
        result = tool_get_reference_index(
            ctx.user_id,
            ctx.plan_id,
            str(arguments.get("project_id") or ""),
        )
        return _truncate_json(result), None

    if name == "list_reference_files":
        result = tool_list_reference_files(
            ctx.user_id,
            ctx.plan_id,
            str(arguments.get("project_id") or ""),
            path_prefix=str(arguments.get("path_prefix") or ""),
            glob_pattern=str(arguments.get("glob_pattern") or ""),
        )
        return _truncate_json(result), None

    if name == "read_reference_file":
        pid = str(arguments.get("project_id") or "")
        path = str(arguments.get("path") or "")
        result = tool_read_reference_file(
            ctx,
            pid,
            path,
            start_line=int(arguments.get("start_line") or 1),
            end_line=int(arguments["end_line"]) if arguments.get("end_line") is not None else None,
        )
        if "content" in result and not result.get("error"):
            finding = {
                "project_id": pid,
                "query": f"read:{path}",
                "reason": "tool:read_reference_file",
                "paths": [
                    {
                        "path": result.get("path"),
                        "start": result.get("start"),
                        "end": result.get("end"),
                    }
                ],
                "snippet_preview": (result.get("content") or "")[:2000],
            }
        return _truncate_json(result), finding

    if name == "search_reference":
        pid = str(arguments.get("project_id") or "")
        query = str(arguments.get("query") or "")
        result = tool_search_reference(ctx, pid, query)
        hits = result.get("hits") or []
        if hits:
            finding = {
                "project_id": pid,
                "query": query,
                "reason": "tool:search_reference",
                "paths": [{"path": h.get("path"), "line": h.get("line")} for h in hits[:3]],
                "snippet_preview": "\n---\n".join(
                    (h.get("context") or "")[:500] for h in hits[:3]
                )[:2000],
            }
        return _truncate_json(result), finding

    return _truncate_json({"error": f"unknown tool: {name}"}), None
