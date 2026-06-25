"""Admin panel password gate (separate from user account admin role)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from auth.jwt_util import decode_token
from auth.passwords import hash_password, verify_password
from config import ADMIN_DIR, JWT_ALGORITHM, JWT_SECRET
from jose import jwt
from storage.file_io import read_json, write_json

PANEL_AUTH_PATH = ADMIN_DIR / "panel_auth.json"
ADMIN_PANEL_SCOPE = "admin_panel"
ADMIN_TOKEN_HOURS = 12


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _load_panel_auth() -> dict[str, Any]:
    data = read_json(PANEL_AUTH_PATH, {}) or {}
    if not data.get("password_hash"):
        env_pw = (os.getenv("MCMOD_ADMIN_PASSWORD") or "").strip()
        if env_pw:
            data["password_hash"] = hash_password(env_pw)
            write_json(PANEL_AUTH_PATH, data)
    return data


def verify_admin_password(password: str) -> bool:
    auth = _load_panel_auth()
    hashed = auth.get("password_hash") or ""
    if not hashed:
        return False
    return verify_password(password, hashed)


def create_admin_panel_token() -> str:
    expire = _utc_now() + timedelta(hours=ADMIN_TOKEN_HOURS)
    payload = {"scope": ADMIN_PANEL_SCOPE, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def validate_admin_panel_token(token: str) -> bool:
    payload = decode_token(token)
    if not payload:
        return False
    return payload.get("scope") == ADMIN_PANEL_SCOPE
