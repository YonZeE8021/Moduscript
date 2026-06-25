"""MDPL (ModDeploy Protocol) frame codec, handshake, and HMAC authentication."""

from __future__ import annotations

import hashlib
import hmac
import json
import struct
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

MAGIC = b"MDPL"
VERSION = 0x01
TAG_SIZE = 16
HEADER_FMT = "!4sBBII"
HEADER_SIZE = struct.calcsize(HEADER_FMT)
MAX_PAYLOAD = 64 * 1024 * 1024
HELLO_MAX_SKEW_SEC = 300
CHUNK_SIZE = 64 * 1024


class MsgType(IntEnum):
    HELLO = 0x01
    HELLO_ACK = 0x02
    MANIFEST = 0x03
    MANIFEST_ACK = 0x04
    FILE_BEGIN = 0x05
    FILE_DATA = 0x06
    FILE_END = 0x07
    COMMIT = 0x08
    RESULT = 0x09
    ERROR = 0xFF


class ProtocolError(Exception):
    pass


@dataclass
class Frame:
    msg_type: MsgType
    seq: int
    payload: bytes


def load_psk(path: str) -> bytes:
    text = open(path, encoding="utf-8").read().strip()
    if not text:
        raise ProtocolError(f"PSK file is empty: {path}")
    try:
        key = bytes.fromhex(text)
    except ValueError as exc:
        raise ProtocolError(f"Invalid PSK hex in {path}") from exc
    if len(key) != 32:
        raise ProtocolError(f"PSK must be 32 bytes, got {len(key)}")
    return key


def _compute_tag(psk: bytes, header: bytes, payload: bytes) -> bytes:
    return hmac.new(psk, header + payload, hashlib.sha256).digest()[:TAG_SIZE]


def encode_frame(psk: bytes, msg_type: MsgType, seq: int, payload: bytes) -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ProtocolError(f"Payload too large: {len(payload)}")
    header = struct.pack(HEADER_FMT, MAGIC, VERSION, int(msg_type), seq, len(payload))
    tag = _compute_tag(psk, header, payload)
    return header + payload + tag


def decode_frame(psk: bytes, data: bytes) -> Frame:
    if len(data) < HEADER_SIZE + TAG_SIZE:
        raise ProtocolError("Frame too short")
    magic, version, msg_type_val, seq, length = struct.unpack(
        HEADER_FMT, data[:HEADER_SIZE]
    )
    if magic != MAGIC:
        raise ProtocolError(f"Bad magic: {magic!r}")
    if version != VERSION:
        raise ProtocolError(f"Unsupported version: {version}")
    expected = HEADER_SIZE + length + TAG_SIZE
    if len(data) < expected:
        raise ProtocolError("Incomplete frame")
    payload = data[HEADER_SIZE : HEADER_SIZE + length]
    tag = data[HEADER_SIZE + length : expected]
    header = data[:HEADER_SIZE]
    expected_tag = _compute_tag(psk, header, payload)
    if not hmac.compare_digest(tag, expected_tag):
        raise ProtocolError("HMAC verification failed")
    return Frame(MsgType(msg_type_val), seq, payload)


def encode_json(psk: bytes, msg_type: MsgType, seq: int, obj: Any) -> bytes:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return encode_frame(psk, msg_type, seq, payload)


def decode_json(frame: Frame) -> Any:
    return json.loads(frame.payload.decode("utf-8"))


def build_hello(client_nonce: str | None = None) -> dict[str, Any]:
    import secrets

    return {
        "timestamp": int(time.time()),
        "nonce": client_nonce or secrets.token_hex(16),
    }


def verify_hello(payload: dict[str, Any], *, role: str) -> None:
    ts = payload.get("timestamp")
    if not isinstance(ts, int):
        raise ProtocolError(f"{role}: missing timestamp")
    skew = abs(int(time.time()) - ts)
    if skew > HELLO_MAX_SKEW_SEC:
        raise ProtocolError(f"{role}: timestamp skew {skew}s exceeds limit")
    nonce = payload.get("nonce")
    if not isinstance(nonce, str) or len(nonce) < 8:
        raise ProtocolError(f"{role}: invalid nonce")


class FrameReader:
    """Incremental frame reader from a TCP socket."""

    def __init__(self, psk: bytes) -> None:
        self.psk = psk
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> list[Frame]:
        self._buf.extend(chunk)
        frames: list[Frame] = []
        while True:
            if len(self._buf) < HEADER_SIZE:
                break
            magic, version, _msg_type, _seq, length = struct.unpack(
                HEADER_FMT, self._buf[:HEADER_SIZE]
            )
            if magic != MAGIC:
                raise ProtocolError(f"Bad magic in stream: {magic!r}")
            if version != VERSION:
                raise ProtocolError(f"Unsupported version: {version}")
            total = HEADER_SIZE + length + TAG_SIZE
            if len(self._buf) < total:
                break
            frame_data = bytes(self._buf[:total])
            del self._buf[:total]
            frames.append(decode_frame(self.psk, frame_data))
        return frames
