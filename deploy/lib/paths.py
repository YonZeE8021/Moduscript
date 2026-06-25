"""Canonical deploy directory paths (single source of truth)."""

from __future__ import annotations

from pathlib import Path

# Set once from deploy/cli.py via init_paths().
_DEPLOY_DIR: Path | None = None


def init_paths(deploy_dir: Path) -> Path:
    """Resolve and validate deploy root. Call from deploy/cli.py on startup."""
    global _DEPLOY_DIR
    resolved = deploy_dir.resolve()
    receiver = resolved / "receiver.py"
    if not receiver.is_file():
        # Guard: never treat deploy/lib/ as deploy root.
        parent = resolved.parent
        parent_receiver = parent / "receiver.py"
        if parent_receiver.is_file():
            resolved = parent
            receiver = parent_receiver
        else:
            raise FileNotFoundError(
                f"receiver.py not found at {receiver} — "
                f"expected deploy directory, got {deploy_dir}"
            )
    _DEPLOY_DIR = resolved
    return resolved


def deploy_dir() -> Path:
    if _DEPLOY_DIR is None:
        # Fallback: lib/../ (deploy/)
        return init_paths(Path(__file__).resolve().parent.parent)
    return _DEPLOY_DIR


def root_dir() -> Path:
    return deploy_dir().parent


def receiver_py() -> Path:
    return deploy_dir() / "receiver.py"


def pid_file() -> Path:
    return deploy_dir() / "receiver.pid"


def log_file() -> Path:
    return deploy_dir() / "receiver.log"


def err_log_file() -> Path:
    return deploy_dir() / "receiver.log.err"
