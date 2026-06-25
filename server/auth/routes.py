"""Authentication API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from audit_log import record as audit_record
from auth.deps import CurrentUser, get_current_user
from auth.jwt_util import create_access_token
from auth.passwords import hash_password, verify_password
from storage.admin_store import admin_store
from storage.user_store import user_store

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    email: str | None = Field(default=None)
    password: str = Field(..., min_length=6)


class LoginRequest(BaseModel):
    account: str = Field(..., min_length=2)
    password: str


@router.post("/register")
async def register(body: RegisterRequest):
    settings = await admin_store.get_settings()
    if not settings.get("registration_enabled", True):
        raise HTTPException(status_code=403, detail="注册已关闭")

    try:
        profile = await user_store.create_user(
            body.username,
            hash_password(body.password),
            email=body.email,
        )
    except ValueError as exc:
        msg = str(exc)
        if "email already registered" in msg:
            raise HTTPException(status_code=409, detail="邮箱已注册") from exc
        if "username already registered" in msg:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        raise HTTPException(status_code=400, detail=msg) from exc

    token = create_access_token(profile["id"], profile["role"])
    email_note = body.email.strip() if body.email and body.email.strip() else "无邮箱"
    audit_record(
        "auth",
        "auth.register",
        user_id=profile["id"],
        message=f"注册: {body.username} ({email_note})",
        detail={"username": body.username, "email": profile.get("email") or ""},
    )
    return {"token": token, "user": profile}


@router.post("/login")
async def login(body: LoginRequest):
    profile = await user_store.get_by_account(body.account)
    if not profile:
        audit_record("auth", "auth.login_failed", message="登录失败", detail={"account": body.account})
        raise HTTPException(status_code=401, detail="账号或密码错误")
    if profile.get("disabled"):
        audit_record(
            "auth",
            "auth.login_failed",
            user_id=profile["id"],
            message="账号已禁用",
            detail={"account": body.account},
        )
        raise HTTPException(status_code=403, detail="账号已禁用")

    auth = await user_store.get_auth(profile["id"])
    if not auth or not verify_password(body.password, auth.get("password_hash", "")):
        audit_record("auth", "auth.login_failed", message="登录失败", detail={"account": body.account})
        raise HTTPException(status_code=401, detail="账号或密码错误")

    public = await user_store.get_profile(profile["id"])
    token = create_access_token(profile["id"], profile.get("role", "user"))
    audit_record(
        "auth",
        "auth.login",
        user_id=profile["id"],
        message=f"登录: {body.account}",
        detail={"account": body.account, "email": profile.get("email")},
    )
    return {"token": token, "user": public}


@router.post("/logout")
async def logout():
    return {"ok": True}


@router.get("/me")
async def me(user: Annotated[CurrentUser, Depends(get_current_user)]):
    profile = await user_store.get_profile(user.id)
    return {"user": profile}
