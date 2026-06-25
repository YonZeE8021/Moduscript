"""Shared CLI helpers for deploy entry points."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

from lib.paths import (
    deploy_dir,
    err_log_file,
    log_file,
    pid_file,
    receiver_py,
    root_dir,
)


def find_python() -> Path:
    venv = root_dir() / ".venv" / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    if venv.is_file():
        return venv
    return Path(sys.executable)


def pause_on_error(code: int) -> int:
    if code == 0 or os.environ.get("CI"):
        return code
    print(f"\n[deploy] failed with exit code {code}", file=sys.stderr)
    if sys.platform == "win32":
        os.system("pause")
    else:
        try:
            input("Press Enter to exit...")
        except EOFError:
            pass
    return code


def run_main(func) -> None:
    """Run CLI handler; print traceback and pause on failure."""
    try:
        code = func()
    except KeyboardInterrupt:
        print("\n[deploy] interrupted", file=sys.stderr)
        code = 130
    except Exception as exc:
        print(f"\n[deploy] error: {exc}", file=sys.stderr)
        traceback.print_exc()
        code = 1
    raise SystemExit(pause_on_error(code))


def _read_pid() -> int | None:
    pf = pid_file()
    if not pf.is_file():
        return None
    try:
        return int(pf.read_text(encoding="ascii").strip())
    except ValueError:
        return None


def _is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stop_receiver() -> int:
    pid = _read_pid()
    pf = pid_file()
    if pid is None:
        print("[deploy] no receiver.pid — receiver not running?", file=sys.stderr)
        return 1
    if not _is_running(pid):
        pf.unlink(missing_ok=True)
        print(f"[deploy] stale PID file removed ({pid})")
        return 0
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
    else:
        os.kill(pid, 15)
    pf.unlink(missing_ok=True)
    print(f"[deploy] stopped receiver (PID {pid})")
    return 0


def start_receiver_daemon() -> int:
    pf = pid_file()
    pid = _read_pid()
    if pid is not None and _is_running(pid):
        print(f"[deploy] receiver already running (PID {pid})")
        print("[deploy] stop: python deploy/cli.py stop")
        print(f"[deploy] log:  {log_file()}")
        return 0
    pf.unlink(missing_ok=True)

    python = find_python()
    receiver = receiver_py().resolve()
    deploy = deploy_dir().resolve()
    lf = log_file()
    elf = err_log_file()
    lf.unlink(missing_ok=True)
    elf.unlink(missing_ok=True)

    print("[deploy] starting receiver (background)")
    print(f"  python:   {python}")
    print(f"  receiver: {receiver}")
    print(f"  log:      {lf}")
    print("  debug:    python deploy/cli.py receive")

    with open(lf, "ab", buffering=0) as log_out, open(elf, "ab", buffering=0) as log_err:
        kwargs: dict = {
            "args": [str(python), str(receiver)],
            "cwd": str(deploy),
            "stdout": log_out,
            "stderr": log_err,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(**kwargs)

    time.sleep(2)
    if proc.poll() is not None:
        print(
            f"[deploy] receiver exited immediately (code {proc.returncode})",
            file=sys.stderr,
        )
        _print_log_tail(lf)
        _print_log_tail(elf)
        return 1

    pf.write_text(str(proc.pid), encoding="ascii")
    print(f"[deploy] receiver started (PID {proc.pid})")
    return 0


def start_receiver_foreground() -> int:
    pid = _read_pid()
    if pid is not None and _is_running(pid):
        print(f"[deploy] receiver already running in background (PID {pid})", file=sys.stderr)
        print("[deploy] stop it first: python deploy/cli.py stop")
        return 1

    python = find_python()
    receiver = receiver_py().resolve()
    deploy = deploy_dir().resolve()

    if not receiver.is_file():
        print(f"[deploy] receiver.py not found: {receiver}", file=sys.stderr)
        return 1

    print("[deploy] receiver (foreground — Ctrl+C to stop)")
    print(f"  python:   {python}")
    print(f"  receiver: {receiver}")
    print(f"  cwd:      {deploy}")

    result = subprocess.run(
        [str(python), str(receiver)],
        cwd=str(deploy),
    )
    return result.returncode


def _print_log_tail(path: Path, lines: int = 40) -> None:
    if not path.is_file():
        return
    print(f"\n--- last {lines} lines of {path} ---")
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in content[-lines:]:
        print(line)
    print("--- end ---")
