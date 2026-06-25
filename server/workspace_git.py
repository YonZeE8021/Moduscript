"""Git checkpoint helpers for session workspaces."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

GIT_TIMEOUT_SEC = 120

DEFAULT_GITIGNORE = """\
.gradle/
build/
run/
out/
.idea/
*.iml
.classpath
.project
.settings/
*.log
.DS_Store
Thumbs.db
"""

# Preserve reference mods across git clean
CLEAN_EXCLUDE = ("references", "references/**")


class WorkspaceGitError(RuntimeError):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


def _run_git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = ["git", *args]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkspaceGitError(f"git timed out: {' '.join(cmd)}") from exc
    except FileNotFoundError as exc:
        raise WorkspaceGitError("git executable not found") from exc
    if check and result.returncode != 0:
        stderr = (result.stderr or result.stdout or "").strip()
        raise WorkspaceGitError(stderr or f"git failed: {' '.join(cmd)}")
    return result


def is_repo(workspace: Path) -> bool:
    return (workspace / ".git").is_dir()


def init_repo(workspace: Path, *, message: str = "Initial workspace") -> str:
    workspace.mkdir(parents=True, exist_ok=True)
    gitignore = workspace / ".gitignore"
    if not gitignore.is_file():
        gitignore.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
    if not is_repo(workspace):
        _run_git(workspace, "init")
        _run_git(workspace, "config", "user.email", "mcmodagent@local")
        _run_git(workspace, "config", "user.name", "MCmodAgent")
    _run_git(workspace, "add", "-A")
    status = _run_git(workspace, "status", "--porcelain", check=False)
    if not (status.stdout or "").strip():
        result = _run_git(workspace, "rev-parse", "HEAD", check=False)
        if result.returncode == 0 and (result.stdout or "").strip():
            return (result.stdout or "").strip()
    _run_git(workspace, "commit", "-m", message, "--allow-empty")
    result = _run_git(workspace, "rev-parse", "HEAD")
    return (result.stdout or "").strip()


def reinit_repo(workspace: Path, *, message: str = "Initial workspace") -> str:
    git_dir = workspace / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir, ignore_errors=True)
    workspace.mkdir(parents=True, exist_ok=True)
    gitignore = workspace / ".gitignore"
    if not gitignore.is_file():
        gitignore.write_text(DEFAULT_GITIGNORE, encoding="utf-8")
    _run_git(workspace, "init")
    _run_git(workspace, "config", "user.email", "mcmodagent@local")
    _run_git(workspace, "config", "user.name", "MCmodAgent")
    _run_git(workspace, "add", "-A")
    status = _run_git(workspace, "status", "--porcelain", check=False)
    if not (status.stdout or "").strip():
        result = _run_git(workspace, "rev-parse", "HEAD", check=False)
        if result.returncode == 0 and (result.stdout or "").strip():
            return (result.stdout or "").strip()
    _run_git(workspace, "commit", "-m", message, "--allow-empty")
    result = _run_git(workspace, "rev-parse", "HEAD")
    return (result.stdout or "").strip()


def checkpoint(workspace: Path, message: str) -> str:
    if not is_repo(workspace):
        return init_repo(workspace, message=message)
    _run_git(workspace, "add", "-A")
    status = _run_git(workspace, "status", "--porcelain", check=False)
    if not (status.stdout or "").strip():
        result = _run_git(workspace, "rev-parse", "HEAD")
        return (result.stdout or "").strip()
    _run_git(workspace, "commit", "-m", message)
    result = _run_git(workspace, "rev-parse", "HEAD")
    return (result.stdout or "").strip()


def verify_ref(workspace: Path, git_ref: str) -> bool:
    if not git_ref or not is_repo(workspace):
        return False
    result = _run_git(workspace, "cat-file", "-e", f"{git_ref.strip()}^{{commit}}", check=False)
    return result.returncode == 0


def reset_to(workspace: Path, git_ref: str | None) -> None:
    if not git_ref or not git_ref.strip():
        raise WorkspaceGitError("git ref required for reset")
    if not is_repo(workspace):
        raise WorkspaceGitError(f"not a git repo: {workspace}")
    ref = git_ref.strip()
    if not verify_ref(workspace, ref):
        raise WorkspaceGitError(f"invalid git ref: {ref}")
    _run_git(workspace, "reset", "--hard", ref)
    clean_args = ["clean", "-fd", *[f"-e={pat}" for pat in CLEAN_EXCLUDE]]
    _run_git(workspace, *clean_args)


def current_ref(workspace: Path) -> str | None:
    if not is_repo(workspace):
        return None
    result = _run_git(workspace, "rev-parse", "HEAD", check=False)
    if result.returncode != 0:
        return None
    ref = (result.stdout or "").strip()
    return ref or None


def wipe_workspace_contents(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for child in workspace.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
