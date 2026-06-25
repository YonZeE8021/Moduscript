"""Public release snapshot rules — extends deploy manifest with publish-only exclusions."""

from __future__ import annotations

import shutil
from pathlib import Path

from lib.manifest import _matches_any, should_include as _deploy_should_include

PUBLIC_ROOT_FILES = [
    "LICENSE",
    "NOTICE",
    "README.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CHANGELOG.md",
    "THIRD_PARTY_LICENSES.md",
    ".env.example",
    ".gitleaks.toml",
    "Moduscript.bat",
    "Moduscript.sh",
]

PUBLIC_EXTRA_EXCLUDE = [
    "AGENTS.md",
    "docs/maintainer/**",
    "public_overlay/**",
    "data/**",
    ".env",
    ".git/**",
    "OutputFootage/**",
    "debug.log",
    "debug-*.log",
    "server/--remove-bridge*/**",
    "deploy/keys/psk.hex",
    "deploy/config/sender.json",
    ".public_staging/**",
    ".cursor/**",
    "**/*.lnk",
    "server/test_*.py",
    "server/pytest.ini",
    "server/requirements-dev.txt",
    "scripts/publish-public.ps1",
    "scripts/publish-public.sh",
    "scripts/export_public_snapshot.py",
    "scripts/verify-public-staging.ps1",
    "scripts/verify-public-staging.sh",
]

TEXT_REPLACEMENTS: list[tuple[str, str]] = [
    ("your-frp-server.example", "your-frp-server.example"),
    ("com.example", "com.example"),
    ("Example", "Example"),
]

BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".zip",
        ".jar",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".pyc",
        ".docx",
    }
)


def _matches_public_exclude(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1]
    if name.startswith("[Must-read]"):
        return True
    if _matches_any(rel, PUBLIC_EXTRA_EXCLUDE):
        return True
    if rel.startswith(".cursor/"):
        return True
    return False


def should_include_public(rel: str) -> bool:
    """Return True if path should appear in a public release snapshot."""
    rel = rel.replace("\\", "/")
    if rel in PUBLIC_ROOT_FILES:
        return True
    if rel.startswith(".github/"):
        return True
    if _matches_public_exclude(rel):
        return False
    return _deploy_should_include(rel)


def iter_public_files(root: Path) -> list[Path]:
    """Return all files under root that pass public include rules."""
    root = root.resolve()
    results: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if should_include_public(rel):
            results.append(path)
    results.sort(key=lambda p: p.as_posix())
    return results


def apply_text_replacements(content: str) -> str:
    for old, new in TEXT_REPLACEMENTS:
        content = content.replace(old, new)
    return content


def post_process_file(dest_path: Path) -> None:
    """Apply sanitization replacements to a copied file if it is text."""
    if dest_path.suffix.lower() in BINARY_EXTENSIONS:
        return
    try:
        text = dest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return
    updated = apply_text_replacements(text)
    if updated != text:
        dest_path.write_text(updated, encoding="utf-8")


def apply_public_overlays(root: Path, dest: Path) -> list[str]:
    """Copy public_overlay/* onto dest, preserving relative paths. Returns paths written."""
    overlay_root = root / "public_overlay"
    if not overlay_root.is_dir():
        return []
    written: list[str] = []
    for src in overlay_root.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(overlay_root).as_posix()
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        post_process_file(target)
        written.append(rel)
    written.sort()
    return written


def copy_public_snapshot(root: Path, dest: Path) -> list[str]:
    """Copy public-safe files from root to dest. Returns relative paths copied."""
    root = root.resolve()
    dest = dest.resolve()
    copied: list[str] = []

    for src in iter_public_files(root):
        rel = src.relative_to(root).as_posix()
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        post_process_file(target)
        copied.append(rel)

    sender_example = root / "deploy/config/sender.example.json"
    if sender_example.is_file():
        out_sender = dest / "deploy/config/sender.json"
        out_sender.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sender_example, out_sender)
        post_process_file(out_sender)
        if "deploy/config/sender.json" not in copied:
            copied.append("deploy/config/sender.json")

    overlay_paths = apply_public_overlays(root, dest)
    for rel in overlay_paths:
        if rel not in copied:
            copied.append(rel)
        else:
            # overlay replaced an existing file — still listed once
            pass

    copied.sort()
    return copied
