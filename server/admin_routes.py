"""Admin control panel API (password-protected, manual URL access)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from audit_log import query_logs, record as audit_record
from auth.admin_panel import create_admin_panel_token, verify_admin_password
from auth.deps import require_admin_panel
from config import USE_MOCK_SESSIONS
from session_service import STATUS_LABELS, session_service
from storage.admin_store import admin_store
from storage.user_store import user_store
from plan.store import plan_store

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

PLAN_STATUS_LABELS: dict[str, str] = {
    "awaiting_l1": "待完成问卷",
    "active": "进行中",
    "ready": "可定稿",
    "finalized": "已定稿",
    "handed_off": "已移交编写",
}


class AdminLoginRequest(BaseModel):
    password: str = Field(..., min_length=1)


class SettingsUpdate(BaseModel):
    registration_enabled: bool | None = None
    shared_llm_enabled: bool | None = None
    beta_banner_enabled: bool | None = None
    beta_banner_text: str | None = None
    beta_agreement_url: str | None = None
    feedback_email: str | None = None


class LlmUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    subagent_model: str | None = None


class PromptOptimizeUpdate(BaseModel):
    enabled: bool | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    thinking_enabled: bool | None = None


class ModNameSuggestUpdate(BaseModel):
    enabled: bool | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None


class PlanLlmUpdate(BaseModel):
    enabled: bool | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    finalize_system_prompt: str | None = None
    temperature: float | None = None


class LlmProfileCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class LlmProfileRename(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class RequirementItem(BaseModel):
    id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    description: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class DefaultRequirementsUpdate(BaseModel):
    items: list[RequirementItem] = Field(..., min_length=1)


class UserDisableUpdate(BaseModel):
    disabled: bool


@router.post("/login")
async def admin_panel_login(body: AdminLoginRequest):
    if not verify_admin_password(body.password):
        audit_record("admin", "admin.login_failed", message="管理后台登录失败")
        raise HTTPException(status_code=401, detail="管理密码错误")
    audit_record("admin", "admin.login", message="管理后台登录成功")
    return {"token": create_admin_panel_token(), "expires_in_hours": 12}


@router.get("/overview")
async def overview(_: Annotated[None, Depends(require_admin_panel)]):
    users = await user_store.list_users()
    sessions = session_service.list_all_sessions()
    active = [s for s in sessions if s.get("status") in ("starting", "running", "waiting_user")]
    return {
        "user_count": len(users),
        "session_count": len(sessions),
        "active_session_count": len(active),
        "use_mock_sessions": USE_MOCK_SESSIONS,
    }


@router.get("/settings")
async def get_settings(_: Annotated[None, Depends(require_admin_panel)]):
    return await admin_store.get_settings()


@router.patch("/settings")
async def patch_settings(
    body: SettingsUpdate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    result = await admin_store.update_settings(body.model_dump(exclude_none=True))
    audit_record(
        "admin",
        "admin.settings_update",
        message="更新站点设置",
        detail=body.model_dump(exclude_none=True),
    )
    return result


@router.get("/llm")
async def get_shared_llm(_: Annotated[None, Depends(require_admin_panel)]):
    return await admin_store.get_shared_llm_public()


@router.patch("/llm")
async def patch_shared_llm(
    body: LlmUpdate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    data = body.model_dump()
    payload: dict[str, Any] = {}
    for key in ("base_url", "model", "subagent_model"):
        if data.get(key) is not None:
            payload[key] = data[key]
    if data.get("api_key"):
        payload["api_key"] = data["api_key"]
    result = await admin_store.update_shared_llm(payload)
    if not result.get("configured"):
        raise HTTPException(
            status_code=400,
            detail="请至少填写 API Key 或 Base URL 之一",
        )
    audit_record(
        "admin",
        "admin.llm_update",
        message="更新统一 LLM 配置",
        detail={
            "base_url": payload.get("base_url"),
            "model": payload.get("model"),
            "has_api_key": bool(data.get("api_key")),
        },
    )
    return result


@router.get("/prompt-optimize")
async def get_prompt_optimize(_: Annotated[None, Depends(require_admin_panel)]):
    return await admin_store.get_prompt_optimize_public()


@router.patch("/prompt-optimize")
async def patch_prompt_optimize(
    body: PromptOptimizeUpdate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    data = body.model_dump(exclude_none=True)
    payload: dict[str, Any] = {}
    for key in (
        "enabled",
        "base_url",
        "model",
        "system_prompt",
        "reasoning_effort",
        "thinking_enabled",
    ):
        if key in data:
            payload[key] = data[key]
    if data.get("api_key"):
        payload["api_key"] = data["api_key"]
    result = await admin_store.update_prompt_optimize(payload)
    audit_record(
        "admin",
        "admin.prompt_optimize_update",
        message="更新描述优化配置",
        detail={
            "enabled": result.get("enabled"),
            "model": result.get("model"),
            "has_api_key": bool(data.get("api_key") or result.get("configured")),
        },
    )
    return result


@router.get("/mod-name-suggest")
async def get_mod_name_suggest(_: Annotated[None, Depends(require_admin_panel)]):
    return await admin_store.get_mod_name_suggest_public()


@router.patch("/mod-name-suggest")
async def patch_mod_name_suggest(
    body: ModNameSuggestUpdate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    data = body.model_dump(exclude_none=True)
    payload: dict[str, Any] = {}
    for key in ("enabled", "base_url", "model", "system_prompt", "temperature"):
        if key in data:
            payload[key] = data[key]
    if data.get("api_key"):
        payload["api_key"] = data["api_key"]
    result = await admin_store.update_mod_name_suggest(payload)
    audit_record(
        "admin",
        "admin.mod_name_suggest_update",
        message="更新 Mod 名称生成配置",
        detail={
            "enabled": result.get("enabled"),
            "model": result.get("model"),
            "has_api_key": bool(data.get("api_key") or result.get("configured")),
        },
    )
    return result


@router.get("/plan-llm")
async def get_plan_llm(_: Annotated[None, Depends(require_admin_panel)]):
    return await admin_store.get_plan_llm_public()


@router.get("/decompile-tools")
async def get_decompile_tools(_: Annotated[None, Depends(require_admin_panel)]):
    from plan.decompile_tools import get_decompile_tools_status

    st = get_decompile_tools_status()
    return st.__dict__


@router.patch("/plan-llm")
async def patch_plan_llm(
    body: PlanLlmUpdate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    data = body.model_dump(exclude_none=True)
    payload: dict[str, Any] = {}
    for key in (
        "enabled",
        "base_url",
        "model",
        "system_prompt",
        "finalize_system_prompt",
        "temperature",
    ):
        if key in data:
            payload[key] = data[key]
    if data.get("api_key"):
        payload["api_key"] = data["api_key"]
    result = await admin_store.update_plan_llm(payload)
    audit_record(
        "admin",
        "admin.plan_llm_update",
        message="更新规划模式 LLM 配置",
        detail={
            "enabled": result.get("enabled"),
            "model": result.get("model"),
            "has_api_key": bool(data.get("api_key") or result.get("configured")),
        },
    )
    return result


@router.get("/llm-profiles")
async def get_llm_profiles(_: Annotated[None, Depends(require_admin_panel)]):
    return await admin_store.get_llm_profiles_public()


@router.post("/llm-profiles")
async def create_llm_profile(
    body: LlmProfileCreate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    try:
        result, created_id = await admin_store.create_llm_profile(body.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record(
        "admin",
        "admin.llm_profile_create",
        message="创建 LLM 配置预设",
        detail={"profile_id": created_id, "name": body.name.strip()},
    )
    return result


@router.patch("/llm-profiles/{profile_id}")
async def rename_llm_profile(
    profile_id: str,
    body: LlmProfileRename,
    _: Annotated[None, Depends(require_admin_panel)],
):
    try:
        result = await admin_store.rename_llm_profile(profile_id, body.name)
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    audit_record(
        "admin",
        "admin.llm_profile_rename",
        message="重命名 LLM 配置预设",
        detail={"profile_id": profile_id, "name": body.name.strip()},
    )
    return result


@router.post("/llm-profiles/{profile_id}/activate")
async def activate_llm_profile(
    profile_id: str,
    _: Annotated[None, Depends(require_admin_panel)],
):
    try:
        result = await admin_store.activate_llm_profile(profile_id)
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    active = next((p for p in result.get("profiles", []) if p.get("is_active")), None)
    audit_record(
        "admin",
        "admin.llm_profile_activate",
        message="切换 LLM 配置预设",
        detail={
            "profile_id": profile_id,
            "name": active.get("name") if active else profile_id,
        },
    )
    return result


@router.delete("/llm-profiles/{profile_id}")
async def delete_llm_profile(
    profile_id: str,
    _: Annotated[None, Depends(require_admin_panel)],
):
    try:
        result = await admin_store.delete_llm_profile(profile_id)
    except ValueError as exc:
        status = 404 if "不存在" in str(exc) else 400
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    audit_record(
        "admin",
        "admin.llm_profile_delete",
        message="删除 LLM 配置预设",
        detail={"profile_id": profile_id},
    )
    return result


@router.get("/default-requirements")
async def get_default_requirements(_: Annotated[None, Depends(require_admin_panel)]):
    return await admin_store.get_default_requirements()


@router.patch("/default-requirements")
async def patch_default_requirements(
    body: DefaultRequirementsUpdate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    items = [item.model_dump() for item in body.items]
    try:
        result = await admin_store.update_default_requirements(items)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit_record(
        "admin",
        "admin.default_requirements_update",
        message="更新默认其他要求",
        detail={"count": len(result.get("items") or [])},
    )
    return result


@router.get("/users")
async def list_users(_: Annotated[None, Depends(require_admin_panel)]):
    return {"users": await user_store.list_users()}


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: str,
    body: UserDisableUpdate,
    _: Annotated[None, Depends(require_admin_panel)],
):
    profile = await user_store.set_disabled(user_id, body.disabled)
    if not profile:
        raise HTTPException(status_code=404, detail="用户不存在")
    audit_record(
        "admin",
        "admin.user_disable",
        user_id=user_id,
        message="禁用用户" if body.disabled else "启用用户",
        detail={"disabled": body.disabled, "email": profile.get("email")},
    )
    return profile


def _session_matches_query(item: dict[str, Any], query: str) -> bool:
    haystack = " ".join(
        [
            str(item.get("task_title") or ""),
            str(item.get("session_id") or ""),
            str(item.get("owner_email") or ""),
            str(item.get("owner_username") or ""),
            str(item.get("owner_id") or ""),
        ]
    ).lower()
    return query in haystack


@router.get("/sessions")
async def list_all_sessions(
    _: Annotated[None, Depends(require_admin_panel)],
    q: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    users = await user_store.list_users()
    user_by_id = {u["id"]: u for u in users}

    enriched: list[dict[str, Any]] = []
    for s in await user_store.list_all_sessions():
        owner_id = s.get("owner_id")
        owner = user_by_id.get(owner_id, {})
        st = s.get("status") or ""
        enriched.append(
            {
                **s,
                "owner_username": owner.get("username") or "",
                "owner_email": s.get("owner_email") or owner.get("email") or "",
                "status_label": STATUS_LABELS.get(st, st),
                "task_title": s.get("task_title") or s.get("session_id"),
                "mode": s.get("mode") or "build",
            }
        )

    query = (q or "").strip().lower()
    if query:
        enriched = [x for x in enriched if _session_matches_query(x, query)]

    status_filter = (status or "").strip()
    if status_filter:
        enriched = [x for x in enriched if x.get("status") == status_filter]

    total = len(enriched)
    page = enriched[offset : offset + limit]
    return {"sessions": page, "total": total, "limit": limit, "offset": offset}


def _plan_matches_query(item: dict[str, Any], query: str) -> bool:
    haystack = " ".join(
        [
            str(item.get("task_title") or ""),
            str(item.get("plan_id") or ""),
            str(item.get("owner_email") or ""),
            str(item.get("owner_username") or ""),
            str(item.get("owner_id") or ""),
        ]
    ).lower()
    return query in haystack


async def _enrich_plan_owner(plan: dict[str, Any], owner_id: str) -> dict[str, Any]:
    profile = await user_store.get_profile(owner_id)
    if profile:
        plan["owner_email"] = profile.get("email", "")
        plan["owner_username"] = profile.get("username", "")
    return plan


@router.get("/plans")
async def list_all_plans(
    _: Annotated[None, Depends(require_admin_panel)],
    q: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)

    users = await user_store.list_users()
    user_by_id = {u["id"]: u for u in users}

    enriched: list[dict[str, Any]] = []
    for p in await plan_store.list_all_plans():
        owner_id = p.get("owner_id")
        owner = user_by_id.get(owner_id, {})
        st = p.get("status") or ""
        enriched.append(
            {
                **p,
                "owner_username": owner.get("username") or "",
                "owner_email": owner.get("email") or "",
                "status_label": PLAN_STATUS_LABELS.get(st, st),
                "task_title": p.get("task_title") or p.get("plan_id"),
            }
        )

    query = (q or "").strip().lower()
    if query:
        enriched = [x for x in enriched if _plan_matches_query(x, query)]

    status_filter = (status or "").strip()
    if status_filter:
        enriched = [x for x in enriched if x.get("status") == status_filter]

    total = len(enriched)
    page = enriched[offset : offset + limit]
    return {"plans": page, "total": total, "limit": limit, "offset": offset}


@router.get("/plans/{plan_id}")
async def get_plan_for_admin(
    plan_id: str,
    _: Annotated[None, Depends(require_admin_panel)],
    owner_id: str,
):
    owner_id = (owner_id or "").strip()
    if not owner_id:
        raise HTTPException(status_code=400, detail="owner_id is required")
    plan = await plan_store.get_plan(owner_id, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="plan not found")
    return await _enrich_plan_owner(plan, owner_id)


@router.post("/sessions/{session_id}/stop")
async def admin_stop_session(
    session_id: str,
    _: Annotated[None, Depends(require_admin_panel)],
):
    record = session_service.get(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="session not found")
    await session_service.stop(session_id, record.owner_id)
    return record.to_snapshot()


@router.delete("/sessions/{session_id}")
async def admin_delete_session(
    session_id: str,
    _: Annotated[None, Depends(require_admin_panel)],
):
    record = session_service.get(session_id)
    if not record:
        raise HTTPException(status_code=404, detail="session not found")
    await session_service.delete_session(session_id)
    return {"ok": True}


@router.get("/audit-logs")
async def list_audit_logs(
    _: Annotated[None, Depends(require_admin_panel)],
    limit: int = 50,
    offset: int = 0,
    category: str | None = None,
    action: str | None = None,
    user_id: str | None = None,
    session_id: str | None = None,
    level: str | None = None,
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    items, total = query_logs(
        limit=limit,
        offset=offset,
        category=category or None,
        action=action or None,
        user_id=user_id or None,
        session_id=session_id or None,
        level=level or None,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
