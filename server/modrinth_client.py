"""Modrinth URL parsing and project fetch."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

MODRINTH_API_BASE = "https://api.modrinth.com/v2"

# slug or base62 project id
IDENTIFIER_RE = re.compile(r"^[\w!@$()`.+,\"\-']{1,64}$", re.IGNORECASE)

# First GitHub/GitLab repo link in markdown body
BODY_REPO_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com)/[\w.\-]+/[\w.\-]+",
    re.IGNORECASE,
)


class ModrinthError(Exception):
    """Base Modrinth client error."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def parse_modrinth_url(url: str) -> str:
    """Extract project identifier from Modrinth permalink or legacy /mod/ URL."""
    raw = (url or "").strip()
    if not raw:
        raise ModrinthError("URL 不能为空", 400)

    if not raw.lower().startswith(("http://", "https://")):
        if IDENTIFIER_RE.match(raw):
            return raw
        raise ModrinthError("无效的 Modrinth 链接或项目 ID", 400)

    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise ModrinthError("无效的 URL", 400) from exc

    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "modrinth.com":
        raise ModrinthError("仅支持 modrinth.com 链接", 400)

    path = parsed.path.rstrip("/")
    for pattern in (r"^/project/([^/]+)$", r"^/mod/([^/]+)$"):
        match = re.match(pattern, path, re.IGNORECASE)
        if match:
            return match.group(1)

    raise ModrinthError("无法从链接解析项目 ID，请使用 /project/… 永久链接", 400)


def build_project_url(project_id: str) -> str:
    return f"https://modrinth.com/project/{project_id}"


def _source_from_issues_url(issues_url: str) -> str:
    """Derive repository root from GitHub/GitLab issues URL."""
    url = issues_url.strip().rstrip("/")
    lower = url.lower()
    for suffix in ("/issues", "/issues/new"):
        if lower.endswith(suffix):
            return url[: -len(suffix)] or url
    return ""


def resolve_source_url(data: dict[str, Any]) -> str:
    """
    Resolve open-source repository URL from Modrinth project payload.

    Priority: source_url -> issues_url (strip /issues) -> first github/gitlab in body.
    """
    source = (data.get("source_url") or "").strip()
    if source:
        return source

    issues = (data.get("issues_url") or "").strip()
    if issues:
        derived = _source_from_issues_url(issues)
        if derived:
            return derived

    body = data.get("body") or ""
    if body:
        match = BODY_REPO_RE.search(body)
        if match:
            return match.group(0).rstrip("/).),;")

    return ""


async def fetch_project(identifier: str) -> dict[str, Any]:
    """Fetch project from Modrinth API v2."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            res = await client.get(f"{MODRINTH_API_BASE}/project/{identifier}")
        except httpx.RequestError as exc:
            raise ModrinthError("无法连接 Modrinth API", 502) from exc

    if res.status_code == 404:
        raise ModrinthError("未找到该项目", 404)
    if res.status_code != 200:
        raise ModrinthError(f"Modrinth API 错误 ({res.status_code})", 502)

    data = res.json()
    project_id = data.get("id") or identifier
    slug = data.get("slug") or identifier

    return {
        "project_id": project_id,
        "slug": slug,
        "title": data.get("title") or slug,
        "description": data.get("description") or "",
        "body": data.get("body") or "",
        "project_type": data.get("project_type") or "",
        "loaders": data.get("loaders") or [],
        "game_versions": data.get("game_versions") or [],
        "source_url": resolve_source_url(data),
        "url": build_project_url(project_id),
    }


async def resolve_project_url(url: str) -> dict[str, Any]:
    identifier = parse_modrinth_url(url)
    return await fetch_project(identifier)


async def fetch_matching_version(
    project_id: str,
    *,
    game_version: str,
    loader: str,
) -> dict[str, Any]:
    """Return best Modrinth version for game version + loader."""
    loader_key = (loader or "fabric").strip().lower()
    mc = (game_version or "1.20.1").strip()
    params = {
        "loaders": json.dumps([loader_key]),
        "game_versions": json.dumps([mc]),
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            res = await client.get(
                f"{MODRINTH_API_BASE}/project/{project_id}/version",
                params=params,
            )
        except httpx.RequestError as exc:
            raise ModrinthError("无法连接 Modrinth API", 502) from exc

    if res.status_code == 404:
        raise ModrinthError("未找到该项目", 404)
    if res.status_code != 200:
        raise ModrinthError(f"Modrinth 版本 API 错误 ({res.status_code})", 502)

    versions = res.json()
    if not isinstance(versions, list) or not versions:
        raise ModrinthError(f"无匹配 {mc} / {loader_key} 的 Modrinth 版本", 404)

    for item in versions:
        if item.get("version_type") == "release":
            return item
    return versions[0]


def pick_primary_jar_file(version: dict[str, Any]) -> dict[str, Any]:
    files = version.get("files") or []
    for f in files:
        if not isinstance(f, dict):
            continue
        mime = (f.get("mime_type") or "").lower()
        if f.get("primary") and ("java-archive" in mime or (f.get("filename") or "").endswith(".jar")):
            return f
    for f in files:
        if isinstance(f, dict) and (f.get("filename") or "").endswith(".jar"):
            return f
    raise ModrinthError("该版本没有可下载的 jar 文件", 404)


async def download_version_file(file_info: dict[str, Any], dest: Path) -> Path:
    url = (file_info.get("url") or "").strip()
    if not url:
        raise ModrinthError("jar 下载地址为空", 502)
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        try:
            async with client.stream("GET", url) as res:
                if res.status_code != 200:
                    raise ModrinthError(f"jar 下载失败 (HTTP {res.status_code})", 502)
                with dest.open("wb") as fh:
                    async for chunk in res.aiter_bytes(chunk_size=65536):
                        fh.write(chunk)
        except httpx.RequestError as exc:
            raise ModrinthError("jar 下载网络错误", 502) from exc
    return dest
