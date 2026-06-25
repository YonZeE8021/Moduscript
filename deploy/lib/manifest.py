"""Project scan, filter rules, and SHA256 manifest generation.

See deploy/README.md for sync usage.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

# Paths relative to project root that are included in deploy sync.
INCLUDE_GLOBS = [
    "server/**/*.py",
    "server/requirements.txt",
    "*.html",
    "css/**/*",
    "js/**/*",
    "docs/**/*",
    "scripts/**/*",
    "deploy/**/*.py",
    "deploy/**/*.bat",
    "deploy/**/*.ps1",
    "deploy/**/*.sh",
    "deploy/config/**/*",
    "deploy/frp/**/*",
    "deploy/README.md",
    ".env.example",
    "README.md",
]

EXCLUDE_PATTERNS = [
    "data/**",
    ".env",
    ".venv/**",
    "venv/**",
    "__pycache__/**",
    "**/__pycache__/**",
    ".git/**",
    ".pytest_cache/**",
    "deploy/keys/psk.hex",
    "deploy/config/sender.json",
    "deploy/receiver.pid",
    "deploy/.deploy_staging/**",
    "**/*.pyc",
    "server/test_*.py",
    "server/pytest.ini",
    "server/**/__pycache__/**",
    "OutputFootage/**",
    "debug.log",
    "debug-*.log",
    "server/--remove-bridge*/**",
    "**/*.lnk",
    ".public_staging/**",
]


@dataclass(frozen=True)
class FileEntry:
    path: str  # forward-slash relative path
    size: int
    sha256: str


def _normalize_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _matches_glob(rel: str, pattern: str) -> bool:
    return PurePosixPath(rel).match(pattern, case_sensitive=False)


def _matches_any(rel: str, patterns: Iterable[str]) -> bool:
    return any(_matches_glob(rel, pat) for pat in patterns)


def should_include(rel: str) -> bool:
    if "/__pycache__/" in rel or rel.startswith("__pycache__/"):
        return False
    if rel.endswith(".pyc"):
        return False
    if _matches_any(rel, EXCLUDE_PATTERNS):
        return False
    if rel in (".env.example", "README.md", "Moduscript.bat", "Moduscript.sh", "server/requirements.txt"):
        return True
    if rel.endswith(".html") and "/" not in rel:
        return True
    if rel.startswith(("css/", "js/", "docs/", "scripts/")):
        return True
    if rel.startswith("deploy/"):
        if rel in ("deploy/keys/psk.hex", "deploy/config/sender.json"):
            return False
        if rel.startswith("deploy/keys/") and not rel.endswith(".example"):
            return False
        if rel.endswith((".pid", ".log", ".log.err")):
            return False
        if ".deploy_staging" in rel:
            return False
        return rel.endswith((".py", ".bat", ".ps1", ".sh", ".json", ".ini", ".md", ".example", ".hex"))
    if rel.startswith("server/") and rel.endswith(".py") and not PurePosixPath(rel).match("server/test_*.py"):
        return True
    return False


def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def scan_project(root: Path) -> list[FileEntry]:
    root = root.resolve()
    entries: list[FileEntry] = []
    seen: set[str] = set()

    for pattern in INCLUDE_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            rel = _normalize_rel(path, root)
            if rel in seen:
                continue
            if not should_include(rel):
                continue
            seen.add(rel)
            entries.append(
                FileEntry(
                    path=rel,
                    size=path.stat().st_size,
                    sha256=hash_file(path),
                )
            )

    entries.sort(key=lambda e: e.path)
    return entries


def manifest_to_dict(entries: list[FileEntry], *, force_full: bool = False) -> dict:
    return {
        "files": [
            {"path": e.path, "size": e.size, "sha256": e.sha256} for e in entries
        ],
        "force_full": force_full,
    }


def diff_manifest(
    remote_entries: list[FileEntry], local_root: Path
) -> list[str]:
    """Return relative paths that need updating on the receiver."""
    needed: list[str] = []
    for entry in remote_entries:
        target = local_root / Path(entry.path)
        if not target.is_file():
            needed.append(entry.path)
            continue
        if hash_file(target) != entry.sha256:
            needed.append(entry.path)
    return needed


def entries_by_path(entries: list[FileEntry]) -> dict[str, FileEntry]:
    return {e.path: e for e in entries}
