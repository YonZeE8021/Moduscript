"""FastAPI auth dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.jwt_util import decode_token
from storage.user_store import user_store

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: str
    email: str
    role: str


async def _user_from_token(token: str | None) -> CurrentUser:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    payload = decode_token(token)
    if not payload or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效 token")
    profile = await user_store.get_profile_internal(payload["sub"])
    if not profile:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if profile.get("disabled"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="账号已禁用")
    return CurrentUser(
        id=profile["id"],
        email=profile["email"],
        role=profile.get("role", "user"),
    )


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    return await _user_from_token(credentials.credentials)


async def get_current_user_sse(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    token: Annotated[str | None, Query()] = None,
) -> CurrentUser:
    if credentials is not None and credentials.scheme.lower() == "bearer":
        return await _user_from_token(credentials.credentials)
    return await _user_from_token(token)


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser | None:
    if credentials is None:
        return None
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None


async def require_admin(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user


async def require_admin_panel(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """管理后台专用口令令牌（与用户账号 admin 角色无关）。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要管理后台登录")
    from auth.admin_panel import validate_admin_panel_token

    if not validate_admin_panel_token(credentials.credentials):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="管理后台令牌无效或已过期")


@dataclass
class SessionCaller:
    """会话 API 调用方：普通用户、账号 admin、或管理后台口令。"""

    kind: str  # user | user_admin | admin_panel
    user: CurrentUser | None
    is_admin_view: bool

    @property
    def actor_id(self) -> str | None:
        if self.user:
            return self.user.id
        return None


async def resolve_session_caller(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    token: Annotated[str | None, Query()] = None,
) -> SessionCaller:
    """用户 JWT（Header 或 query token）或管理后台口令令牌。"""
    from auth.admin_panel import validate_admin_panel_token

    raw = ""
    if credentials is not None and credentials.scheme.lower() == "bearer":
        raw = credentials.credentials or ""
    elif token:
        raw = token.strip()

    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")

    if validate_admin_panel_token(raw):
        return SessionCaller(kind="admin_panel", user=None, is_admin_view=True)

    user = await _user_from_token(raw)
    is_admin = user.role == "admin"
    return SessionCaller(
        kind="user_admin" if is_admin else "user",
        user=user,
        is_admin_view=is_admin,
    )
