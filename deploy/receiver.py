#!/usr/bin/env python3
"""MCmodAgent deploy receiver — listens for TCP deploy connections."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
import threading
import time
import zlib
from pathlib import Path
from typing import Any

DEPLOY_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEPLOY_DIR))

from lib.manifest import FileEntry, diff_manifest  # noqa: E402
from lib.platform_util import (  # noqa: E402
    restart_server_after_deploy,
    run_pip_install,
    stop_server_on_port,
)
from lib.protocol import (  # noqa: E402
    CHUNK_SIZE,
    Frame,
    FrameReader,
    MsgType,
    ProtocolError,
    build_hello,
    decode_json,
    encode_frame,
    encode_json,
    load_psk,
    verify_hello,
)


class FileReceiveState:
    def __init__(self) -> None:
        self.path: str = ""
        self.size: int = 0
        self.sha256: str = ""
        self.received: int = 0
        self.hasher = hashlib.sha256()
        self.handle = None
        self.staging_path: Path | None = None


class DeploySession:
    def __init__(
        self,
        conn: socket.socket,
        addr: tuple[str, int],
        psk: bytes,
        config: dict[str, Any],
    ) -> None:
        self.conn = conn
        self.addr = addr
        self.psk = psk
        self.config = config
        self.seq_out = 1
        self.reader = FrameReader(psk)
        self.deploy_root = Path(config["deploy_root"]).resolve()
        self.staging_root = self.deploy_root / ".deploy_staging"
        self.manifest_entries: list[FileEntry] = []
        self.needed_paths: list[str] = []
        self.file_state = FileReceiveState()
        self.files_written: list[str] = []
        self.requirements_changed = False

    def send_json(self, msg_type: MsgType, obj: Any) -> None:
        data = encode_json(self.psk, msg_type, self.seq_out, obj)
        self.seq_out += 1
        self.conn.sendall(data)

    def send_error(self, message: str, code: str = "error") -> None:
        self.send_json(MsgType.ERROR, {"code": code, "message": message})

    def recv_frames(self) -> list[Frame]:
        while True:
            frames = self.reader.feed(b"")
            if frames:
                return frames
            chunk = self.conn.recv(CHUNK_SIZE)
            if not chunk:
                raise ProtocolError("Connection closed")
            frames = self.reader.feed(chunk)
            if frames:
                return frames

    def handle(self) -> None:
        try:
            self._run()
        except ProtocolError as exc:
            print(f"[receiver] protocol error: {exc}", flush=True)
            try:
                self.send_error(str(exc))
            except OSError:
                pass
        except Exception as exc:
            import traceback
            print(f"[receiver] internal error: {exc}", flush=True)
            traceback.print_exc()
            try:
                self.send_error(f"internal error: {exc}", code="internal")
            except OSError:
                pass
        finally:
            if self.file_state.handle:
                self.file_state.handle.close()

    def _run(self) -> None:
        frames = self.recv_frames()
        if not frames or frames[0].msg_type != MsgType.HELLO:
            raise ProtocolError("Expected HELLO")
        hello = decode_json(frames[0])
        verify_hello(hello, role="receiver")

        ack = build_hello()
        self.send_json(MsgType.HELLO_ACK, ack)

        frames = self.recv_frames()
        if not frames or frames[0].msg_type != MsgType.MANIFEST:
            raise ProtocolError("Expected MANIFEST")
        manifest = decode_json(frames[0])
        self._process_manifest(manifest)

        while True:
            for frame in self.recv_frames():
                if frame.msg_type == MsgType.FILE_BEGIN:
                    self._file_begin(decode_json(frame))
                elif frame.msg_type == MsgType.FILE_DATA:
                    self._file_data(frame.payload)
                elif frame.msg_type == MsgType.FILE_END:
                    self._file_end(decode_json(frame))
                elif frame.msg_type == MsgType.COMMIT:
                    result = self._commit(decode_json(frame))
                    self.send_json(MsgType.RESULT, result)
                    return
                elif frame.msg_type == MsgType.ERROR:
                    raise ProtocolError(decode_json(frame).get("message", "sender error"))
                else:
                    raise ProtocolError(f"Unexpected message: {frame.msg_type}")

    def _process_manifest(self, manifest: dict[str, Any]) -> None:
        files = manifest.get("files", [])
        force_full = bool(manifest.get("force_full", False))
        self.manifest_entries = [
            FileEntry(path=f["path"], size=f["size"], sha256=f["sha256"])
            for f in files
        ]
        if force_full:
            self.needed_paths = [e.path for e in self.manifest_entries]
        else:
            self.needed_paths = diff_manifest(self.manifest_entries, self.deploy_root)

        if "server/requirements.txt" in self.needed_paths:
            self.requirements_changed = True

        print(f"[receiver] manifest: {len(self.needed_paths)} file(s) to update")
        self.send_json(MsgType.MANIFEST_ACK, {"needed": self.needed_paths})

    def _staging_file(self, rel_path: str) -> Path:
        self.staging_root.mkdir(parents=True, exist_ok=True)
        dest = self.staging_root / rel_path.replace("/", os.sep)
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

    def _file_begin(self, payload: dict[str, Any]) -> None:
        if self.file_state.handle:
            self.file_state.handle.close()
        self.file_state = FileReceiveState()
        self.file_state.path = payload["path"]
        self.file_state.size = payload["size"]
        self.file_state.sha256 = payload["sha256"]
        self.file_state.staging_path = self._staging_file(self.file_state.path)
        self.file_state.handle = open(self.file_state.staging_path, "wb")

    def _file_data(self, payload: bytes) -> None:
        if not self.file_state.handle:
            raise ProtocolError("FILE_DATA without FILE_BEGIN")
        if len(payload) < 5:
            raise ProtocolError("FILE_DATA payload too short")
        offset = int.from_bytes(payload[:4], "big")
        compressed = payload[4]
        data = payload[5:]
        if compressed:
            data = zlib.decompress(data)
        if offset != self.file_state.received:
            raise ProtocolError(f"Unexpected offset {offset}, expected {self.file_state.received}")
        self.file_state.handle.write(data)
        self.file_state.hasher.update(data)
        self.file_state.received += len(data)

    def _file_end(self, payload: dict[str, Any]) -> None:
        if not self.file_state.handle:
            raise ProtocolError("FILE_END without FILE_BEGIN")
        self.file_state.handle.close()
        self.file_state.handle = None
        digest = self.file_state.hasher.hexdigest()
        if digest != payload.get("sha256") or digest != self.file_state.sha256:
            raise ProtocolError(f"Checksum mismatch for {self.file_state.path}")
        if payload.get("path") != self.file_state.path:
            raise ProtocolError("FILE_END path mismatch")
        self.files_written.append(self.file_state.path)
        print(f"[receiver] received {self.file_state.path}")

    def _commit(self, payload: dict[str, Any]) -> dict[str, Any]:
        pip_result = ""
        restart_result = ""
        files_applied = 0

        try:
            for rel in self.files_written:
                src = self.staging_root / rel.replace("/", os.sep)
                dest = self.deploy_root / rel.replace("/", os.sep)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                files_applied += 1

            # Clean empty staging dirs
            if self.staging_root.is_dir():
                shutil.rmtree(self.staging_root, ignore_errors=True)

            auto_pip = self.config.get("auto_pip", True)
            if auto_pip and (self.requirements_changed or payload.get("pip_install")):
                print("[receiver] running pip install ...")
                ok, pip_result = run_pip_install(self.deploy_root, self.config)
                if not ok:
                    print(f"[receiver] pip install FAILED: {pip_result}", flush=True)
                    return {
                        "ok": False,
                        "message": f"pip install failed: {pip_result}",
                        "files_written": files_applied,
                    }
                print(f"[receiver] pip install OK")

            auto_restart = self.config.get("auto_restart", True)
            if auto_restart or payload.get("restart"):
                port = int(self.config.get("server_port", 8000))
                print(f"[receiver] restarting service on port {port} ...")
                start_ok, restart_result = restart_server_after_deploy(
                    self.deploy_root, self.config, port
                )
                if not start_ok:
                    print(f"[receiver] restart FAILED: {restart_result}", flush=True)
                    return {
                        "ok": False,
                        "message": restart_result,
                        "files_written": files_applied,
                        "pip": pip_result,
                    }
                print(f"[receiver] restart OK: {restart_result}")

            print(f"[receiver] committed {files_applied} file(s)")
            return {
                "ok": True,
                "message": "deploy committed",
                "files_written": files_applied,
                "pip": pip_result or "skipped",
                "restart": restart_result or "skipped",
            }
        except Exception as exc:
            return {
                "ok": False,
                "message": str(exc),
                "files_written": files_applied,
            }


def load_config(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def handle_client(
    conn: socket.socket,
    addr: tuple[str, int],
    psk: bytes,
    config: dict[str, Any],
) -> None:
    conn.settimeout(300)
    print(f"[receiver] connection from {addr[0]}:{addr[1]}")
    try:
        session = DeploySession(conn, addr, psk, config)
        session.handle()
    finally:
        conn.close()
        print(f"[receiver] disconnected {addr[0]}:{addr[1]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="MCmodAgent deploy receiver")
    parser.add_argument(
        "--config",
        default=str(DEPLOY_DIR / "config" / "receiver.json"),
        help="Path to receiver.json",
    )
    parser.add_argument(
        "--psk",
        default=str(DEPLOY_DIR / "keys" / "psk.hex"),
        help="Path to PSK hex file",
    )
    args = parser.parse_args()

    config = load_config(Path(args.config))
    psk = load_psk(args.psk)

    host = config.get("listen_host", "127.0.0.1")
    port = int(config.get("listen_port", 19090))
    deploy_root = Path(config.get("deploy_root", ".."))
    if not deploy_root.is_absolute():
        deploy_root = (DEPLOY_DIR / deploy_root).resolve()
    config["deploy_root"] = str(deploy_root)
    if not deploy_root.is_dir():
        print(f"[receiver] ERROR: deploy_root not found: {deploy_root}", file=sys.stderr)
        return 1

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(5)
    print(f"[receiver] listening on {host}:{port}")
    print(f"[receiver] deploy_root={deploy_root}")

    try:
        while True:
            conn, addr = server.accept()
            thread = threading.Thread(
                target=handle_client,
                args=(conn, addr, psk, config),
                daemon=True,
            )
            thread.start()
    except KeyboardInterrupt:
        print("\n[receiver] shutting down")
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    from lib.cli_util import run_main

    run_main(main)
