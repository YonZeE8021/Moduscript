"""User account and preferences file storage."""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BOOTSTRAP_ADMIN_EMAIL, USERS_DIR
from storage.file_io import ensure_dir, list_json_files, read_json, write_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _email_key(email: str) -> str:
    return email.strip().lower()


def _username_key(username: str) -> str:
    return username.strip().lower()


_USERNAME_RE = re.compile(r"^[\w\u4e00-\u9fff]{2,32}$", re.UNICODE)


def _validate_username(username: str) -> str:
    name = username.strip()
    if not _USERNAME_RE.match(name):
        raise ValueError("用户名须为 2–32 个字符，仅含字母、数字、下划线或中文")
    return name


def _display_username(profile: dict[str, Any]) -> str:
    stored = profile.get("username")
    if stored:
        return stored
    email = profile.get("email", "")
    if "@" in email:
        return email.split("@", 1)[0]
    return email or "用户"


def _mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


class UserStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        ensure_dir(base_dir)
        self._email_index_path = base_dir / "_email_index.json"
        self._username_index_path = base_dir / "_username_index.json"

    def _user_dir(self, user_id: str) -> Path:
        return self.base_dir / user_id

    def _load_email_index(self) -> dict[str, str]:
        return read_json(self._email_index_path, {}) or {}

    def _save_email_index(self, index: dict[str, str]) -> None:
        write_json(self._email_index_path, index)

    def _load_username_index(self) -> dict[str, str]:
        return read_json(self._username_index_path, {}) or {}

    def _save_username_index(self, index: dict[str, str]) -> None:
        write_json(self._username_index_path, index)

    def init(self) -> None:
        ensure_dir(self.base_dir)

    def _create_sync(
        self,
        username: str,
        password_hash: str,
        *,
        email: str | None = None,
        accepted_beta: bool = False,
    ) -> dict[str, Any]:
        username_norm = _validate_username(username)
        username_key = _username_key(username_norm)

        email_norm = ""
        if email and email.strip():
            email_norm = _email_key(email)
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_norm):
                raise ValueError("invalid email")

        email_index = self._load_email_index()
        if email_norm and email_norm in email_index:
            raise ValueError("email already registered")

        username_index = self._load_username_index()
        if username_key in username_index:
            raise ValueError("username already registered")

        user_id = str(uuid.uuid4())
        role = (
            "admin"
            if BOOTSTRAP_ADMIN_EMAIL and email_norm and email_norm == BOOTSTRAP_ADMIN_EMAIL
            else "user"
        )
        now = _utc_now()

        profile = {
            "id": user_id,
            "username": username_norm,
            "email": email_norm,
            "role": role,
            "disabled": False,
            "accepted_beta": accepted_beta,
            "created_at": now,
            "updated_at": now,
        }
        auth = {"password_hash": password_hash}
        preferences = {
            "requirements": [],
            "reference_mods": [],
            "theme": "system",
        }
        llm = {
            "base_url": "",
            "api_key": "",
            "model": "",
            "subagent_model": "",
        }

        user_dir = self._user_dir(user_id)
        ensure_dir(user_dir / "sessions")
        write_json(user_dir / "profile.json", profile)
        write_json(user_dir / "auth.json", auth)
        write_json(user_dir / "preferences.json", preferences)
        write_json(user_dir / "llm.json", llm)

        if email_norm:
            email_index[email_norm] = user_id
            self._save_email_index(email_index)
        username_index[username_key] = user_id
        self._save_username_index(username_index)
        return profile

    def _get_by_email_sync(self, email: str) -> dict[str, Any] | None:
        user_id = self._load_email_index().get(_email_key(email))
        if not user_id:
            return None
        return self._get_profile_sync(user_id)

    def _get_by_username_sync(self, username: str) -> dict[str, Any] | None:
        user_id = self._load_username_index().get(_username_key(username))
        if not user_id:
            return None
        return self._get_profile_sync(user_id)

    def _get_by_account_sync(self, account: str) -> dict[str, Any] | None:
        account = account.strip()
        if not account:
            return None
        if "@" in account:
            return self._get_by_email_sync(account)
        return self._get_by_username_sync(account)

    def _get_profile_sync(self, user_id: str) -> dict[str, Any] | None:
        path = self._user_dir(user_id) / "profile.json"
        return read_json(path)

    def _get_auth_sync(self, user_id: str) -> dict[str, Any] | None:
        return read_json(self._user_dir(user_id) / "auth.json")

    def _public_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": profile["id"],
            "username": _display_username(profile),
            "email": profile["email"],
            "role": profile.get("role", "user"),
            "disabled": profile.get("disabled", False),
            "created_at": profile.get("created_at"),
        }

    def _list_users_sync(self) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        if not self.base_dir.is_dir():
            return users
        for entry in self.base_dir.iterdir():
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            profile = read_json(entry / "profile.json")
            if profile:
                users.append(profile)
        users.sort(key=lambda u: u.get("created_at", ""), reverse=True)
        return users

    def _update_profile_sync(self, user_id: str, **fields: Any) -> dict[str, Any] | None:
        path = self._user_dir(user_id) / "profile.json"
        profile = read_json(path)
        if not profile:
            return None
        for key, val in fields.items():
            if val is not None:
                profile[key] = val
        profile["updated_at"] = _utc_now()
        write_json(path, profile)
        return profile

    def _update_account_sync(
        self,
        user_id: str,
        *,
        username: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        profile = self._get_profile_sync(user_id)
        if not profile:
            raise ValueError("user not found")

        email_index = self._load_email_index()
        username_index = self._load_username_index()

        if username is not None:
            username_norm = _validate_username(username)
            username_key = _username_key(username_norm)
            old_username = profile.get("username")
            if old_username:
                old_key = _username_key(old_username)
                if username_key != old_key:
                    if username_key in username_index and username_index[username_key] != user_id:
                        raise ValueError("username already registered")
                    if old_key in username_index and username_index[old_key] == user_id:
                        del username_index[old_key]
                    username_index[username_key] = user_id
            elif username_key in username_index and username_index[username_key] != user_id:
                raise ValueError("username already registered")
            else:
                username_index[username_key] = user_id
            profile["username"] = username_norm

        if email is not None:
            if not str(email).strip():
                old_email = profile.get("email", "")
                if old_email and old_email in email_index and email_index[old_email] == user_id:
                    del email_index[old_email]
                profile["email"] = ""
            else:
                email_norm = _email_key(email)
                if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_norm):
                    raise ValueError("invalid email")
                old_email = profile.get("email", "")
                if email_norm != old_email:
                    if email_norm in email_index and email_index[email_norm] != user_id:
                        raise ValueError("email already registered")
                    if old_email and old_email in email_index and email_index[old_email] == user_id:
                        del email_index[old_email]
                    email_index[email_norm] = user_id
                    profile["email"] = email_norm

        profile["updated_at"] = _utc_now()
        write_json(self._user_dir(user_id) / "profile.json", profile)
        self._save_email_index(email_index)
        self._save_username_index(username_index)
        return profile

    def _update_password_sync(self, user_id: str, password_hash: str) -> None:
        path = self._user_dir(user_id) / "auth.json"
        if not path.is_file():
            raise ValueError("user not found")
        write_json(path, {"password_hash": password_hash})

    def _get_preferences_sync(self, user_id: str) -> dict[str, Any]:
        return read_json(self._user_dir(user_id) / "preferences.json", {}) or {}

    def _save_preferences_sync(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        path = self._user_dir(user_id) / "preferences.json"
        existing = read_json(path, {}) or {}
        existing.update(data)
        write_json(path, existing)
        return existing

    def _get_llm_sync(self, user_id: str) -> dict[str, Any]:
        return read_json(self._user_dir(user_id) / "llm.json", {}) or {}

    def _save_llm_sync(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        path = self._user_dir(user_id) / "llm.json"
        existing = read_json(path, {}) or {}
        for key in ("base_url", "api_key", "model", "subagent_model"):
            if key in data and data[key] is not None:
                if key == "api_key" and data[key] == "":
                    continue
                existing[key] = data[key]
        write_json(path, existing)
        return self._public_llm(existing)

    def _public_llm(self, llm: dict[str, Any]) -> dict[str, Any]:
        return {
            "base_url": llm.get("base_url", ""),
            "model": llm.get("model", ""),
            "subagent_model": llm.get("subagent_model", ""),
            "api_key_masked": _mask_api_key(llm.get("api_key") or ""),
            "configured": bool(llm.get("api_key") or llm.get("base_url")),
        }

    def _session_path(self, user_id: str, session_id: str) -> Path:
        return self._user_dir(user_id) / "sessions" / f"{session_id}.json"

    def _save_session_sync(self, user_id: str, session_data: dict[str, Any]) -> None:
        sid = session_data["session_id"]
        write_json(self._session_path(user_id, sid), session_data)

    def _load_session_sync(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        return read_json(self._session_path(user_id, session_id))

    def _delete_session_sync(self, user_id: str, session_id: str) -> bool:
        path = self._session_path(user_id, session_id)
        if path.is_file():
            path.unlink()
            return True
        return False

    def _list_sessions_sync(self, user_id: str) -> list[dict[str, Any]]:
        sessions_dir = self._user_dir(user_id) / "sessions"
        items: list[dict[str, Any]] = []
        for path in list_json_files(sessions_dir):
            data = read_json(path)
            if data:
                items.append(data)
        items.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return items

    def _list_all_sessions_sync(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for user in self._list_users_sync():
            uid = user["id"]
            for sess in self._list_sessions_sync(uid):
                result.append({**sess, "owner_id": uid, "owner_email": user.get("email")})
        result.sort(key=lambda s: s.get("created_at", ""), reverse=True)
        return result

    async def create_user(
        self,
        username: str,
        password_hash: str,
        *,
        email: str | None = None,
        accepted_beta: bool = False,
    ) -> dict[str, Any]:
        profile = await asyncio.to_thread(
            self._create_sync,
            username,
            password_hash,
            email=email,
            accepted_beta=accepted_beta,
        )
        return self._public_profile(profile)

    async def get_by_email(self, email: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_by_email_sync, email)

    async def get_by_account(self, account: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_by_account_sync, account)

    async def get_profile(self, user_id: str) -> dict[str, Any] | None:
        profile = await asyncio.to_thread(self._get_profile_sync, user_id)
        return self._public_profile(profile) if profile else None

    async def get_profile_internal(self, user_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_profile_sync, user_id)

    async def get_auth(self, user_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_auth_sync, user_id)

    async def list_users(self) -> list[dict[str, Any]]:
        users = await asyncio.to_thread(self._list_users_sync)
        return [self._public_profile(u) for u in users]

    async def set_disabled(self, user_id: str, disabled: bool) -> dict[str, Any] | None:
        profile = await asyncio.to_thread(self._update_profile_sync, user_id, disabled=disabled)
        return self._public_profile(profile) if profile else None

    async def update_account(
        self,
        user_id: str,
        *,
        username: str | None = None,
        email: str | None = None,
    ) -> dict[str, Any]:
        profile = await asyncio.to_thread(
            self._update_account_sync, user_id, username=username, email=email
        )
        return self._public_profile(profile)

    async def update_password(self, user_id: str, password_hash: str) -> None:
        await asyncio.to_thread(self._update_password_sync, user_id, password_hash)

    async def get_preferences(self, user_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_preferences_sync, user_id)

    async def save_preferences(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._save_preferences_sync, user_id, data)

    async def get_llm(self, user_id: str) -> dict[str, Any]:
        llm = await asyncio.to_thread(self._get_llm_sync, user_id)
        return self._public_llm(llm)

    async def get_llm_internal(self, user_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_llm_sync, user_id)

    async def save_llm(self, user_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._save_llm_sync, user_id, data)

    async def save_session(self, user_id: str, session_data: dict[str, Any]) -> None:
        await asyncio.to_thread(self._save_session_sync, user_id, session_data)

    async def load_session(self, user_id: str, session_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._load_session_sync, user_id, session_id)

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        return await asyncio.to_thread(self._delete_session_sync, user_id, session_id)

    async def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_sessions_sync, user_id)

    async def list_all_sessions(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_all_sessions_sync)


user_store = UserStore(USERS_DIR)
