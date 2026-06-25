#!/usr/bin/env python3
"""MCmodAgent deploy sender — push incremental updates over TCP."""

from __future__ import annotations

import argparse
import json
import socket
import sys
import zlib
from pathlib import Path
from typing import Any

DEPLOY_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(DEPLOY_DIR))

from lib.manifest import entries_by_path, manifest_to_dict, scan_project  # noqa: E402
from lib.protocol import (  # noqa: E402
    CHUNK_SIZE,
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

COMPRESS_MIN_SIZE = 512
COMPRESS_EXTS = {
    ".py", ".html", ".js", ".css", ".json", ".md", ".txt", ".ini", ".ps1", ".sh"
}


def load_config(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def should_compress(path: str, data: bytes) -> bool:
    if len(data) < COMPRESS_MIN_SIZE:
        return False
    ext = Path(path).suffix.lower()
    return ext in COMPRESS_EXTS


def recv_frame(conn: socket.socket, reader: FrameReader) -> Any:
    while True:
        frames = reader.feed(b"")
        if frames:
            return decode_json(frames[0]), frames[0].msg_type
        chunk = conn.recv(CHUNK_SIZE)
        if not chunk:
            raise ProtocolError("Connection closed")
        frames = reader.feed(chunk)
        if frames:
            return decode_json(frames[0]), frames[0].msg_type


def send_file(
    conn: socket.socket,
    psk: bytes,
    seq: int,
    root: Path,
    rel_path: str,
    entry_size: int,
    entry_hash: str,
) -> int:
    file_path = root / rel_path.replace("/", "\\") if sys.platform == "win32" else root / rel_path
    data = file_path.read_bytes()
    if len(data) != entry_size:
        raise ProtocolError(f"Size mismatch for {rel_path}")

    begin = encode_json(
        psk,
        MsgType.FILE_BEGIN,
        seq,
        {"path": rel_path, "size": entry_size, "sha256": entry_hash},
    )
    conn.sendall(begin)
    seq += 1

    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + CHUNK_SIZE]
        use_compress = should_compress(rel_path, chunk)
        payload_data = zlib.compress(chunk, level=6) if use_compress else chunk
        header = offset.to_bytes(4, "big") + bytes([1 if use_compress else 0])
        payload = header + payload_data
        conn.sendall(encode_frame(psk, MsgType.FILE_DATA, seq, payload))
        seq += 1
        offset += len(chunk)

    end = encode_json(
        psk,
        MsgType.FILE_END,
        seq,
        {"path": rel_path, "sha256": entry_hash},
    )
    conn.sendall(end)
    return seq + 1


def deploy(
    config: dict[str, Any],
    psk: bytes,
    *,
    force_full: bool = False,
    dry_run: bool = False,
) -> int:
    project_root = Path(config.get("project_root", ".."))
    if not project_root.is_absolute():
        project_root = (DEPLOY_DIR / project_root).resolve()

    host = config["host"]
    port = int(config["port"])
    timeout = float(config.get("connect_timeout_sec", 30))

    entries = scan_project(project_root)
    by_path = entries_by_path(entries)
    manifest = manifest_to_dict(entries, force_full=force_full)

    print(f"[sender] project_root={project_root}")
    print(f"[sender] scanned {len(entries)} deployable files")

    if dry_run:
        print("[sender] dry-run mode — files that would be sent:")
        for e in entries:
            print(f"  {e.path}  ({e.size} bytes)")
        return 0

    print(f"[sender] connecting to {host}:{port} ...")
    conn = socket.create_connection((host, port), timeout=timeout)
    reader = FrameReader(psk)
    seq = 1

    try:
        hello = build_hello()
        conn.sendall(encode_json(psk, MsgType.HELLO, seq, hello))
        seq += 1

        ack, msg_type = recv_frame(conn, reader)
        if msg_type != MsgType.HELLO_ACK:
            raise ProtocolError(f"Expected HELLO_ACK, got {msg_type}")
        verify_hello(ack, role="sender")

        conn.sendall(
            encode_json(psk, MsgType.MANIFEST, seq, manifest)
        )
        seq += 1

        ack_body, msg_type = recv_frame(conn, reader)
        if msg_type != MsgType.MANIFEST_ACK:
            raise ProtocolError(f"Expected MANIFEST_ACK, got {msg_type}")

        needed: list[str] = ack_body.get("needed", [])
        if not needed:
            print("[sender] no files need updating")
            conn.sendall(
                encode_json(
                    psk,
                    MsgType.COMMIT,
                    seq,
                    {"restart": False, "pip_install": False},
                )
            )
            seq += 1
            result, msg_type = recv_frame(conn, reader)
            if msg_type == MsgType.RESULT:
                print(f"[sender] result: {result.get('message', result)}")
            return 0

        print(f"[sender] sending {len(needed)} file(s) ...")
        requirements_changed = "server/requirements.txt" in needed

        for i, rel_path in enumerate(needed, 1):
            entry = by_path.get(rel_path)
            if not entry:
                raise ProtocolError(f"Missing manifest entry for {rel_path}")
            print(f"  [{i}/{len(needed)}] {rel_path} ({entry.size} bytes)")
            seq = send_file(
                conn, psk, seq, project_root, rel_path, entry.size, entry.sha256
            )

        conn.sendall(
            encode_json(
                psk,
                MsgType.COMMIT,
                seq,
                {
                    "restart": True,
                    "pip_install": requirements_changed,
                },
            )
        )
        seq += 1

        result, msg_type = recv_frame(conn, reader)
        if msg_type == MsgType.ERROR:
            print(f"[sender] ERROR: {result.get('message')}", file=sys.stderr)
            return 1
        if msg_type != MsgType.RESULT:
            raise ProtocolError(f"Expected RESULT, got {msg_type}")

        if result.get("ok"):
            print(f"[sender] deploy OK — {result.get('files_written', 0)} file(s) written")
            if result.get("pip"):
                print(f"[sender] pip: {result['pip']}")
            if result.get("restart"):
                print(f"[sender] restart: {result['restart']}")
            return 0

        print(f"[sender] deploy FAILED: {result.get('message')}", file=sys.stderr)
        return 1
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MCmodAgent deploy sender")
    parser.add_argument(
        "--config",
        default=str(DEPLOY_DIR / "config" / "sender.json"),
        help="Path to sender.json",
    )
    parser.add_argument(
        "--psk",
        default=str(DEPLOY_DIR / "keys" / "psk.hex"),
        help="Path to PSK hex file",
    )
    parser.add_argument("--force", action="store_true", help="Force full sync")
    parser.add_argument("--dry-run", action="store_true", help="List files only")
    args = parser.parse_args(argv)

    try:
        config = load_config(Path(args.config))
        psk = load_psk(args.psk)
        return deploy(config, psk, force_full=args.force, dry_run=args.dry_run)
    except ProtocolError as exc:
        print(f"[sender] protocol error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[sender] connection error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    from lib.cli_util import run_main

    run_main(main)
