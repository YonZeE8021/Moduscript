"""MCmodAgent API server."""

from __future__ import annotations

from asyncio_platform import configure_asyncio_for_platform, install_quiet_proactor_handler

configure_asyncio_for_platform()

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from admin_routes import router as admin_router
from audit_log import cleanup_old_logs, record as audit_record
from auth.deps import CurrentUser, SessionCaller, get_current_user, get_optional_user, resolve_session_caller
from auth.routes import router as auth_router
from config import (
    CORS_ORIGINS,
    JWT_SECRET,
    JWT_SECRET_WEAK_DEFAULT,
    GRADLE_MAX_CONCURRENT,
    MOD_TEMPLATE_PACKAGE,
    REQUIRE_STRONG_SECRETS,
    ROOT_DIR,
    SESSION_MAX_ACTIVE,
    USE_MOCK_SESSIONS,
    UVICORN_ACCESS_LOG,
    UVICORN_GRACEFUL_TIMEOUT,
)
from availability import SessionCapacityError, RuntimeState, agent_capacity, configure_capacity, gradle_capacity
from http_utils import content_disposition_attachment
from logging_setup import setup_logging
from modrinth_client import ModrinthError, resolve_project_url
from session_service import session_service, sse_encode
from storage.admin_store import admin_store
from storage.user_store import user_store
from user_routes import router as user_router

if USE_MOCK_SESSIONS:
    from session_mock import session_store as mock_store
    from session_mock import sse_encode as mock_sse_encode

logger = logging.getLogger(__name__)

setup_logging()


def _should_audit_http(path: str, method: str) -> bool:
    if not path.startswith("/api/"):
        return False
    if method == "GET" and path == "/api/v1/sessions":
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    import sys

    if REQUIRE_STRONG_SECRETS and JWT_SECRET == JWT_SECRET_WEAK_DEFAULT:
        raise RuntimeError(
            "JWT_SECRET 仍为默认值。生产/A 测请设置强随机密钥，"
            "或关闭 MCMOD_REQUIRE_STRONG_SECRETS（仅开发）。"
        )
    elif JWT_SECRET == JWT_SECRET_WEAK_DEFAULT:
        logger.warning(
            "JWT_SECRET 使用默认值，不适合生产环境。"
            "请设置 JWT_SECRET 或 MCMOD_REQUIRE_STRONG_SECRETS=true 强制校验。"
        )

    configure_capacity(
        max_active_sessions=SESSION_MAX_ACTIVE,
        max_gradle_builds=GRADLE_MAX_CONCURRENT,
    )

    cleanup_old_logs()
    install_quiet_proactor_handler()

    loop = asyncio.get_running_loop()
    logger.info("Event loop: %s", type(loop).__name__)
    audit_record(
        "system",
        "system.startup",
        message="服务启动",
        detail={"use_mock_sessions": USE_MOCK_SESSIONS, "loop": type(loop).__name__},
    )
    if sys.platform == "win32" and type(loop).__name__ == "SelectorEventLoop":
        logger.error(
            "Windows SelectorEventLoop detected; Agent subprocess may fail. "
            "Start with: cd server && python main.py"
        )

    user_store.init()
    admin_store.init()
    await session_service.init()
    from workspace_git import git_available

    git_ok = git_available()
    if git_ok:
        logger.info("Git available for workspace checkpoints")
    else:
        logger.warning(
            "Git not found; session branch/regenerate and workspace rollback will be limited."
        )
    audit_record(
        "system",
        "system.git_check",
        level="warn" if not git_ok else "info",
        message="Git 就绪" if git_ok else "Git 未安装",
        detail={"git_available": git_ok},
    )
    if not USE_MOCK_SESSIONS:
        try:
            from agent.options import verify_cli_at_startup, verify_cli_async_transport

            ok, cli, version = verify_cli_at_startup()
            if ok:
                async_ok, async_msg = await verify_cli_async_transport()
                if async_ok:
                    logger.info("Claude CLI ready: %s (%s)", cli, version)
                    audit_record(
                        "system",
                        "system.cli_check",
                        message="Claude CLI 就绪",
                        detail={"cli": cli, "version": version},
                    )
                else:
                    logger.error("Claude CLI async subprocess check failed: %s", async_msg)
                    audit_record(
                        "system",
                        "system.cli_check",
                        level="error",
                        message="Claude CLI 异步检查失败",
                        detail={"error": async_msg},
                    )
            else:
                logger.warning("Claude CLI check failed: %s %s", cli, version)
                audit_record(
                    "system",
                    "system.cli_check",
                    level="warn",
                    message="Claude CLI 检查失败",
                    detail={"cli": cli, "version": version},
                )
        except Exception as exc:
            logger.warning("Claude CLI not available: %s", exc)
            audit_record(
                "system",
                "system.cli_check",
                level="warn",
                message="Claude CLI 不可用",
                detail={"error": str(exc)},
            )
    yield
    RuntimeState.drain()
    try:
        if USE_MOCK_SESSIONS:
            await mock_store.shutdown()
        else:
            await session_service.shutdown()
    except Exception as exc:
        logger.warning("Shutdown cleanup failed: %s", exc)
    audit_record("system", "system.shutdown", message="服务关闭")


