"""User preferences and settings routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from datetime import datetime, timezone

from audit_log import query_logs, record as audit_record
from auth.deps import CurrentUser, get_current_user
from auth.passwords import hash_password, verify_password
from plan.schemas import KnowledgeL1Update
from storage.admin_store import admin_store
from storage.user_store import user_store


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

router = APIRouter(prefix="/api/v1/user", tags=["user"])


class PreferencesUpdate(BaseModel):
    requirements: list[dict[str, Any]] | None = None
    reference_mods: list[dict[str, Any]] | None = None
    theme: str | None = None


class LlmUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    subagent_model: str | None = None


class ProfileUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=32)
    email: str | None = None


class PasswordUpdate(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6)


@router.get("/profile")
async def get_user_profile(user: Annotated[CurrentUser, Depends(get_current_user)]):
    profile = await user_store.get_profile(user.id)
    return {"user": profile}


@router.put("/profile")
async def update_user_profile(
    body: ProfileUpdate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    data = body.model_dump(exclude_unset=True)
    if not data:
        profile = await user_store.get_profile(user.id)
        return {"user": profile}
    try:
        profile = await user_store.update_account(user.id, **data)
    except ValueError as exc:
        msg = str(exc)
        if "email already registered" in msg:
            raise HTTPException(status_code=409, detail="邮箱已注册") from exc
        if "username already registered" in msg:
            raise HTTPException(status_code=409, detail="用户名已存在") from exc
        raise HTTPException(status_code=400, detail=msg) from exc
    audit_record(
        "user",
        "user.profile_update",
        user_id=user.id,
        message="更新账号资料",
        detail=data,
    )
    return {"user": profile}


@router.put("/password")
async def change_user_password(
    body: PasswordUpdate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    auth = await user_store.get_auth(user.id)
    if not auth or not verify_password(body.current_password, auth.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="当前密码错误")
    await user_store.update_password(user.id, hash_password(body.new_password))
    audit_record(
        "user",
        "user.password_change",
        user_id=user.id,
        message="修改密码",
    )
    return {"ok": True}


@router.get("/preferences")
async def get_preferences(user: Annotated[CurrentUser, Depends(get_current_user)]):
    return await user_store.get_preferences(user.id)


@router.put("/preferences")
async def update_preferences(
    body: PreferencesUpdate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    data = body.model_dump(exclude_none=True)
    return await user_store.save_preferences(user.id, data)


@router.get("/llm")
async def get_user_llm(user: Annotated[CurrentUser, Depends(get_current_user)]):
    settings = await admin_store.get_settings()
    if settings.get("shared_llm_enabled", True):
        shared = await admin_store.get_shared_llm_public()
        return {"source": "shared", "shared_enabled": True, "llm": shared}
    llm = await user_store.get_llm(user.id)
    return {"source": "user", "shared_enabled": False, "llm": llm}


@router.put("/llm")
async def update_user_llm(
    body: LlmUpdate,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    settings = await admin_store.get_settings()
    if settings.get("shared_llm_enabled", True):
        return {"message": "当前使用平台统一 API，无需单独配置", "shared_enabled": True}
    data = body.model_dump()
    payload: dict[str, Any] = {}
    for key in ("base_url", "model", "subagent_model"):
        if data.get(key) is not None:
            payload[key] = data[key]
    if data.get("api_key"):
        payload["api_key"] = data["api_key"]
    llm = await user_store.save_llm(user.id, payload)
    audit_record(
        "user",
        "user.llm_update",
        user_id=user.id,
        message="更新个人 LLM 配置",
        detail={
            "base_url": payload.get("base_url"),
            "model": payload.get("model"),
            "has_api_key": bool(data.get("api_key")),
        },
    )
    return {"source": "user", "shared_enabled": False, "llm": llm}


@router.get("/knowledge-l1")
async def get_knowledge_l1(user: Annotated[CurrentUser, Depends(get_current_user)]):
    prefs = await user_store.get_preferences(user.id)
    return {"knowledge_l1": prefs.get("knowledge_l1")}


@router.put("/knowledge-l1")
async def update_knowledge_l1(
    body: KnowledgeL1Update,
    user: Annotated[CurrentUser, Depends(get_current_user)],
):
    l1 = body.model_dump()
    l1["completed_at"] = _utc_now()
    prefs = await user_store.save_preferences(user.id, {"knowledge_l1": l1})
    audit_record(
        "user",
        "user.knowledge_l1_update",
        user_id=user.id,
        message="更新知识水平 L1",
        detail=l1,
    )
    return {"knowledge_l1": prefs.get("knowledge_l1")}
