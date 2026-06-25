"""Cross-platform stop/start server and pip install helpers."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

STARTUP_WAIT_SEC = 15.0
STARTUP_POLL_INTERVAL = 0.5
INTEGRATED_RESTART_WAIT_SEC = 22.0

# Keep log handles open so detached child processes keep working stdout/stderr.
_open_log_handles: list[object] = []


def is_windows() -> bool:
    return sys.platform == "win32"


def resolve_venv_python(deploy_root: Path, config: dict[str, Any]) -> Path:
    rel = config.get("venv_python", "")
    if rel:
        candidate = deploy_root / rel.replace("/", os.sep)
        if candidate.is_file():
            return candidate
    if is_windows():
        candidate = deploy_root / ".venv" / "Scripts" / "python.exe"
    else:
        candidate = deploy_root / ".venv" / "bin" / "python"
    return candidate


def is_integrated_mode() -> bool:
    return os.environ.get("MCMOD_DEPLOY_INTEGRATED", "").lower() in ("1", "true", "yes")


def restart_log_path(deploy_root: Path) -> Path:
    return deploy_root / "data" / "logs" / "deploy-restart.log"


def _log_restart_note(log_file: Path, message: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, "ab") as f:
        f.write(f"\n--- deploy restart {stamp} ---\n".encode("utf-8"))
        f.write(message.encode("utf-8"))
        if not message.endswith("\n"):
            f.write(b"\n")


def pids_on_port(port: int) -> set[int]:
    if is_windows():
        return _pids_on_port_windows(port)
    return _find_linux_listeners(port)


def is_port_listening(port: int) -> tuple[bool, str]:
    if _can_connect_port(port):
        pids = pids_on_port(port)
        if pids:
            pid_text = ", ".join(str(p) for p in sorted(pids))
            return True, f"PID {pid_text}"
        return True, "accepting connections"

    pids = pids_on_port(port)
    if pids:
        pid_text = ", ".join(str(p) for p in sorted(pids))
        return True, f"PID {pid_text}"
    return False, "not listening"


def _can_connect_port(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _pids_on_port_windows(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return set()

    pids: set[int] = set()
    for line in result.stdout.splitlines():
        if f":{port}" not in line:
            continue
        upper = line.upper()
        if "LISTENING" not in upper and "监听" not in line:
            continue
        parts = line.split()
        if parts:
            try:
                pids.add(int(parts[-1]))
            except ValueError:
                pass
    return pids


def stop_server_on_port(port: int) -> tuple[bool, str]:
    if is_windows():
        return _stop_windows(port)
    return _stop_linux(port)


def _stop_windows(port: int) -> tuple[bool, str]:
    pids = _pids_on_port_windows(port)
    if not pids:
        return True, "no process listening"

    killed: list[int] = []
    for pid in pids:
        if pid <= 4:
            continue
        r = subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0:
            killed.append(pid)

    if killed:
        return True, f"killed PIDs: {', '.join(map(str, killed))}"
    return True, "no killable process found"


def _stop_linux(port: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["fuser", "-n", "tcp", f"{port}/tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            subprocess.run(
                ["fuser", "-k", "-n", "tcp", f"{port}/tcp"],
                capture_output=True,
                check=False,
            )
            return True, f"fuser killed port {port}"
    except FileNotFoundError:
        pass

    pids = _find_linux_listeners(port)
    if not pids:
        return True, "no process listening"

    killed: list[int] = []
    for pid in pids:
        try:
            os.kill(pid, 15)
            killed.append(pid)
        except OSError:
            pass
    if killed:
        time.sleep(1)
        return True, f"sent SIGTERM to PIDs: {', '.join(map(str, killed))}"
    return True, "no killable process found"


def _find_linux_listeners(port: int) -> set[int]:
    pids: set[int] = set()
    try:
        result = subprocess.run(
            ["ss", "-ltnp"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTEN" in line:
                    for token in line.split():
                        if token.startswith("pid="):
                            try:
                                pids.add(int(token.split("=")[1].split(",")[0]))
                            except ValueError:
                                pass
            if pids:
                return pids
    except FileNotFoundError:
        pass

    proc_net = Path("/proc/net/tcp")
    if not proc_net.is_file():
        return pids

    port_hex = f"{port:04X}"
    inode_targets: set[str] = set()
    for line in proc_net.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 10:
            continue
        local_addr, state, inode = parts[1], parts[3], parts[9]
        if state != "0A":
            continue
        if local_addr.split(":")[-1].upper() == port_hex:
            inode_targets.add(inode)

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        fd_dir = entry / "fd"
        if not fd_dir.is_dir():
            continue
        try:
            for fd in fd_dir.iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inode_targets:
                    pids.add(int(entry.name))
                    break
        except PermissionError:
            continue
    return pids


def run_pip_install(deploy_root: Path, config: dict[str, Any]) -> tuple[bool, str]:
    python = resolve_venv_python(deploy_root, config)
    if not python.is_file():
        return False, f"venv python not found: {python}"

    req = deploy_root / "server" / "requirements.txt"
    if not req.is_file():
        return False, f"requirements not found: {req}"

    cmd = [str(python), "-m", "pip", "install", "-r", str(req)]
    result = subprocess.run(
        cmd,
        cwd=str(deploy_root),
        capture_output=True,
        text=True,
        check=False,
    )
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return False, output.strip() or "pip install failed"
    return True, "pip install ok"


def start_server(deploy_root: Path, config: dict[str, Any]) -> tuple[bool, str]:
    port = int(config.get("server_port", 8000))
    log_file = restart_log_path(deploy_root)
    if is_windows():
        return _start_windows(deploy_root, config, port, log_file)
    return _start_linux(deploy_root, config, port, log_file)


def _wait_for_port(port: int, log_file: Path, *, timeout: float | None = None) -> tuple[bool, str]:
    wait_sec = INTEGRATED_RESTART_WAIT_SEC if timeout is None and is_integrated_mode() else (timeout or STARTUP_WAIT_SEC)
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        listening, detail = is_port_listening(port)
        if listening:
            suffix = f"; log: {log_file}" if not is_integrated_mode() else ""
            return True, f"port {port} listening ({detail}){suffix}"
        time.sleep(STARTUP_POLL_INTERVAL)
    hint = "Moduscript.bat will restart server" if is_integrated_mode() else f"see {log_file}"
    return False, f"port {port} not listening after {wait_sec:.0f}s; {hint}"


def restart_server_after_deploy(
    deploy_root: Path,
    config: dict[str, Any],
    port: int,
) -> tuple[bool, str]:
    log_file = restart_log_path(deploy_root)
    _stop_ok, stop_msg = stop_server_on_port(port)

    if is_integrated_mode():
        _log_restart_note(log_file, f"integrated restart: stop={stop_msg}; waiting for bat loop")
        ok, wait_msg = _wait_for_port(port, log_file, timeout=INTEGRATED_RESTART_WAIT_SEC)
        return ok, f"stop: {stop_msg}; {wait_msg}"

    time.sleep(1)
    start_ok, start_msg = start_server(deploy_root, config)
    return start_ok, f"stop: {stop_msg}; start: {start_msg}"


def _popen_server(
    cmd: list[str],
    cwd: Path,
    log_file: Path,
) -> subprocess.Popen[Any]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_fd = open(log_file, "ab", buffering=0)
    log_fd.write(f"\n--- deploy restart {stamp} ---\n".encode("utf-8"))
    log_fd.write(f"cmd: {' '.join(cmd)}\n".encode("utf-8"))
    log_fd.write(f"cwd: {cwd}\n".encode("utf-8"))
    log_fd.flush()
    _open_log_handles.append(log_fd)

    kwargs: dict = {
        "args": cmd,
        "cwd": str(cwd),
        "stdout": log_fd,
        "stderr": log_fd,
        "stdin": subprocess.DEVNULL,
        "close_fds": False,
    }
    if is_windows():
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(**kwargs)


def _start_windows(
    deploy_root: Path,
    config: dict[str, Any],
    port: int,
    log_file: Path,
) -> tuple[bool, str]:
    python = resolve_venv_python(deploy_root, config)
    server_dir = deploy_root / "server"
    main_py = server_dir / "main.py"
    if not python.is_file():
        return False, f"venv python not found: {python}"
    if not main_py.is_file():
        return False, f"main.py not found: {main_py}"

    cmd = [str(python), "-u", str(main_py)]
    _log_restart_note(
        log_file,
        f"cmd: {' '.join(cmd)}\ncwd: {server_dir}",
    )

    try:
        creationflags = subprocess.CREATE_NEW_CONSOLE
        subprocess.Popen(
            cmd,
            cwd=str(server_dir),
            creationflags=creationflags,
        )
    except OSError as exc:
        return False, str(exc)

    ok, msg = _wait_for_port(port, log_file)
    if ok:
        return True, f"{msg}; started server/main.py"
    return ok, msg


def _start_linux(
    deploy_root: Path,
    config: dict[str, Any],
    port: int,
    log_file: Path,
) -> tuple[bool, str]:
    python = resolve_venv_python(deploy_root, config)
    main_py = deploy_root / "server" / "main.py"
    if not python.is_file():
        return False, f"venv python not found: {python}"
    if not main_py.is_file():
        return False, f"main.py not found: {main_py}"

    cmd = [str(python), str(main_py.name)]
    try:
        proc = _popen_server(cmd, deploy_root / "server", log_file)
    except OSError as exc:
        return False, str(exc)
    ok, msg = _wait_for_port(port, log_file)
    if not ok and proc.poll() is not None:
        return False, f"process exited (code {proc.returncode}); see {log_file}"
    return ok, msg