app = FastAPI(title="MCmodAgent API", version="1.0.0", lifespan=lifespan)

_cors_kwargs: dict = {
    "allow_credentials": True,
    "allow_methods": ["*"],
    "allow_headers": ["*"],
}
if CORS_ORIGINS == ["*"]:
    _cors_kwargs["allow_origins"] = ["*"]
else:
    _cors_kwargs["allow_origins"] = CORS_ORIGINS

app.add_middleware(CORSMiddleware, **_cors_kwargs)


@app.exception_handler(SessionCapacityError)
async def session_capacity_handler(_request: Request, exc: SessionCapacityError):
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers={"Retry-After": "30"},
    )

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(admin_router)

from plan.routes import router as plan_router  # noqa: E402

app.include_router(plan_router)


@app.middleware("http")
async def audit_http_middleware(request: Request, call_next):
    path = request.url.path
    method = request.method
    skip = not _should_audit_http(path, method)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    except Exception:
        logger.exception("Request failed %s %s", method, path)
        response = JSONResponse(status_code=500, content={"detail": "服务器内部错误，请稍后重试"})
    if not skip:
        duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            audit_record(
                "http",
                "http.request",
                message=f"{method} {path} {response.status_code}",
                detail={
                    "method": method,
                    "path": path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        except Exception as exc:
            logger.warning("Audit log failed for %s %s: %s", method, path, exc)
    return response


@app.get("/health/live")
async def health_live():
    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready():
    if not RuntimeState.accepting_traffic:
        return JSONResponse(status_code=503, content={"status": "draining"})
    from workspace_git import git_available

    return {
        "status": "ready",
        "agent_capacity": agent_capacity.stats(),
        "gradle_capacity": gradle_capacity.stats(),
        "git_available": git_available(),
    }


class ModrinthResolveRequest(BaseModel):
    url: str = Field(..., min_length=1)


class ModMetadataSuggestRequest(BaseModel):
    prompt: str = ""
    task_title: str = ""
    mod_name: str | None = None
    mod_id: str | None = None
    package_name: str | None = None


class SessionCreateRequest(BaseModel):
    prompt: str = ""
    mode: str = "build"
    minecraft_version: str = ""
    mod_loader: str = ""
    platform: str = "unspecified"
    reference_mods: dict | None = None
    requirements: list[str] | None = None
    requirements_detail: dict | None = None
    locale: str = "zh-CN"
    interruption_level: int | None = None
    max_turns: int | None = None
    mod_name: str | None = None
    mod_id: str | None = None
    package_name: str | None = None
    readable_blueprint: str | None = None
    task_title: str | None = None


class SessionTitleUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


class SessionMetaUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=80)
    pinned: bool | None = None


class SessionMessageCreate(BaseModel):
    content: str = Field(..., min_length=1)


class SessionRewindRequest(BaseModel):
    node_id: str = Field(..., min_length=1)
    new_content: str | None = Field(None, min_length=1)


class SessionBranchSwitchRequest(BaseModel):
    node_id: str = Field(..., min_length=1)


class SessionActionSubmit(BaseModel):
    choice_id: str | None = None
    request_id: str | None = None
    answers: dict | None = None


def _get_store():
    return mock_store if USE_MOCK_SESSIONS else session_service


@app.get("/api/v1/site/settings")
async def public_site_settings():
    settings = await admin_store.get_settings()
    prompt_optimize = await admin_store.get_prompt_optimize_public()
    return {
        "registration_enabled": settings.get("registration_enabled", True),
        "shared_llm_enabled": settings.get("shared_llm_enabled", True),
        "beta_banner_enabled": settings.get("beta_banner_enabled", True),
        "beta_banner_text": settings.get("beta_banner_text", ""),
        "beta_agreement_url": settings.get("beta_agreement_url", ""),
        "feedback_email": settings.get("feedback_email", ""),
        "desc_optimize_enabled": bool(
            prompt_optimize.get("enabled", True) and prompt_optimize.get("configured")
        ),
        "mod_template_package": MOD_TEMPLATE_PACKAGE,
    }


@app.get("/api/v1/site/default-requirements")
async def public_default_requirements():
    return await admin_store.get_default_requirements()


@app.post("/api/v1/modrinth/resolve")
async def modrinth_resolve(body: ModrinthResolveRequest):
    try:
        return await resolve_project_url(body.url)
    except ModrinthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/v1/mod/suggest-metadata")
async def suggest_mod_metadata_route(
    body: ModMetadataSuggestRequest,
    user: CurrentUser = Depends(get_current_user),
):
    from mod_metadata import suggest_mod_metadata

    try:
        return await suggest_mod_metadata(
            prompt=body.prompt,
            task_title=body.task_title,
            mod_name=body.mod_name,
            mod_id=body.mod_id,
            package_name=body.package_name,
            user_id=user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/sessions")
async def list_sessions(
    user: CurrentUser = Depends(get_current_user),
    recycled: bool = False,
):
    store = _get_store()
    return {"sessions": store.list_sessions(user.id, recycled=recycled)}


@app.post("/api/v1/sessions")
async def create_session(body: SessionCreateRequest, user: CurrentUser = Depends(get_current_user)):
    payload = body.model_dump(exclude={"readable_blueprint", "task_title"})
    store = _get_store()
    try:
        if USE_MOCK_SESSIONS:
            record = await store.create(
                payload,
                owner_id=user.id,
                final_prompt=body.prompt,
                readable_blueprint=body.readable_blueprint or body.prompt,
                task_title=body.task_title,
            )
        else:
            record = await store.create(
                user.id,
                payload,
                final_prompt=body.prompt,
                readable_blueprint=body.readable_blueprint or body.prompt,
                task_title=body.task_title,
            )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SessionCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    sid = record.session_id
    return {
        "session_id": sid,
        "id": sid,
        "status": record.status,
        "mode": record.mode,
        "redirect_url": f"/session.html?session_id={sid}",
    }


def _require_session(session_id: str, caller: SessionCaller):
    store = _get_store()
    if USE_MOCK_SESSIONS:
        record = store.get(session_id)
        if (
            record
            and not caller.is_admin_view
            and caller.user
            and getattr(record, "owner_id", None)
            and record.owner_id != caller.user.id
        ):
            record = None
    elif caller.is_admin_view:
        record = store.get(session_id)
    else:
        record = store.get(session_id, caller.user.id)  # type: ignore[union-attr]
    if not record:
        raise HTTPException(status_code=404, detail="session not found")
    return record, store


def _effective_owner_id(caller: SessionCaller, record) -> str:
    if USE_MOCK_SESSIONS:
        return getattr(record, "owner_id", None) or (caller.actor_id or "")
    if caller.is_admin_view:
        return record.owner_id
    return caller.user.id  # type: ignore[union-attr]


def _audit_user_id(caller: SessionCaller, record=None) -> str | None:
    if caller.actor_id:
        return caller.actor_id
    if record is not None:
        return record.owner_id
    return None


def _admin_intervention_kwargs(caller: SessionCaller) -> dict:
    if not caller.is_admin_view:
        return {}
    return {
        "sent_by_admin": True,
        "admin_actor": caller.kind,
        "admin_actor_id": caller.actor_id,
    }


async def _enrich_snapshot_owner(snap: dict, owner_id: str) -> dict:
    profile = await user_store.get_profile(owner_id)
    if profile:
        snap["owner_email"] = profile.get("email", "")
        snap["owner_username"] = profile.get("username", "")
    return snap


@app.get("/api/v1/sessions/{session_id}")
async def get_session(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, _ = _require_session(session_id, caller)
    snap = record.to_snapshot()
    if caller.is_admin_view:
        owner_id = getattr(record, "owner_id", None)
        if owner_id:
            await _enrich_snapshot_owner(snap, owner_id)
    return snap


@app.get("/api/v1/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    encode = mock_sse_encode if USE_MOCK_SESSIONS else sse_encode
    owner_id = _effective_owner_id(caller, record)

    async def event_generator():
        try:
            if USE_MOCK_SESSIONS:
                async for event in store.subscribe(session_id):
                    yield encode(event)
            else:
                async for event in store.subscribe(session_id, owner_id):
                    yield encode(event)
        except KeyError as exc:
            yield encode({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.patch("/api/v1/sessions/{session_id}")
async def update_session(
    session_id: str,
    body: SessionMetaUpdate,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    if body.title is None and body.pinned is None:
        raise HTTPException(status_code=400, detail="no fields to update")
    try:
        if USE_MOCK_SESSIONS:
            if body.title is not None and body.pinned is None:
                record = await store.update_title(session_id, body.title)
            else:
                record = await store.update_session_meta(
                    session_id, title=body.title, pinned=body.pinned
                )
        else:
            record = await store.update_session_meta(
                session_id, owner_id, title=body.title, pinned=body.pinned
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/trash")
async def trash_session(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            record = await store.trash_session(session_id)
        else:
            record = await store.trash_session(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    audit_record(
        "session",
        "session.trash",
        user_id=_audit_user_id(caller, record),
        session_id=session_id,
        message="移至回收站",
    )
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/restore")
async def restore_session(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            record = await store.restore_session(session_id)
        else:
            record = await store.restore_session(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/stop")
async def stop_session(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            record = await store.stop(session_id)
        else:
            record = await store.stop(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/messages")
async def post_session_message(
    session_id: str,
    body: SessionMessageCreate,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    admin_kw = _admin_intervention_kwargs(caller)
    try:
        if USE_MOCK_SESSIONS:
            record = await store.add_message(session_id, body.content, **admin_kw)
        else:
            record = await store.add_message(session_id, owner_id, body.content, **admin_kw)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except SessionCapacityError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/regenerate")
async def regenerate_session(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            raise HTTPException(status_code=501, detail="mock sessions unsupported")
        record = await store.regenerate(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/rewind")
async def rewind_session(
    session_id: str,
    body: SessionRewindRequest,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            raise HTTPException(status_code=501, detail="mock sessions unsupported")
        record = await store.rewind(
            session_id,
            owner_id,
            node_id=body.node_id,
            new_content=body.new_content,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/branch/switch")
async def switch_session_branch(
    session_id: str,
    body: SessionBranchSwitchRequest,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            raise HTTPException(status_code=501, detail="mock sessions unsupported")
        record = await store.switch_branch(session_id, owner_id, node_id=body.node_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_snapshot()


@app.post("/api/v1/sessions/{session_id}/build")
async def build_session_artifact(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            result = await store.build_artifact(session_id)
        else:
            result = await store.build_artifact(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    audit_record(
        "artifact",
        "artifact.build",
        user_id=_audit_user_id(caller, record),
        session_id=session_id,
        message=result.get("message") or "gradlew build",
        detail={"artifact_file": result.get("artifact_file")},
    )
    return result


@app.get("/api/v1/sessions/{session_id}/workspace/entries")
async def list_session_workspace_entries(
    session_id: str,
    path: str = "",
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            return store.list_workspace_entries(session_id, path)
        return store.list_workspace_entries(session_id, owner_id, path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/sessions/{session_id}/workspace/file")
async def read_session_workspace_file(
    session_id: str,
    path: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            return store.read_workspace_file(session_id, path)
        return store.read_workspace_file(session_id, owner_id, path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc


@app.get("/api/v1/sessions/{session_id}/workspace/download")
async def download_session_workspace_file(
    session_id: str,
    path: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            file_path = store.resolve_workspace_download(session_id, path)
        else:
            file_path = store.resolve_workspace_download(session_id, owner_id, path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return FileResponse(
        file_path,
        filename=file_path.name,
        headers={"Content-Disposition": content_disposition_attachment(file_path.name)},
    )


@app.get("/api/v1/sessions/{session_id}/workspace/archive")
async def download_session_workspace_archive(
    session_id: str,
    path: str = "",
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            buf, name = store.build_workspace_archive(session_id, path)
        else:
            buf, name = store.build_workspace_archive(session_id, owner_id, path)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition_attachment(name)},
    )


@app.get("/api/v1/sessions/{session_id}/artifact")
async def download_session_artifact(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)

    if not USE_MOCK_SESSIONS:
        jar_path = session_service.get_artifact_path(session_id, owner_id)
        if jar_path and jar_path.is_file():
            name = jar_path.name
            audit_record(
                "artifact",
                "artifact.download",
                user_id=_audit_user_id(caller, record),
                session_id=session_id,
                message=f"下载产物 {name}",
                detail={"artifact_name": name},
            )
            return FileResponse(
                jar_path,
                media_type="application/java-archive",
                headers={"Content-Disposition": content_disposition_attachment(name)},
            )

    if not USE_MOCK_SESSIONS or not record.delivery:
        raise HTTPException(status_code=404, detail="artifact not found")

    name = record.delivery.get("artifact_name") or "mod.jar"
    content = (
        f"# Placeholder artifact\n# Session: {session_id}\n# Task: {record.task_title}\n"
    ).encode("utf-8")
    return Response(
        content=content,
        media_type="application/java-archive",
        headers={"Content-Disposition": content_disposition_attachment(name)},
    )


@app.post("/api/v1/sessions/{session_id}/test-server")
async def start_session_test_server(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            record = await store.start_test_server(session_id)
        else:
            record = await store.start_test_server(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "status": record.test_server_status,
        "port": record.test_server_port,
        "host": record.test_server_host,
        "address": None,
        "placeholder": record.test_server_status == "placeholder",
        "message": "封闭测试阶段暂未开放自动测试服务器，请参阅 docs/TEST_SERVER.md",
    }


@app.delete("/api/v1/sessions/{session_id}/test-server")
async def stop_session_test_server(
    session_id: str,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    try:
        if USE_MOCK_SESSIONS:
            record = await store.stop_test_server(session_id)
        else:
            record = await store.stop_test_server(session_id, owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return {"status": record.test_server_status, "message": "测试服务器已关闭"}


@app.post("/api/v1/sessions/{session_id}/actions")
async def post_session_action(
    session_id: str,
    body: SessionActionSubmit,
    caller: SessionCaller = Depends(resolve_session_caller),
):
    record, store = _require_session(session_id, caller)
    owner_id = _effective_owner_id(caller, record)
    admin_kw = _admin_intervention_kwargs(caller)
    try:
        if USE_MOCK_SESSIONS:
            if not body.choice_id and body.answers is None:
                raise ValueError("choice_id or answers required")
            record = await store.submit_action(
                session_id,
                choice_id=body.choice_id,
                answers=body.answers,
                request_id=body.request_id,
                **admin_kw,
            )
        else:
            record = await store.submit_action(
                session_id,
                owner_id,
                choice_id=body.choice_id,
                answers=body.answers,
                request_id=body.request_id,
                **admin_kw,
            )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return record.to_snapshot()


@app.post("/api/v1/prompt/optimize")
async def optimize_prompt(
    body: dict,
    user: Annotated[CurrentUser | None, Depends(get_optional_user)] = None,
):
    from prompt_optimize import optimize_description

    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")
    try:
        optimized = await optimize_description(prompt, user_id=user.id if user else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("prompt optimize failed: %s", exc)
        raise HTTPException(status_code=502, detail="优化失败，请稍后重试") from exc
    return {"optimized_prompt": optimized, "text": optimized, "content": optimized}


app.mount("/docs", StaticFiles(directory=str(ROOT_DIR / "docs")), name="docs-static")
app.mount("/", StaticFiles(directory=str(ROOT_DIR), html=True), name="static")


if __name__ == "__main__":
    import sys

    import uvicorn

    reload = os.getenv("UVICORN_RELOAD", "false").lower() in ("1", "true", "yes")
    if sys.platform == "win32":
        reload = False
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=reload,
        access_log=UVICORN_ACCESS_LOG,
        timeout_graceful_shutdown=UVICORN_GRACEFUL_TIMEOUT,
    )
