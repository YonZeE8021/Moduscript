"""Persistent session service with Claude Agent SDK integration."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from agent.event_mapper import process_agent_message
from agent.options import validate_workspace
from agent.proactor_bridge import (
    resolve_agent_permission,
    stream_agent_follow_up,
    stream_agent_prompt,
)
from agent.runner import AgentSession
from agent.trace import trace_event
from asyncio_platform import configure_asyncio_for_platform
from config import (
    AGENT_IDLE_WARN_SEC,
    CHAT_MAX_TURNS,
    CHAT_MAX_TURNS_MAX,
    CHAT_MAX_TURNS_MIN,
    MOD_TEMPLATE_MAX_RETRIES,
    MOD_TEMPLATE_RETRY_DELAY_SEC,
    SESSION_MAX_ACTIVE,
    SSE_PUBLISH_DEBOUNCE_MS,
    SSE_STRIP_HEAVY_FIELDS,
    SUPPORTED_MC_VERSION,
    SUPPORTED_MOD_LOADER,
    USE_MOCK_SESSIONS,
    WORKSPACE_ROOT,
)
from availability import SessionCapacityError, RuntimeState, agent_capacity, gradle_capacity
from task_safety import spawn_task
from mod_metadata import TITLE_GENERATING_PLACEHOLDER, resolve_initial_task_title
from audit_log import record as audit_record
from session_reference import (
    build_reference_prompt_append,
    code_refs_from_payload,
    copy_from_plan_refs,
    copy_session_refs_to_workspace,
    init_reference_index_from_payload,
    materialize_all_session_refs,
    session_context_from_payload,
)
from conversation_tree import (
    KIND_FOLLOW_UP,
    KIND_INITIAL,
    append_assistant_turn,
    append_user_follow_up,
    backfill_git_refs_from_workspace,
    ensure_tree,
    fork_assistant_sibling,
    fork_user_sibling,
    git_ref_for_path_leaf,
    git_ref_for_reset_before,
    last_assistant_on_path,
    migrate_linear_to_tree,
    path_prompt_for_rerun,
    set_node_git_refs,
    switch_branch as tree_switch_branch,
    sync_tree_to_linear,
    tree_to_snapshot_extra,
    update_assistant_turn_snapshot,
)
from storage.admin_store import admin_store
from storage.user_store import user_store
from workspace_git import (
    WorkspaceGitError,
    checkpoint as git_checkpoint,
    current_ref as git_current_ref,
    git_available,
    init_repo as git_init_repo,
    reinit_repo as git_reinit_repo,
    reset_to as git_reset_to,
    wipe_workspace_contents,
)

configure_asyncio_for_platform()

logger = logging.getLogger(__name__)

BUILD_STAGES = ["创建环境", "编写代码", "编译完成"]
FOLLOW_UP_STAGES = ["进行中", "已完成"]

AUTO_CONTINUE_AFTER_COMPACTION = (
    "上下文已自动压缩。请根据当前项目进度继续完成 mod 开发与编译，"
    "直至 build/libs 下生成 jar 产物；无需重复已完成的工作。"
)

STATUS_LABELS = {
    "starting": "创建环境中…",
    "running": "正在编写…",
    "waiting_user": "等待你的回复…",
    "completed": "已完成",
    "stopped": "已停止",
    "interrupted": "已中断",
    "error": "出错",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_task_title(readable_blueprint: str, prompt: str, fallback: str = "新任务") -> str:
    text = (readable_blueprint or prompt or "").strip()
    if not text:
        return fallback
    match = re.search(r"^##\s+(.+)$", text, re.MULTILINE)
    if match:
        return match.group(1).strip()[:80]
    first = text.splitlines()[0].lstrip("#").strip()
    return (first[:80] if first else fallback)


def _stages_for_record(record: "SessionRecord") -> list[str]:
    if record.interaction_kind == "follow_up":
        return FOLLOW_UP_STAGES
    return BUILD_STAGES


def _new_interaction_turn(interaction_index: int) -> dict[str, Any]:
    return {
        "id": f"interaction-{interaction_index}",
        "summary": "",
        "thinking": [],
        "tools": [],
        "pending_action": None,
        "user_reply": None,
        "progress": None,
    }


def _sync_turn_to_tree(record: "SessionRecord", turn: dict[str, Any]) -> None:
    tree = ensure_tree(record)
    path = tree.get("active_path") or []
    if not path:
        return
    last_id = path[-1]
    nodes = tree.get("nodes") or {}
    node = nodes.get(last_id)
    if node and node.get("type") == "assistant":
        update_assistant_turn_snapshot(tree, last_id, turn)
        if record.agent_cli_session_id:
            node["agent_cli_session_id"] = record.agent_cli_session_id


def _persist_active_turn(record: "SessionRecord") -> None:
    if not record.turns:
        return
    turn = record.turns[-1]
    _freeze_turn_progress(record, turn)
    _sync_turn_to_tree(record, turn)


def _git_not_installed_error() -> ValueError:
    return ValueError(
        "系统未安装 Git，会话分支与工作区回滚不可用。"
        "请运行 scripts\\setup.ps1 -InstallGit 或手动安装 Git。"
    )


def _require_git_for_reset(reset_ref: str | None) -> None:
    if USE_MOCK_SESSIONS:
        return
    if not git_available():
        raise _git_not_installed_error()
    if not reset_ref:
        raise ValueError("无法回滚工作区：缺少 git checkpoint")


def _raise_git_reset_error(exc: WorkspaceGitError) -> None:
    if "not found" in str(exc).lower():
        raise _git_not_installed_error() from exc
    raise ValueError(f"工作区回滚失败：{exc}") from exc


async def _checkpoint_workspace_for_node(
    record: "SessionRecord",
    node_id: str,
    message: str,
    *,
    git_ref_start: bool = False,
) -> str | None:
    if USE_MOCK_SESSIONS:
        return None
    workspace = record.workspace_path()
    try:
        ref = await asyncio.to_thread(git_checkpoint, workspace, message)
    except WorkspaceGitError as exc:
        logger.warning(
            "git checkpoint failed session=%s node=%s: %s",
            record.session_id,
            node_id,
            exc,
        )
        return None
    tree = ensure_tree(record)
    if git_ref_start:
        set_node_git_refs(tree, node_id, git_ref_start=ref)
    else:
        set_node_git_refs(tree, node_id, git_ref=ref)
        record.workspace_git_expected_ref = ref
        record.workspace_git_stale = False
    return ref


async def _checkpoint_workspace(record: "SessionRecord", message: str) -> str | None:
    tree = ensure_tree(record)
    path = tree.get("active_path") or []
    if not path:
        return None
    return await _checkpoint_workspace_for_node(record, path[-1], message)


async def _restore_workspace_for_path(record: "SessionRecord", tree: dict[str, Any]) -> None:
    if USE_MOCK_SESSIONS:
        record.workspace_git_warning = None
        _clear_workspace_stale(record, tree)
        _sync_artifact_state(record)
        return
    git_ref = git_ref_for_path_leaf(tree)
    record.workspace_git_warning = None
    if not git_ref:
        record.workspace_git_warning = "此分支缺少工作区快照，文件可能未与对话同步"
        audit_record(
            "session",
            "session.workspace_git_missing",
            user_id=record.owner_id,
            session_id=record.session_id,
            level="warn",
            message=record.workspace_git_warning,
        )
        _sync_artifact_state(record)
        return
    workspace = record.workspace_path()
    try:
        await asyncio.to_thread(git_reset_to, workspace, git_ref)
        _clear_workspace_stale(record, tree)
    except WorkspaceGitError as exc:
        record.workspace_git_warning = f"工作区回滚失败: {exc}"
        audit_record(
            "session",
            "session.workspace_git_reset_failed",
            user_id=record.owner_id,
            session_id=record.session_id,
            level="warn",
            message=str(exc)[:200],
        )
    _sync_artifact_state(record)


def _clear_workspace_stale(record: "SessionRecord", tree: dict[str, Any] | None = None) -> None:
    tree = tree or ensure_tree(record)
    record.workspace_git_expected_ref = git_ref_for_path_leaf(tree)
    record.workspace_git_stale = False


def _workspace_is_stale(record: "SessionRecord") -> bool:
    if USE_MOCK_SESSIONS:
        return False
    tree = ensure_tree(record)
    expected = record.workspace_git_expected_ref or git_ref_for_path_leaf(tree)
    if not expected:
        return False
    current = git_current_ref(record.workspace_path())
    if not current:
        return True
    return current != expected


def _mark_workspace_git_after_switch(record: "SessionRecord", tree: dict[str, Any]) -> None:
    expected = git_ref_for_path_leaf(tree)
    record.workspace_git_expected_ref = expected
    if USE_MOCK_SESSIONS:
        record.workspace_git_stale = False
        record.workspace_git_warning = None
        return
    if not expected:
        record.workspace_git_stale = False
        record.workspace_git_warning = "此分支缺少工作区快照，文件可能未与对话同步"
        audit_record(
            "session",
            "session.workspace_git_missing",
            user_id=record.owner_id,
            session_id=record.session_id,
            level="warn",
            message=record.workspace_git_warning,
        )
        return
    record.workspace_git_warning = None
    record.workspace_git_stale = _workspace_is_stale(record)


def _refresh_workspace_git_state(record: "SessionRecord") -> None:
    tree = ensure_tree(record)
    record.workspace_git_expected_ref = git_ref_for_path_leaf(tree)
    record.workspace_git_stale = _workspace_is_stale(record)


def _ensure_workspace_synced_sync(record: "SessionRecord") -> None:
    if not _workspace_is_stale(record):
        return
    tree = ensure_tree(record)
    git_ref = git_ref_for_path_leaf(tree)
    if not git_ref:
        return
    workspace = record.workspace_path()
    try:
        git_reset_to(workspace, git_ref)
        _clear_workspace_stale(record, tree)
        record.workspace_git_warning = None
    except WorkspaceGitError as exc:
        record.workspace_git_warning = f"工作区回滚失败: {exc}"
    _sync_artifact_state(record)


async def ensure_workspace_synced(record: "SessionRecord") -> None:
    if not _workspace_is_stale(record):
        return
    tree = ensure_tree(record)
    await _restore_workspace_for_path(record, tree)


def _artifact_fields_for_snapshot(record: "SessionRecord") -> tuple[bool, str | None]:
    stale = _workspace_is_stale(record) or record.workspace_git_stale
    if stale:
        if (
            record.status == "completed"
            and record.delivery
            and record.delivery.get("artifact_name")
        ):
            return True, record.delivery["artifact_name"]
        return False, None
    jar = _find_artifact(record.workspace_path())
    return jar is not None, jar.name if jar else None


def _sync_status_from_active_path(record: "SessionRecord") -> None:
    tree = ensure_tree(record)
    assistant = last_assistant_on_path(tree)
    if not assistant:
        return
    snap = assistant.get("turn_snapshot") or {}
    progress = snap.get("progress") or {}
    if progress.get("interaction_kind"):
        record.interaction_kind = progress["interaction_kind"]
    if progress.get("stage_index") is not None:
        record.stage_index = progress["stage_index"]
    if snap.get("pending_action") and not snap.get("user_reply"):
        record.status = "waiting_user"
        return
    if progress.get("stage_total") and progress.get("stage_index") is not None:
        if progress["stage_index"] >= progress["stage_total"] - 1:
            record.status = "completed"
            record.error_message = None
            return
    if snap.get("summary"):
        record.status = "completed"
        record.error_message = None


def _assert_branch_allowed(record: "SessionRecord") -> None:
    if record.branch_processing:
        raise ValueError("正在处理分支操作，请稍候")
    if record.status in {"starting", "running"}:
        raise ValueError("Agent 正在运行，请先停止")
    if record._agent_task and not record._agent_task.done():
        raise ValueError("Agent 正在运行，请先停止")
    active_turn = record.turns[-1] if record.turns else None
    if active_turn and active_turn.get("pending_action") and not active_turn.get("user_reply"):
        raise ValueError("当前轮次有待回复的操作，请先处理")
    if record.status not in {"completed", "stopped", "error", "waiting_user", "interrupted"}:
        raise ValueError("当前状态不支持此操作")


def _assert_user_node_editable(node: dict[str, Any]) -> None:
    if node.get("sent_by_admin"):
        raise ValueError("管理员介入的消息不可编辑")

def _turn_progress_snapshot(record: "SessionRecord", stage_index: int | None = None) -> dict[str, Any]:
    stages = _stages_for_record(record)
    idx = record.stage_index if stage_index is None else stage_index
    if record.interaction_kind == "follow_up":
        if record.status == "completed":
            idx = len(FOLLOW_UP_STAGES) - 1
        else:
            idx = min(max(idx, 0), len(FOLLOW_UP_STAGES) - 1)
    elif record.status == "completed" and record.interaction_kind == "build":
        idx = len(BUILD_STAGES) - 1
    else:
        idx = min(max(idx, 0), len(stages) - 1)
    return {
        "interaction_kind": record.interaction_kind,
        "stage_index": idx,
        "stage_total": len(stages),
        "stage_name": stages[idx],
        "stages": list(stages),
    }


def _freeze_turn_progress(record: "SessionRecord", turn: dict[str, Any]) -> None:
    turn["progress"] = _turn_progress_snapshot(record)


SETUP_STEP_DEFS = [
    {"id": "connect", "label": "连接 Fabric 服务器"},
    {"id": "download", "label": "模板下载中"},
    {"id": "build", "label": "构建中"},
]

SETUP_STATUS_LABELS = {
    "pending": "等待中",
    "active": "进行中",
    "done": "已完成",
    "failed": "未成功",
}


def _initial_setup_progress() -> dict[str, Any]:
    return {
        "steps": [
            {"id": step["id"], "label": step["label"], "status": "pending", "detail": None}
            for step in SETUP_STEP_DEFS
        ],
        "retry_attempt": 0,
        "retry_max": MOD_TEMPLATE_MAX_RETRIES,
        "retrying": False,
        "failed": False,
        "notice": None,
    }


def _apply_setup_progress_to_turn(record: "SessionRecord") -> None:
    if not record.setup_progress or not record.turns:
        return

    turn = record.turns[-1]
    tools: list[dict[str, str]] = []
    for step in record.setup_progress["steps"]:
        status = step.get("status", "pending")
        if status == "pending":
            continue
        prefix = {"active": "◉", "done": "✓", "failed": "·"}.get(status, "·")
        detail = step.get("detail") or SETUP_STATUS_LABELS.get(status, "")
        tools.append({"name": f"{prefix} {step['label']}", "preview": detail})
    for ref_step in record.reference_tool_steps:
        status = ref_step.get("status", "running")
        prefix = {"running": "◉", "ok": "✓", "failed": "·"}.get(status, "·")
        label = ref_step.get("label") or ref_step.get("step") or "参考索引"
        preview = ref_step.get("preview") or ""
        tools.append({"name": f"{prefix} {label}", "preview": preview})
    turn["tools"] = tools

    if record.status != "starting":
        return

    progress = record.setup_progress
    if progress.get("retrying"):
        attempt = progress.get("retry_attempt") or 0
        retry_max = progress.get("retry_max") or MOD_TEMPLATE_MAX_RETRIES
        turn["summary"] = f"正在重试获取 Fabric 模板（第 {attempt}/{retry_max} 次）…"
        return

    active = next((step for step in progress["steps"] if step.get("status") == "active"), None)
    ref_active = next(
        (s for s in record.reference_tool_steps if s.get("status") == "running"),
        None,
    )
    if ref_active:
        turn["summary"] = f"正在索引参考模组：{ref_active.get('label') or '处理中'}…"
    elif active:
        turn["summary"] = f"正在创建环境：{active['label']}…"
    else:
        turn["summary"] = "正在创建 Fabric 开发环境…"

    _sync_turn_to_tree(record, turn)


def _setup_failure_notice(retry_max: int) -> dict[str, str]:
    return {
        "title": "环境创建失败",
        "body": "Moduscript 服务器无法获取到 Fabric 模板，工作区未初始化",
        "hint": "这不是你的错，通常是网络波动导致。Moduscript 会持续尝试继续获取，并执行接下来的任务。",
        "retry_text": f"已重试 {retry_max} 次仍未成功，将继续编写代码。",
    }


def _clamp_max_turns(value: int | None) -> int:
    if value is None:
        return CHAT_MAX_TURNS
    try:
        n = int(value)
    except (TypeError, ValueError):
        return CHAT_MAX_TURNS
    return max(CHAT_MAX_TURNS_MIN, min(CHAT_MAX_TURNS_MAX, n))


def _resolve_max_turns(record: "SessionRecord") -> int:
    raw = (record.payload or {}).get("max_turns")
    return _clamp_max_turns(raw if raw is not None else None)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from mod_metadata_validation import normalize_mod_id, validate_mod_metadata_fields

    normalized = deepcopy(payload)
    normalized["minecraft_version"] = SUPPORTED_MC_VERSION
    normalized["mod_loader"] = SUPPORTED_MOD_LOADER
    for key in ("mod_name", "mod_id", "package_name"):
        if normalized.get(key):
            normalized[key] = str(normalized[key]).strip()
    check = validate_mod_metadata_fields(
        mod_name=normalized.get("mod_name"),
        mod_id=normalized.get("mod_id"),
        package_name=normalized.get("package_name"),
    )
    if not check.valid:
        raise ValueError(check.message)
    if normalized.get("mod_id"):
        normalized["mod_id"] = normalize_mod_id(normalized["mod_id"])
    if normalized.get("package_name"):
        normalized["package_name"] = normalized["package_name"].strip().lower()
    if "max_turns" in normalized and normalized["max_turns"] is not None:
        normalized["max_turns"] = _clamp_max_turns(normalized["max_turns"])
    return normalized


def _build_delivery(record: "SessionRecord", jar_name: str | None = None) -> dict[str, Any]:
    from http_utils import artifact_slug

    delivery: dict[str, Any] = {
        "download_path": f"/api/v1/sessions/{record.session_id}/artifact",
        "test_server_path": f"/api/v1/sessions/{record.session_id}/test-server",
        "project_path": str(record.workspace_path()),
    }
    if jar_name:
        delivery["artifact_name"] = jar_name
    else:
        slug = artifact_slug(
            record.task_title,
            payload=record.payload,
            session_id=record.session_id,
        )
        delivery["artifact_name"] = f"{slug}.jar"
    return delivery


def _sync_artifact_state(record: "SessionRecord") -> None:
    try:
        jar = _find_artifact(record.workspace_path())
    except OSError as exc:
        logger.warning("artifact sync skipped session=%s: %s", record.session_id, exc)
        return
    if not jar:
        return
    if not record.delivery:
        record.delivery = _build_delivery(record, jar.name)
    else:
        record.delivery["artifact_name"] = jar.name


def _delivery_base(record: "SessionRecord") -> dict[str, Any]:
    return {
        "download_path": f"/api/v1/sessions/{record.session_id}/artifact",
        "test_server_path": f"/api/v1/sessions/{record.session_id}/test-server",
        "project_path": str(record.workspace_path()),
    }


async def _finalize_build_success(
    service: "SessionService",
    record: "SessionRecord",
    *,
    warning: str | None = None,
) -> None:
    jar = _find_artifact(record.workspace_path())
    if not jar:
        record.status = "error"
        record.error_message = "编译未完成：build/libs 中未找到产物 jar"
        record.updated_at = _utc_now()
        if not record.delivery:
            record.delivery = _delivery_base(record)
        else:
            record.delivery.update(_delivery_base(record))
        await service._persist(record)
        await service._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        audit_record(
            "session",
            "session.error",
            user_id=record.owner_id,
            session_id=record.session_id,
            level="error",
            message=record.error_message,
        )
        return

    record.stage_index = len(BUILD_STAGES) - 1
    record.status = "completed"
    record.error_message = warning
    record.updated_at = _utc_now()
    _sync_artifact_state(record)
    if record.turns:
        _freeze_turn_progress(record, record.turns[-1])
        _sync_turn_to_tree(record, record.turns[-1])
    await _checkpoint_workspace(record, "build complete")
    if not record.delivery:
        record.delivery = _delivery_base(record)
    else:
        record.delivery.update(_delivery_base(record))
    await service._persist(record)
    await service._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
    action = "agent.soft_complete" if warning else "session.complete"
    audit_record(
        "session" if not warning else "agent",
        action,
        user_id=record.owner_id,
        session_id=record.session_id,
        message="编译完成" if not warning else warning[:120],
        level="warn" if warning else "info",
        detail={
            "stage_index": record.stage_index,
            "artifact": record.delivery.get("artifact_name") if record.delivery else None,
        },
    )


def _jar_mtime(path: Path) -> float:
    try:
        if path.is_file():
            return path.stat().st_mtime
    except OSError:
        pass
    return -1.0


def _find_artifact(workspace: Path) -> Path | None:
    libs = workspace / "build" / "libs"
    if not libs.is_dir():
        for sub in workspace.rglob("build/libs"):
            if sub.is_dir():
                libs = sub
                break
    if not libs.is_dir():
        return None
    jars = [
        p
        for p in libs.glob("*.jar")
        if not any(x in p.name.lower() for x in ("-sources", "-javadoc", "-dev"))
    ]
    scored = [(p, _jar_mtime(p)) for p in jars]
    scored = [(p, mtime) for p, mtime in scored if mtime >= 0]
    if not scored:
        return None
    return max(scored, key=lambda item: item[1])[0]


def _has_artifact(workspace: Path) -> bool:
    try:
        return _find_artifact(workspace) is not None
    except OSError:
        return False


def _merge_agent_run(record: "SessionRecord", **updates: Any) -> None:
    base = dict(record.agent_run or {})
    base.update({k: v for k, v in updates.items() if v is not None})
    record.agent_run = base


def _apply_side_events(record: "SessionRecord", side_events: list[dict[str, Any]]) -> None:
    for ev in side_events:
        if ev.get("type") == "compaction":
            _merge_agent_run(record, compacted=True)
        elif ev.get("type") == "result":
            _merge_agent_run(
                record,
                num_turns=ev.get("num_turns", ev.get("turns")),
                duration_ms=ev.get("duration_ms"),
                cost_usd=ev.get("cost_usd"),
            )
            session_id = (ev.get("session_id") or "").strip()
            if session_id:
                record.agent_cli_session_id = session_id
        elif ev.get("type") == "error":
            _merge_agent_run(record, last_error=ev.get("message"))
        elif ev.get("type") == "tool_use":
            _merge_agent_run(record, last_tool=ev.get("name"))


def _format_duration_ms(ms: int | float | None) -> str:
    if ms is None:
        return "?"
    sec = int(ms) // 1000
    if sec < 60:
        return f"{sec}s"
    minutes = sec // 60
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h{minutes % 60}m"


def _format_agent_run_diagnostic(agent_run: dict[str, Any] | None) -> str:
    if not agent_run:
        return ""
    num = agent_run.get("num_turns")
    max_t = agent_run.get("max_turns")
    dur = _format_duration_ms(agent_run.get("duration_ms"))
    reason = agent_run.get("exit_reason") or "unknown"
    turns_part = f"{num}/{max_t} 轮" if num is not None and max_t else ""
    parts = [p for p in [turns_part, dur, reason] if p]
    if not parts:
        return ""
    return f"（Agent 已结束，{' · '.join(parts)}）"


def _resolve_build_exit_reason(record: "SessionRecord") -> str:
    agent_run = record.agent_run or {}
    num_turns = agent_run.get("num_turns")
    max_turns = agent_run.get("max_turns") or _resolve_max_turns(record)
    artifact = _has_artifact(record.workspace_path())
    if num_turns is not None and num_turns >= max_turns:
        return "max_turns_reached"
    if artifact:
        return "completed_with_jar"
    if agent_run.get("compacted") and num_turns is not None and num_turns < max_turns:
        return "compacted_early_stop"
    return "completed_no_jar"


def _create_agent_session(
    record: "SessionRecord",
    workspace: Path,
    *,
    on_permission: Any = None,
    max_turns: int | None = None,
) -> AgentSession:
    return AgentSession(
        record.session_id,
        str(workspace),
        record.owner_id,
        record.mode,
        on_permission_request=on_permission,
        max_turns=max_turns if max_turns is not None else _resolve_max_turns(record),
        cli_resume_id=record.agent_cli_session_id,
    )


def _friendly_session_error(exc: BaseException, record: "SessionRecord | None" = None) -> str:
    msg = str(exc).strip()
    if "await wasn't used with future" in msg:
        if record and _has_artifact(record.workspace_path()):
            return "服务端推送更新时出错；mod 可能已编译完成，请刷新页面或下载 jar"
        return "服务端推送更新时出错，请刷新页面查看最新进度"
    if isinstance(exc, PermissionError) or "PermissionError" in type(exc).__name__:
        return "保存会话数据时文件被占用，请稍后刷新页面"
    if record and record.agent_run:
        reason = record.agent_run.get("exit_reason")
        if reason == "max_turns_reached":
            max_t = record.agent_run.get("max_turns") or _resolve_max_turns(record)
            return f"Agent 达到轮次上限（{max_t}），可发送跟进消息继续修改或编译"
    if msg:
        return msg[:500]
    return "会话执行出错，请刷新页面或发送跟进消息重试"


def _get_sse_flush_lock(record: "SessionRecord") -> asyncio.Lock:
    if record._sse_flush_lock is None:
        record._sse_flush_lock = asyncio.Lock()
    return record._sse_flush_lock


def _audit_agent_stream_end(record: "SessionRecord") -> None:
    run = record.agent_run or {}
    detail = {
        "exit_reason": run.get("exit_reason"),
        "num_turns": run.get("num_turns"),
        "max_turns": run.get("max_turns"),
        "duration_ms": run.get("duration_ms"),
        "cost_usd": run.get("cost_usd"),
        "artifact_found": run.get("artifact_found"),
        "last_tool": run.get("last_tool"),
    }
    logger.info(
        "Agent stream ended session=%s exit_reason=%s turns=%s/%s duration_ms=%s artifact=%s",
        record.session_id,
        detail.get("exit_reason"),
        detail.get("num_turns"),
        detail.get("max_turns"),
        detail.get("duration_ms"),
        detail.get("artifact_found"),
    )
    audit_record(
        "agent",
        "agent.stream_end",
        user_id=record.owner_id,
        session_id=record.session_id,
        message=f"Agent 流结束 ({run.get('exit_reason', 'unknown')})",
        detail=detail,
    )


def _merge_turn_patch(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, val in incoming.items():
        if key == "turn_patch":
            tp_base = dict(out.get("turn_patch") or {})
            tp_in = val or {}
            merged_tp = dict(tp_base)
            if tp_in.get("turn_id"):
                merged_tp["turn_id"] = tp_in["turn_id"]
            if tp_in.get("summary_append"):
                merged_tp["summary_append"] = (merged_tp.get("summary_append") or "") + tp_in[
                    "summary_append"
                ]
            if tp_in.get("thinking_append"):
                prev = merged_tp.get("thinking_append") or {}
                inc = tp_in["thinking_append"]
                if prev.get("index") == inc.get("index"):
                    merged_tp["thinking_append"] = {
                        "index": inc.get("index", 0),
                        "text": (prev.get("text") or "") + (inc.get("text") or ""),
                    }
                else:
                    merged_tp["thinking_append"] = inc
            if tp_in.get("tool_added"):
                merged_tp["tool_added"] = tp_in["tool_added"]
            out["turn_patch"] = merged_tp
        else:
            out[key] = val
    return out


def _build_sse_patch(
    record: "SessionRecord", turn: dict[str, Any], side_events: list[dict[str, Any]]
) -> dict[str, Any]:
    stages = _stages_for_record(record)
    stage_index = record.stage_index
    if record.interaction_kind == "follow_up" and record.status == "completed":
        stage_index = len(FOLLOW_UP_STAGES) - 1
    patch: dict[str, Any] = {
        "updated_at": record.updated_at,
        "status": record.status,
        "stage_index": stage_index,
        "stage_name": stages[min(stage_index, len(stages) - 1)],
        "compose_enabled": record.status in {"waiting_user", "completed", "stopped", "error"},
        "can_stop": record.status in {"starting", "running", "waiting_user"},
    }
    if record.agent_run:
        patch["agent_run"] = deepcopy(record.agent_run)
    turn_patch: dict[str, Any] = {"turn_id": turn.get("id")}
    for ev in side_events:
        if ev.get("type") == "delta" and ev.get("field") == "summary":
            turn_patch["summary_append"] = (turn_patch.get("summary_append") or "") + ev.get("append", "")
        elif ev.get("type") == "delta" and ev.get("field") == "thinking":
            idx = ev.get("index", 0)
            prev = turn_patch.get("thinking_append")
            if prev and prev.get("index") == idx:
                turn_patch["thinking_append"] = {
                    "index": idx,
                    "text": (prev.get("text") or "") + ev.get("append", ""),
                }
            else:
                turn_patch["thinking_append"] = {"index": idx, "text": ev.get("append", "")}
        elif ev.get("type") in ("tool_use", "tool_result") and ev.get("tool"):
            turn_patch["tool_added"] = deepcopy(ev["tool"])
    if len(turn_patch) > 1:
        patch["turn_patch"] = turn_patch
    return patch


def _trace_side_events(
    record: "SessionRecord", side_events: list[dict[str, Any]], msg_type: str
) -> None:
    for ev in side_events:
        ev_type = ev.get("type")
        if ev_type == "tool_use":
            trace_event(
                record.session_id,
                "tool.use",
                owner_id=record.owner_id,
                detail={"name": ev.get("name"), "preview": (ev.get("tool") or {}).get("preview")},
            )
        elif ev_type == "tool_result":
            trace_event(
                record.session_id,
                "tool.result",
                owner_id=record.owner_id,
                detail={"preview": (ev.get("tool") or {}).get("preview")},
            )
        elif ev_type == "result":
            trace_event(
                record.session_id,
                "msg.result",
                owner_id=record.owner_id,
                detail={
                    "num_turns": ev.get("num_turns"),
                    "duration_ms": ev.get("duration_ms"),
                    "cost_usd": ev.get("cost_usd"),
                },
            )
        elif ev_type == "delta":
            trace_event(
                record.session_id,
                "msg.delta",
                owner_id=record.owner_id,
                detail={"field": ev.get("field"), "len": len(ev.get("append") or "")},
            )
    trace_event(
        record.session_id,
        "msg.received",
        owner_id=record.owner_id,
        detail={"msg_type": msg_type, "side_events": [e.get("type") for e in side_events]},
    )


async def _consume_agent_stream(
    service: "SessionService",
    record: SessionRecord,
    turn: dict[str, Any],
    messages: AsyncIterator[Any],
    stream_state: dict[str, Any],
) -> str:
    """Consume one agent message stream. Returns stopped | waiting_user | done."""
    async for message in messages:
        if record.status == "stopped":
            _merge_agent_run(
                record,
                ended_at=_utc_now(),
                exit_reason="stopped",
                artifact_found=_has_artifact(record.workspace_path()),
            )
            _audit_agent_stream_end(record)
            _persist_active_turn(record)
            return "stopped"
        if record.status == "waiting_user":
            await asyncio.sleep(0.3)
            continue

        turn, side_events = process_agent_message(
            message, stream_state=stream_state, turn=turn
        )
        _apply_side_events(record, side_events)
        _sync_turn_to_tree(record, turn)
        record.updated_at = _utc_now()
        _trace_side_events(record, side_events, type(message).__name__)

        patch = _build_sse_patch(record, turn, side_events)
        event_types = {ev.get("type") for ev in side_events}
        immediate = bool(event_types & {"tool_use", "tool_result", "result", "error"})
        if side_events:
            await service._publish_patch(record, patch, immediate=immediate)

    if record.status == "waiting_user":
        return "waiting_user"
    return "done"


@dataclass
class SessionRecord:
    session_id: str
    owner_id: str
    payload: dict[str, Any]
    mode: str
    final_prompt: str
    readable_blueprint: str
    task_title: str
    status: str = "starting"
    interaction_kind: str = "build"
    stage_index: int = 0
    turns: list[dict[str, Any]] = field(default_factory=list)
    user_messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    delivery: dict[str, Any] | None = None
    test_server_status: str = "idle"
    test_server_port: int | None = None
    test_server_host: str = "127.0.0.1"
    error_message: str | None = None
    agent_run: dict[str, Any] | None = None
    agent_cli_session_id: str | None = None
    setup_progress: dict[str, Any] | None = None
    reference_index: dict[str, Any] | None = None
    reference_tool_steps: list[dict[str, Any]] = field(default_factory=list, repr=False)
    pinned: bool = False
    deleted_at: str | None = None
    conversation_tree: dict[str, Any] | None = None
    branch_processing: bool = False
    workspace_git_warning: str | None = None
    workspace_git_stale: bool = False
    workspace_git_expected_ref: str | None = None
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list, repr=False)
    _agent_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _agent: AgentSession | None = field(default=None, repr=False)
    _persist_pending: bool = field(default=False, repr=False)
    _sse_debounce_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _sse_pending_patch: dict[str, Any] | None = field(default=None, repr=False)
    _sse_flush_lock: asyncio.Lock | None = field(default=None, repr=False)
    _idle_watch_task: asyncio.Task[None] | None = field(default=None, repr=False)

    def workspace_path(self) -> Path:
        return WORKSPACE_ROOT / self.owner_id / self.session_id

    def to_snapshot(self, *, include_heavy: bool = True) -> dict[str, Any]:
        stages = _stages_for_record(self)
        pending = None
        active_turn = self.turns[-1] if self.turns else None
        if self.status == "waiting_user" and active_turn:
            pending = active_turn.get("pending_action")

        status_label = STATUS_LABELS.get(self.status, self.status)
        if self.error_message and self.status != "completed":
            status_label = f"{status_label}：{self.error_message[:60]}"

        artifact_ready, artifact_file = _artifact_fields_for_snapshot(self)

        stage_index = self.stage_index
        if self.interaction_kind == "follow_up":
            if self.status == "completed":
                stage_index = len(FOLLOW_UP_STAGES) - 1
            elif self.status in {"running", "starting", "waiting_user"}:
                stage_index = min(max(self.stage_index, 0), len(FOLLOW_UP_STAGES) - 1)
        elif artifact_ready and self.status == "completed" and self.interaction_kind == "build":
            stage_index = len(BUILD_STAGES) - 1
        stage_name = stages[min(stage_index, len(stages) - 1)]

        delivery = deepcopy(self.delivery) if self.delivery else {}
        delivery["project_path"] = str(self.workspace_path())

        snap: dict[str, Any] = {
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "mode": self.mode,
            "status": self.status,
            "status_label": status_label,
            "task_title": self.task_title,
            "mod_name": (self.payload.get("mod_name") or "").strip(),
            "payload": deepcopy(self.payload),
            "final_prompt": self.final_prompt,
            "readable_blueprint": self.readable_blueprint,
            "interaction_kind": self.interaction_kind,
            "stage_index": stage_index,
            "stage_name": stage_name,
            "stage_total": len(stages),
            "stages": stages,
            "turns": deepcopy(self.turns),
            "user_messages": deepcopy(self.user_messages),
            "pending_action": deepcopy(pending) if pending else None,
            "compose_enabled": self.status in {"waiting_user", "completed", "stopped", "error"},
            "can_stop": self.status in {"starting", "running", "waiting_user"},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "delivery": delivery,
            "artifact_ready": artifact_ready,
            "artifact_file": artifact_file,
            "pinned": self.pinned,
            "deleted_at": self.deleted_at,
            "setup_progress": deepcopy(self.setup_progress) if self.setup_progress else None,
            "reference_index": deepcopy(self.reference_index) if self.reference_index else None,
            "agent_run": deepcopy(self.agent_run) if self.agent_run else None,
            "agent_cli_session_id": self.agent_cli_session_id,
            "branch_processing": self.branch_processing,
            "workspace_git_warning": self.workspace_git_warning,
            "workspace_git_stale": _workspace_is_stale(self) or self.workspace_git_stale,
            **tree_to_snapshot_extra(ensure_tree(self)),
            "test_server": {
                "status": self.test_server_status,
                "port": self.test_server_port,
                "host": self.test_server_host,
                "address": (
                    f"{self.test_server_host}:{self.test_server_port}"
                    if self.test_server_status == "running" and self.test_server_port
                    else None
                ),
                "placeholder": self.test_server_status == "placeholder",
            },
        }
        if not include_heavy and SSE_STRIP_HEAVY_FIELDS:
            snap.pop("final_prompt", None)
            snap.pop("readable_blueprint", None)
            snap.pop("payload", None)
        return snap

    def to_persisted(self) -> dict[str, Any]:
        snap = self.to_snapshot()
        snap.pop("pending_action", None)
        return snap

    @classmethod
    def from_persisted(cls, data: dict[str, Any]) -> "SessionRecord":
        return cls(
            session_id=data["session_id"],
            owner_id=data["owner_id"],
            payload=data.get("payload") or {},
            mode=data.get("mode") or "build",
            final_prompt=data.get("final_prompt") or "",
            readable_blueprint=data.get("readable_blueprint") or "",
            task_title=data.get("task_title") or "新任务",
            status=data.get("status") or "stopped",
            interaction_kind=data.get("interaction_kind") or "build",
            stage_index=data.get("stage_index") or 0,
            turns=data.get("turns") or [],
            user_messages=data.get("user_messages") or [],
            created_at=data.get("created_at") or _utc_now(),
            updated_at=data.get("updated_at") or _utc_now(),
            delivery=data.get("delivery"),
            test_server_status=(data.get("test_server") or {}).get("status", "idle"),
            test_server_port=(data.get("test_server") or {}).get("port"),
            test_server_host=(data.get("test_server") or {}).get("host", "127.0.0.1"),
            error_message=data.get("error_message"),
            setup_progress=data.get("setup_progress"),
            reference_index=data.get("reference_index"),
            agent_run=data.get("agent_run"),
            agent_cli_session_id=data.get("agent_cli_session_id"),
            pinned=bool(data.get("pinned")),
            deleted_at=data.get("deleted_at"),
            conversation_tree=data.get("conversation_tree"),
            branch_processing=bool(data.get("branch_processing")),
            workspace_git_warning=data.get("workspace_git_warning"),
            workspace_git_stale=bool(data.get("workspace_git_stale")),
            workspace_git_expected_ref=data.get("workspace_git_expected_ref"),
        )


class SessionService:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()
        self._persist_tasks: dict[str, asyncio.Task[None]] = {}

    async def init(self) -> None:
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        users = await user_store.list_users()
        loaded = 0
        skipped = 0
        for user in users:
            sessions = await user_store.list_sessions(user["id"])
            for data in sessions:
                session_id = (data or {}).get("session_id") or "unknown"
                try:
                    record = SessionRecord.from_persisted(data)
                    ensure_tree(record)
                    sync_tree_to_linear(record)
                    if not USE_MOCK_SESSIONS:
                        tree = ensure_tree(record)
                        head = git_current_ref(record.workspace_path())
                        backfilled = backfill_git_refs_from_workspace(tree, head)
                        record.conversation_tree = tree
                        _refresh_workspace_git_state(record)
                        if backfilled:
                            record.updated_at = _utc_now()
                            await self._persist(record)
                    if record.status in {"starting", "running", "waiting_user"}:
                        record.status = "interrupted"
                        record.updated_at = _utc_now()
                        await self._persist(record)
                    self._sessions[record.session_id] = record
                    loaded += 1
                except Exception as exc:
                    skipped += 1
                    logger.error(
                        "Skip corrupt session user=%s session=%s: %s",
                        user.get("id"),
                        session_id,
                        exc,
                        exc_info=True,
                    )
        if skipped:
            logger.warning("Session init: loaded=%s skipped=%s", loaded, skipped)

    async def _spawn_agent_task(
        self,
        record: SessionRecord,
        coro: Any,
        *,
        task_kind: str = "agent",
    ) -> None:
        if not RuntimeState.accepting_traffic:
            raise SessionCapacityError("服务正在维护，请稍后重试")
        if not await agent_capacity.try_acquire():
            limit = SESSION_MAX_ACTIVE
            raise SessionCapacityError(
                f"当前并发编写任务已满（上限 {limit}），请稍后再试"
            )

        async def _wrapped() -> None:
            try:
                await coro
            finally:
                await agent_capacity.release()

        record._agent_task = spawn_task(
            _wrapped(),
            name=f"{task_kind}-{record.session_id}",
            logger=logger,
        )

    def _require(self, session_id: str, owner_id: str | None = None) -> SessionRecord:
        record = self._sessions.get(session_id)
        if not record:
            raise KeyError(session_id)
        if owner_id and record.owner_id != owner_id:
            raise PermissionError("forbidden")
        return record

    async def _persist(self, record: SessionRecord) -> None:
        await user_store.save_session(record.owner_id, record.to_persisted())

    async def _schedule_persist(self, record: SessionRecord) -> None:
        sid = record.session_id

        async def _do() -> None:
            await asyncio.sleep(0.5)
            await self._persist(record)
            self._persist_tasks.pop(sid, None)

        if sid in self._persist_tasks and not self._persist_tasks[sid].done():
            return
        self._persist_tasks[sid] = asyncio.create_task(_do())

    async def _emit(self, record: SessionRecord, event: dict[str, Any]) -> None:
        await self._schedule_persist(record)
        for queue in list(record.subscribers):
            try:
                await queue.put(event)
            except Exception as exc:
                logger.warning(
                    "SSE queue put failed session=%s: %s",
                    record.session_id,
                    exc,
                )

    async def _safe_sync_artifact(self, record: SessionRecord) -> None:
        try:
            _sync_artifact_state(record)
        except OSError as exc:
            logger.warning("artifact sync skipped session=%s: %s", record.session_id, exc)
            trace_event(
                record.session_id,
                "artifact.sync_failed",
                owner_id=record.owner_id,
                detail={"error": str(exc)[:200]},
            )

    async def _cancel_debounce_task(self, record: SessionRecord) -> None:
        task = record._sse_debounce_task
        current = asyncio.current_task()
        if task and not task.done() and task is not current:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        record._sse_debounce_task = None

    async def _emit_pending_patch(self, record: SessionRecord) -> None:
        lock = _get_sse_flush_lock(record)
        async with lock:
            pending = record._sse_pending_patch
            record._sse_pending_patch = None
            if not pending:
                return
            await self._safe_sync_artifact(record)
            pending.setdefault("updated_at", record.updated_at)
            await self._emit(record, {"type": "patch", "data": pending})

    async def _flush_debounced_patch(self, record: SessionRecord) -> None:
        await self._cancel_debounce_task(record)
        await self._emit_pending_patch(record)

    async def _schedule_patch(self, record: SessionRecord, patch: dict[str, Any]) -> None:
        pending = record._sse_pending_patch or {}
        record._sse_pending_patch = _merge_turn_patch(pending, patch)

        if record._sse_debounce_task and not record._sse_debounce_task.done():
            return

        async def _debounced() -> None:
            try:
                await asyncio.sleep(SSE_PUBLISH_DEBOUNCE_MS / 1000.0)
            except asyncio.CancelledError:
                return
            record._sse_debounce_task = None
            try:
                await self._emit_pending_patch(record)
            except Exception as exc:
                logger.exception("SSE debounce flush failed session=%s", record.session_id)
                trace_event(
                    record.session_id,
                    "sse.flush_failed",
                    owner_id=record.owner_id,
                    detail={"error": str(exc)[:200]},
                )

        record._sse_debounce_task = asyncio.create_task(_debounced())

    async def _publish_patch(
        self, record: SessionRecord, patch: dict[str, Any], *, immediate: bool = False
    ) -> None:
        try:
            if immediate or SSE_PUBLISH_DEBOUNCE_MS <= 0:
                await self._flush_debounced_patch(record)
                merged = _merge_turn_patch({}, patch)
                await self._safe_sync_artifact(record)
                merged.setdefault("updated_at", record.updated_at)
                await self._emit(record, {"type": "patch", "data": merged})
                return
            await self._schedule_patch(record, patch)
        except Exception as exc:
            logger.exception("SSE publish_patch failed session=%s", record.session_id)
            trace_event(
                record.session_id,
                "sse.publish_failed",
                owner_id=record.owner_id,
                detail={"error": str(exc)[:200], "immediate": immediate},
            )

    async def _safe_publish(
        self,
        record: SessionRecord,
        event: dict[str, Any],
        *,
        include_heavy: bool | None = None,
    ) -> None:
        try:
            await self._publish(record, event, include_heavy=include_heavy)
        except Exception as exc:
            logger.exception("SSE publish failed session=%s", record.session_id)
            trace_event(
                record.session_id,
                "sse.publish_failed",
                owner_id=record.owner_id,
                detail={"error": str(exc)[:200], "type": event.get("type")},
            )

    def _stop_idle_watch(self, record: SessionRecord) -> None:
        if record._idle_watch_task and not record._idle_watch_task.done():
            record._idle_watch_task.cancel()
        record._idle_watch_task = None

    def _start_idle_watch(self, record: SessionRecord) -> None:
        self._stop_idle_watch(record)
        if AGENT_IDLE_WARN_SEC <= 0:
            return

        async def _watch() -> None:
            try:
                while record.status in {"starting", "running"}:
                    await asyncio.sleep(AGENT_IDLE_WARN_SEC)
                    if record.status not in {"starting", "running"}:
                        break
                    run = record.agent_run or {}
                    trace_event(
                        record.session_id,
                        "idle.warn",
                        owner_id=record.owner_id,
                        detail={
                            "silent_sec": AGENT_IDLE_WARN_SEC,
                            "last_tool": run.get("last_tool"),
                            "num_turns": run.get("num_turns"),
                        },
                    )
            except asyncio.CancelledError:
                pass

        record._idle_watch_task = asyncio.create_task(_watch())

    async def _publish(
        self,
        record: SessionRecord,
        event: dict[str, Any],
        *,
        include_heavy: bool | None = None,
    ) -> None:
        await self._flush_debounced_patch(record)
        await self._safe_sync_artifact(record)
        if event.get("type") == "snapshot":
            heavy = True if include_heavy is None else include_heavy
            if include_heavy is None and SSE_STRIP_HEAVY_FIELDS:
                heavy = False
            event = {**event, "data": record.to_snapshot(include_heavy=heavy)}
        await self._emit(record, event)

    def list_sessions(self, owner_id: str, *, recycled: bool = False) -> list[dict[str, Any]]:
        items = [s for s in self._sessions.values() if s.owner_id == owner_id]
        if recycled:
            items = [s for s in items if s.deleted_at]
        else:
            items = [s for s in items if not s.deleted_at]
        items.sort(key=lambda s: s.created_at, reverse=True)
        items.sort(key=lambda s: s.pinned, reverse=True)
        return [
            {
                "session_id": s.session_id,
                "task_title": s.task_title,
                "status": s.status,
                "status_label": STATUS_LABELS.get(s.status, s.status),
                "mode": s.mode,
                "created_at": s.created_at,
                "pinned": s.pinned,
                "deleted_at": s.deleted_at,
                "stage_name": _stages_for_record(s)[min(s.stage_index, len(_stages_for_record(s)) - 1)],
            }
            for s in items
        ]

    def list_all_sessions(self) -> list[dict[str, Any]]:
        items = sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)
        result = []
        for s in items:
            entry = self.list_sessions(s.owner_id)
            for e in entry:
                if e["session_id"] == s.session_id:
                    e["owner_id"] = s.owner_id
                    result.append(e)
                    break
        return result

    async def create(
        self,
        owner_id: str,
        payload: dict[str, Any],
        *,
        final_prompt: str | None = None,
        readable_blueprint: str | None = None,
        task_title: str | None = None,
    ) -> SessionRecord:
        if not USE_MOCK_SESSIONS:
            if not RuntimeState.accepting_traffic:
                raise SessionCapacityError("服务正在维护，请稍后重试")
            if not await agent_capacity.has_capacity():
                raise SessionCapacityError(
                    f"当前并发编写任务已满（上限 {SESSION_MAX_ACTIVE}），请稍后再试"
                )

        llm = await admin_store.resolve_llm_for_user(owner_id)
        if not llm and not USE_MOCK_SESSIONS:
            raise RuntimeError("未配置 LLM，请在设置或管理后台添加 API")

        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        prompt = final_prompt or payload.get("prompt") or ""
        blueprint = readable_blueprint or prompt
        normalized_payload = _normalize_payload(payload)
        title = resolve_initial_task_title(normalized_payload, task_title)

        workspace = validate_workspace(WORKSPACE_ROOT / owner_id / session_id, WORKSPACE_ROOT)

        record = SessionRecord(
            session_id=session_id,
            owner_id=owner_id,
            payload=normalized_payload,
            mode=normalized_payload.get("mode") or "build",
            final_prompt=prompt,
            readable_blueprint=blueprint,
            task_title=title,
            status="starting",
            stage_index=0,
        )
        record.turns.append(_new_interaction_turn(1))
        record.conversation_tree = migrate_linear_to_tree(
            final_prompt=prompt,
            readable_blueprint=blueprint,
            turns=record.turns,
            user_messages=[],
        )

        async with self._lock:
            self._sessions[session_id] = record

        await self._persist(record)
        await self._spawn_agent_task(
            record, self._setup_and_run(record, prompt), task_kind="setup"
        )
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        audit_record(
            "session",
            "session.create",
            user_id=owner_id,
            session_id=session_id,
            message="创建会话",
            detail={
                "mode": record.mode,
                "prompt_len": len(prompt),
                "blueprint_len": len(blueprint),
            },
        )
        return record

    async def _update_setup_step(
        self,
        record: SessionRecord,
        step_id: str,
        status: str,
        detail: str | None = None,
        *,
        publish: bool = True,
    ) -> None:
        if not record.setup_progress:
            record.setup_progress = _initial_setup_progress()
        for step in record.setup_progress["steps"]:
            if step["id"] == step_id:
                step["status"] = status
                if detail is not None:
                    step["detail"] = detail
                break
        _apply_setup_progress_to_turn(record)
        record.updated_at = _utc_now()
        if publish:
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

    async def _run_fabric_bootstrap(
        self,
        record: SessionRecord,
        workspace: Path,
        *,
        mod_name: str,
        mod_id: str,
        package_name: str,
    ) -> bool:
        from mod_bootstrap import bootstrap_fabric_workspace, has_gradlew

        record.setup_progress = _initial_setup_progress()
        _apply_setup_progress_to_turn(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

        loop = asyncio.get_running_loop()
        last_exc: Exception | None = None

        def on_progress(step_id: str, status: str, detail: str | None = None) -> None:
            asyncio.run_coroutine_threadsafe(
                self._update_setup_step(record, step_id, status, detail),
                loop,
            )

        for attempt in range(1, MOD_TEMPLATE_MAX_RETRIES + 1):
            progress = record.setup_progress
            assert progress is not None
            progress["retry_attempt"] = attempt
            progress["retrying"] = attempt > 1
            if attempt > 1:
                for step in progress["steps"]:
                    if step["status"] in {"failed", "active"}:
                        step["status"] = "pending"
                        step["detail"] = f"第 {attempt}/{MOD_TEMPLATE_MAX_RETRIES} 次重试"
                _apply_setup_progress_to_turn(record)
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
                await asyncio.sleep(MOD_TEMPLATE_RETRY_DELAY_SEC)

            try:
                await asyncio.to_thread(
                    bootstrap_fabric_workspace,
                    workspace,
                    mod_name,
                    mod_id=mod_id,
                    package_name=package_name,
                    run_build=False,
                    on_progress=on_progress,
                )
                if not has_gradlew(workspace):
                    raise RuntimeError("Fabric 模板解压后未找到 gradlew")
                progress["retrying"] = False
                progress["failed"] = False
                progress["notice"] = None
                _apply_setup_progress_to_turn(record)
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
                audit_record(
                    "agent",
                    "agent.bootstrap_ok",
                    user_id=record.owner_id,
                    session_id=record.session_id,
                    message="Fabric 环境创建成功",
                    detail={"mod_id": mod_id, "package_name": package_name, "attempt": attempt},
                )
                if not USE_MOCK_SESSIONS:
                    try:
                        ref = await asyncio.to_thread(
                            git_init_repo, workspace, message="Fabric bootstrap"
                        )
                        tree = ensure_tree(record)
                        root_id = tree.get("root_id")
                        if root_id:
                            set_node_git_refs(tree, root_id, git_ref=ref)
                    except WorkspaceGitError as exc:
                        logger.warning("git init after bootstrap failed: %s", exc)
                return True
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Fabric bootstrap attempt %s/%s failed session=%s: %s",
                    attempt,
                    MOD_TEMPLATE_MAX_RETRIES,
                    record.session_id,
                    exc,
                )
                for step in progress["steps"]:
                    if step["status"] == "active":
                        step["status"] = "failed"
                        step["detail"] = "暂时未成功，准备重试"
                _apply_setup_progress_to_turn(record)
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

        progress = record.setup_progress
        assert progress is not None
        progress["retrying"] = False
        progress["failed"] = True
        progress["notice"] = _setup_failure_notice(MOD_TEMPLATE_MAX_RETRIES)
        _apply_setup_progress_to_turn(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        audit_record(
            "agent",
            "agent.bootstrap_fail",
            user_id=record.owner_id,
            session_id=record.session_id,
            level="warn",
            message="Fabric bootstrap 未成功，将继续编写",
            detail={"error": str(last_exc)[:200] if last_exc else None, "attempts": MOD_TEMPLATE_MAX_RETRIES},
        )
        return False

    async def _emit_reference_step(self, record: SessionRecord, data: dict[str, Any]) -> None:
        record.reference_tool_steps.append(dict(data))
        _apply_setup_progress_to_turn(record)
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "reference_step", "data": data})
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

    async def _materialize_session_references(self, record: SessionRecord, refs: list[dict[str, Any]]) -> None:
        record.reference_index = record.reference_index or {}
        ctx = session_context_from_payload(record.payload)

        async def on_step(data: dict[str, Any]) -> None:
            await self._emit_reference_step(record, data)

        await materialize_all_session_refs(
            record.owner_id,
            record.session_id,
            refs,
            record.reference_index,
            session_context=ctx,
            on_step=on_step,
        )

        for pid, meta in (record.reference_index or {}).items():
            if not isinstance(meta, dict):
                continue
            ev_type = "reference_ready" if meta.get("status") == "ready" else "reference_failed"
            if meta.get("status") in {"ready", "failed"}:
                await self._publish(record, {"type": ev_type, "data": meta})
        record.updated_at = _utc_now()
        await self._persist(record)

    async def _prepare_session_references(self, record: SessionRecord) -> dict[str, str]:
        handoff_plan_id = (record.payload.get("handoff_plan_id") or "").strip()
        refs = code_refs_from_payload(record.payload)
        ref_paths: dict[str, str] = {}

        if handoff_plan_id:
            record.reference_index = init_reference_index_from_payload(record.payload)
            ref_paths = copy_from_plan_refs(
                record.owner_id,
                handoff_plan_id,
                record.session_id,
                record.reference_index,
            )
        elif refs:
            record.reference_index = record.reference_index or {}
            await self._materialize_session_references(record, refs)
            ref_paths = copy_session_refs_to_workspace(
                record.owner_id,
                record.session_id,
                record.reference_index,
            )
            await self._persist(record)

        return ref_paths

    async def _setup_and_run(self, record: SessionRecord, prompt: str) -> None:
        try:
            from mod_metadata import (
                derive_mod_id,
                derive_package_name,
                suggest_mod_name,
                suggest_task_title,
            )

            record.status = "starting"
            record.stage_index = 0
            record.updated_at = _utc_now()
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

            if not (record.payload.get("mod_name") or "").strip():
                if record.task_title == TITLE_GENERATING_PLACEHOLDER:
                    title = await asyncio.to_thread(
                        suggest_task_title,
                        prompt=prompt,
                        readable_blueprint=record.readable_blueprint,
                    )
                    record.task_title = title
                    record.updated_at = _utc_now()
                    await self._persist(record)
                    await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
                    audit_record(
                        "session",
                        "session.title_generated",
                        user_id=record.owner_id,
                        session_id=record.session_id,
                        message=f"标题: {title}",
                        detail={"task_title": title},
                    )

            workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)

            title_for_mod = record.task_title
            if title_for_mod == TITLE_GENERATING_PLACEHOLDER:
                title_for_mod = _derive_task_title(record.readable_blueprint, prompt)

            mod_name = await asyncio.to_thread(
                suggest_mod_name,
                prompt=prompt,
                task_title=title_for_mod,
                mod_name=record.payload.get("mod_name") or None,
            )
            mod_id = record.payload.get("mod_id") or derive_mod_id(mod_name)
            package_name = record.payload.get("package_name") or derive_package_name(mod_id)
            record.payload["mod_name"] = mod_name
            record.payload["mod_id"] = mod_id
            record.payload["package_name"] = package_name
            record.task_title = mod_name[:80]
            record.updated_at = _utc_now()
            await self._persist(record)

            refs = code_refs_from_payload(record.payload)
            handoff_plan_id = (record.payload.get("handoff_plan_id") or "").strip()
            needs_refs = bool(handoff_plan_id or refs)

            async def prep_refs() -> dict[str, str]:
                if not needs_refs:
                    return {}
                return await self._prepare_session_references(record)

            async def prep_env() -> None:
                if USE_MOCK_SESSIONS:
                    return
                await self._run_fabric_bootstrap(
                    record,
                    workspace,
                    mod_name=mod_name,
                    mod_id=mod_id,
                    package_name=package_name,
                )

            ref_paths, _ = await asyncio.gather(prep_refs(), prep_env())

            append = build_reference_prompt_append(record.reference_index or {}, ref_paths)
            if append:
                prompt = f"{prompt}\n\n{append}"
                record.final_prompt = prompt
                await self._persist(record)

            record.stage_index = 1
            record.status = "running"
            record.updated_at = _utc_now()
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
            audit_record(
                "session",
                "session.stage_change",
                user_id=record.owner_id,
                session_id=record.session_id,
                message="阶段: 创建环境 → 编写代码",
                detail={"from_stage": 0, "to_stage": 1},
            )

            turn = record.turns[-1]

            async def on_permission(req_id: str, pending: dict[str, Any]) -> None:
                turn["pending_action"] = pending
                record.status = "waiting_user"
                record.updated_at = _utc_now()
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

            record._agent = _create_agent_session(
                record,
                workspace,
                on_permission=on_permission,
            )
            await self._run_agent(record, prompt)
        except asyncio.CancelledError:
            if record.status != "stopped":
                record.status = "stopped"
                record.updated_at = _utc_now()
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
            raise
        except Exception as exc:
            logger.exception("Setup/run failed session=%s", record.session_id)
            record.status = "error"
            record.error_message = _friendly_session_error(exc, record)
            record.updated_at = _utc_now()
            if record.stage_index == 0:
                record.delivery = record.delivery or _delivery_base(record)
            await self._safe_publish(
                record, {"type": "snapshot", "data": {}}, include_heavy=True
            )

    def get(self, session_id: str, owner_id: str | None = None) -> SessionRecord | None:
        try:
            return self._require(session_id, owner_id)
        except (KeyError, PermissionError):
            return None

    async def update_title(self, session_id: str, owner_id: str, title: str) -> SessionRecord:
        record = self._require(session_id, owner_id)
        trimmed = title.strip()
        if not trimmed:
            raise ValueError("title required")
        record.task_title = trimmed[:80]
        record.updated_at = _utc_now()
        await self._persist(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def update_session_meta(
        self,
        session_id: str,
        owner_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
    ) -> SessionRecord:
        record = self._require(session_id, owner_id)
        if title is not None:
            trimmed = title.strip()
            if not trimmed:
                raise ValueError("title required")
            record.task_title = trimmed[:80]
        if pinned is not None:
            record.pinned = pinned
        record.updated_at = _utc_now()
        await self._persist(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def trash_session(self, session_id: str, owner_id: str) -> SessionRecord:
        record = self._require(session_id, owner_id)
        record.deleted_at = _utc_now()
        record.pinned = False
        record.updated_at = _utc_now()
        await self._persist(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def restore_session(self, session_id: str, owner_id: str) -> SessionRecord:
        record = self._require(session_id, owner_id)
        record.deleted_at = None
        record.updated_at = _utc_now()
        await self._persist(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def stop(self, session_id: str, owner_id: str) -> SessionRecord:
        record = self._require(session_id, owner_id)
        if record.status in {"completed", "stopped"}:
            return record
        if record._agent:
            await record._agent.interrupt()
        if record._agent_task and not record._agent_task.done():
            record._agent_task.cancel()
        record.status = "stopped"
        record.updated_at = _utc_now()
        _persist_active_turn(record)
        await self._persist(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        audit_record(
            "session",
            "session.stop",
            user_id=owner_id,
            session_id=session_id,
            message="用户停止会话",
        )
        return record

    @staticmethod
    def _admin_intervention_meta(
        *,
        sent_by_admin: bool = False,
        admin_actor: str | None = None,
        admin_actor_id: str | None = None,
    ) -> dict[str, Any]:
        if not sent_by_admin:
            return {}
        meta: dict[str, Any] = {"sent_by_admin": True}
        if admin_actor:
            meta["admin_actor"] = admin_actor
        if admin_actor_id:
            meta["admin_actor_id"] = admin_actor_id
        return meta

    async def submit_action(
        self,
        session_id: str,
        owner_id: str,
        *,
        choice_id: str | None = None,
        answers: dict[str, Any] | None = None,
        request_id: str | None = None,
        sent_by_admin: bool = False,
        admin_actor: str | None = None,
        admin_actor_id: str | None = None,
    ) -> SessionRecord:
        record = self._require(session_id, owner_id)
        if record.status != "waiting_user":
            raise ValueError("no pending action")

        active_turn = record.turns[-1] if record.turns else None
        if not active_turn or not active_turn.get("pending_action"):
            raise ValueError("no pending action")

        action = active_turn["pending_action"]
        req_id = request_id or action.get("request_id")

        admin_meta = self._admin_intervention_meta(
            sent_by_admin=sent_by_admin,
            admin_actor=admin_actor,
            admin_actor_id=admin_actor_id,
        )

        if action.get("type") == "ask_user" and answers is not None:
            active_turn["user_reply"] = {
                "answers": answers,
                "created_at": _utc_now(),
                **admin_meta,
            }
            if record._agent and req_id:
                resolve_agent_permission(record._agent, req_id, {"answers": answers})
        elif choice_id:
            valid_ids = {c["id"] for c in action.get("choices", [])}
            if choice_id not in valid_ids:
                raise ValueError("invalid choice")
            action["selected"] = choice_id
            label = next(c["label"] for c in action["choices"] if c["id"] == choice_id)
            active_turn["user_reply"] = {
                "choice_id": choice_id,
                "label": label,
                "created_at": _utc_now(),
                **admin_meta,
            }
            if record._agent and req_id:
                resolve_agent_permission(
                    record._agent,
                    req_id,
                    {"answers": {action.get("question", "q"): label}},
                )
        else:
            raise ValueError("choice_id or answers required")

        active_turn["pending_action"] = None
        record.status = "running"
        record.updated_at = _utc_now()
        if sent_by_admin:
            audit_record(
                "admin",
                "session.admin_intervene_action",
                user_id=admin_actor_id or owner_id,
                session_id=session_id,
                message="管理员代用户确认 Agent 操作",
                detail={
                    "owner_id": owner_id,
                    "admin_actor": admin_actor,
                    "choice_id": choice_id,
                },
            )
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def add_message(
        self,
        session_id: str,
        owner_id: str,
        content: str,
        *,
        sent_by_admin: bool = False,
        admin_actor: str | None = None,
        admin_actor_id: str | None = None,
    ) -> SessionRecord:
        record = self._require(session_id, owner_id)
        trimmed = content.strip()
        if not trimmed:
            raise ValueError("message required")
        if record.status not in {"waiting_user", "completed", "stopped", "error"}:
            raise ValueError("agent busy")

        if record.status in {"completed", "stopped", "error"}:
            await ensure_workspace_synced(record)
            _persist_active_turn(record)

        record.user_messages.append(
            {
                "id": f"msg-{uuid.uuid4().hex[:8]}",
                "role": "user",
                "content": trimmed,
                "kind": "follow_up",
                "created_at": _utc_now(),
                **self._admin_intervention_meta(
                    sent_by_admin=sent_by_admin,
                    admin_actor=admin_actor,
                    admin_actor_id=admin_actor_id,
                ),
            }
        )
        tree = ensure_tree(record)
        user_node = append_user_follow_up(tree, trimmed)
        if sent_by_admin:
            user_node["sent_by_admin"] = True
            if admin_actor:
                user_node["admin_actor"] = admin_actor
        record.updated_at = _utc_now()

        if sent_by_admin:
            audit_record(
                "admin",
                "session.admin_intervene_message",
                user_id=admin_actor_id or owner_id,
                session_id=session_id,
                message="管理员代用户发送跟进消息",
                detail={
                    "owner_id": owner_id,
                    "admin_actor": admin_actor,
                    "content_preview": trimmed[:200],
                },
            )

        if record.status in {"completed", "stopped", "error"}:
            if not await agent_capacity.has_capacity():
                raise SessionCapacityError(
                    f"当前并发编写任务已满（上限 {SESSION_MAX_ACTIVE}），请稍后再试"
                )
            await _checkpoint_workspace_for_node(record, user_node["id"], "pre-run user msg")
            record.status = "running"
            record.interaction_kind = "follow_up"
            record.stage_index = 0
            record.workspace_git_warning = None
            turn_index = len(record.turns) + 1
            record.turns.append(_new_interaction_turn(turn_index))
            assistant_node = append_assistant_turn(tree, record.turns[-1]["id"])
            sync_tree_to_linear(record)
            await _checkpoint_workspace_for_node(
                record, assistant_node["id"], "pre-run assistant", git_ref_start=True
            )
            if not record._agent:
                workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)
                record._agent = _create_agent_session(record, workspace)
            await self._spawn_agent_task(
                record, self._run_follow_up(record, trimmed), task_kind="follow-up"
            )

        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def regenerate(self, session_id: str, owner_id: str) -> SessionRecord:
        record = self._require(session_id, owner_id)
        _assert_branch_allowed(record)
        await self._persist(record)
        await ensure_workspace_synced(record)
        tree = ensure_tree(record)
        assistant = last_assistant_on_path(tree)
        if not assistant:
            raise ValueError("尚无 assistant 回复可重新生成")
        turn_snap = assistant.get("turn_snapshot") or {}
        if turn_snap.get("pending_action") and not turn_snap.get("user_reply"):
            raise ValueError("当前轮次有待回复的操作，无法重新生成")

        reset_ref = git_ref_for_reset_before(tree, assistant["id"])
        if not USE_MOCK_SESSIONS:
            _require_git_for_reset(reset_ref)
            workspace = record.workspace_path()
            try:
                await asyncio.to_thread(git_reset_to, workspace, reset_ref)
            except WorkspaceGitError as exc:
                _raise_git_reset_error(exc)

        turn_index = len(record.turns) + 1
        new_turn_id = f"interaction-{turn_index}-reg-{uuid.uuid4().hex[:6]}"
        new_assistant = fork_assistant_sibling(
            tree, assistant["id"], turn_id=new_turn_id, git_ref=reset_ref
        )
        sync_tree_to_linear(record)
        record.agent_cli_session_id = None
        record._agent = None
        record.error_message = None
        record.workspace_git_warning = None
        record.workspace_git_stale = False
        record.agent_run = None
        record.branch_processing = True
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

        parent_id = assistant.get("parent_id")
        parent = (tree.get("nodes") or {}).get(parent_id) if parent_id else None
        prompt = (parent or {}).get("content") or record.final_prompt
        kind = (parent or {}).get("kind") or KIND_INITIAL
        record.branch_processing = False
        await _checkpoint_workspace_for_node(
            record, new_assistant["id"], "pre-run assistant", git_ref_start=True
        )
        await self._start_branch_rerun(record, kind=kind, prompt=prompt)
        return record

    async def rewind(
        self,
        session_id: str,
        owner_id: str,
        *,
        node_id: str,
        new_content: str | None = None,
    ) -> SessionRecord:
        record = self._require(session_id, owner_id)
        _assert_branch_allowed(record)
        await self._persist(record)
        tree = ensure_tree(record)
        nodes = tree.get("nodes") or {}
        node = nodes.get(node_id)
        if not node or node.get("type") != "user":
            raise ValueError("只能编辑用户消息")
        _assert_user_node_editable(node)
        content = (new_content or node.get("content") or "").strip()
        if not content:
            raise ValueError("message required")

        if node.get("kind") == KIND_INITIAL:
            return await self._rewind_initial_prompt(record, node_id, content)

        await ensure_workspace_synced(record)
        reset_ref = git_ref_for_reset_before(tree, node_id)
        if not USE_MOCK_SESSIONS:
            _require_git_for_reset(reset_ref)
            workspace = record.workspace_path()
            try:
                await asyncio.to_thread(git_reset_to, workspace, reset_ref)
            except WorkspaceGitError as exc:
                _raise_git_reset_error(exc)

        new_user = fork_user_sibling(tree, node_id, content=content)
        sync_tree_to_linear(record)
        record.agent_cli_session_id = None
        record._agent = None
        record.error_message = None
        record.workspace_git_warning = None
        record.workspace_git_stale = False
        record.agent_run = None
        turn_index = len(record.turns) + 1
        new_turn = _new_interaction_turn(turn_index)
        record.turns.append(new_turn)
        assistant_node = append_assistant_turn(tree, new_turn["id"])
        sync_tree_to_linear(record)
        await _checkpoint_workspace_for_node(record, new_user["id"], "pre-run user msg")
        await _checkpoint_workspace_for_node(
            record, assistant_node["id"], "pre-run assistant", git_ref_start=True
        )
        await self._start_branch_rerun(record, kind=KIND_FOLLOW_UP, prompt=content)
        return record

    async def _rewind_initial_prompt(
        self, record: SessionRecord, node_id: str, content: str
    ) -> SessionRecord:
        tree = ensure_tree(record)
        node = (tree.get("nodes") or {}).get(node_id)
        if not node:
            raise ValueError("node not found")

        readable = record.readable_blueprint
        if content != record.final_prompt:
            readable = content

        fork_user_sibling(
            tree,
            node_id,
            content=content,
            kind=KIND_INITIAL,
            readable_blueprint=readable,
        )
        record.final_prompt = content
        record.readable_blueprint = readable
        record.payload = dict(record.payload or {})
        record.payload["prompt"] = content
        sync_tree_to_linear(record)

        workspace = record.workspace_path()
        if not USE_MOCK_SESSIONS:
            await asyncio.to_thread(wipe_workspace_contents, workspace)
            try:
                ref = await asyncio.to_thread(git_reinit_repo, workspace, message="rewind initial")
                tree = ensure_tree(record)
                root_id = tree.get("root_id")
                if root_id:
                    set_node_git_refs(tree, root_id, git_ref=ref)
                    record.workspace_git_expected_ref = ref
                    record.workspace_git_stale = False
            except WorkspaceGitError as exc:
                logger.warning("git reinit after rewind initial failed: %s", exc)

        record.turns = [_new_interaction_turn(1)]
        tree = ensure_tree(record)
        path = tree.get("active_path") or []
        if path:
            append_assistant_turn(tree, record.turns[0]["id"])
        sync_tree_to_linear(record)

        record.agent_cli_session_id = None
        record._agent = None
        record.error_message = None
        record.status = "starting"
        record.interaction_kind = "build"
        record.stage_index = 0
        record.setup_progress = None
        record.delivery = None
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        await self._spawn_agent_task(
            record, self._setup_and_run(record, content), task_kind="rewind-setup"
        )
        return record

    async def switch_branch(
        self, session_id: str, owner_id: str, *, node_id: str
    ) -> SessionRecord:
        record = self._require(session_id, owner_id)
        _assert_branch_allowed(record)
        await self._persist(record)
        tree = ensure_tree(record)
        nodes = tree.get("nodes") or {}
        if node_id not in nodes:
            raise ValueError("node not found")

        tree_switch_branch(tree, node_id)
        sync_tree_to_linear(record)
        record.agent_run = None
        record.error_message = None
        record.branch_processing = False
        _sync_status_from_active_path(record)
        _mark_workspace_git_after_switch(record, tree)
        record.updated_at = _utc_now()
        await self._persist(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def _start_branch_rerun(
        self, record: SessionRecord, *, kind: str, prompt: str
    ) -> None:
        await ensure_workspace_synced(record)
        record.status = "running"
        record.updated_at = _utc_now()
        workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)
        record._agent = None
        record.agent_cli_session_id = None
        record.agent_run = None

        tree = ensure_tree(record)
        assistant = last_assistant_on_path(tree)
        if assistant and not assistant.get("git_ref_start"):
            await _checkpoint_workspace_for_node(
                record, assistant["id"], "pre-run assistant", git_ref_start=True
            )

        if kind == KIND_INITIAL and record.interaction_kind == "build" and len(record.turns) <= 1:
            record.interaction_kind = "build"
            record.stage_index = max(record.stage_index, 1)
            await self._spawn_agent_task(
                record, self._run_agent(record, prompt), task_kind="branch-agent"
            )
        else:
            record.interaction_kind = "follow_up"
            record.stage_index = 0
            if not record._agent:
                record._agent = _create_agent_session(record, workspace)
            await self._spawn_agent_task(
                record, self._run_follow_up(record, prompt), task_kind="branch-follow-up"
            )

        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

    async def start_test_server(self, session_id: str, owner_id: str) -> SessionRecord:
        record = self._require(session_id, owner_id)
        if not _find_artifact(record.workspace_path()) and not record.delivery:
            raise ValueError("artifact not ready")
        record.test_server_status = "placeholder"
        record.test_server_port = None
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def stop_test_server(self, session_id: str, owner_id: str) -> SessionRecord:
        record = self._require(session_id, owner_id)
        record.test_server_status = "idle"
        record.test_server_port = None
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def delete_session(self, session_id: str, owner_id: str | None = None) -> bool:
        if owner_id:
            try:
                record = self._require(session_id, owner_id)
            except PermissionError:
                return False
        else:
            record = self._sessions.get(session_id)
        if not record:
            return False
        if record._agent_task and not record._agent_task.done():
            record._agent_task.cancel()
        if record._agent:
            await record._agent.close()
        async with self._lock:
            self._sessions.pop(session_id, None)
        return await user_store.delete_session(record.owner_id, session_id)

    def get_artifact_path(self, session_id: str, owner_id: str | None = None) -> Path | None:
        record = self.get(session_id, owner_id)
        if not record:
            return None
        _ensure_workspace_synced_sync(record)
        jar = _find_artifact(record.workspace_path())
        return jar

    def _validated_workspace(self, record: SessionRecord) -> Path:
        return validate_workspace(record.workspace_path(), WORKSPACE_ROOT)

    async def build_artifact(self, session_id: str, owner_id: str) -> dict[str, Any]:
        from workspace_ops import run_gradlew_build

        record = self._require(session_id, owner_id)
        await ensure_workspace_synced(record)
        workspace = self._validated_workspace(record)
        await gradle_capacity.acquire()
        try:
            result = await asyncio.to_thread(run_gradlew_build, workspace)
        finally:
            gradle_capacity.release()
        if not result.get("ok"):
            raise ValueError(result.get("message") or "gradlew build 失败")

        _sync_artifact_state(record)
        jar = _find_artifact(workspace)
        if not jar:
            raise ValueError("编译完成但未在 build/libs 中找到产物 jar")

        record.updated_at = _utc_now()
        await self._persist(record)
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return {
            "ok": True,
            "message": result.get("message") or "编译成功",
            "artifact_ready": True,
            "artifact_file": jar.name,
        }

    def list_workspace_entries(self, session_id: str, owner_id: str, path: str = "") -> dict[str, Any]:
        from workspace_ops import list_workspace_entries

        record = self._require(session_id, owner_id)
        _ensure_workspace_synced_sync(record)
        return list_workspace_entries(self._validated_workspace(record), path)

    def read_workspace_file(self, session_id: str, owner_id: str, path: str) -> dict[str, Any]:
        from workspace_ops import read_workspace_file

        record = self._require(session_id, owner_id)
        _ensure_workspace_synced_sync(record)
        return read_workspace_file(self._validated_workspace(record), path)

    def resolve_workspace_download(self, session_id: str, owner_id: str, path: str) -> Path:
        from workspace_ops import resolve_workspace_rel_path

        record = self._require(session_id, owner_id)
        _ensure_workspace_synced_sync(record)
        target = resolve_workspace_rel_path(self._validated_workspace(record), path)
        if not target.is_file():
            raise ValueError("not a file")
        return target

    def build_workspace_archive(self, session_id: str, owner_id: str, path: str) -> tuple[Any, str]:
        from workspace_ops import build_workspace_zip

        record = self._require(session_id, owner_id)
        _ensure_workspace_synced_sync(record)
        return build_workspace_zip(self._validated_workspace(record), path)

    async def subscribe(self, session_id: str, owner_id: str) -> AsyncIterator[dict[str, Any]]:
        record = self._require(session_id, owner_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        record.subscribers.append(queue)
        stop_ping = asyncio.Event()

        async def _pinger() -> None:
            while not stop_ping.is_set():
                try:
                    await asyncio.wait_for(stop_ping.wait(), timeout=25.0)
                    break
                except asyncio.TimeoutError:
                    pass
                if stop_ping.is_set():
                    break
                try:
                    queue.put_nowait(
                        {
                            "type": "ping",
                            "data": {
                                "updated_at": record.updated_at,
                                "status": record.status,
                            },
                        }
                    )
                except Exception:
                    break

        ping_task = asyncio.create_task(_pinger())
        trace_event(session_id, "sse.subscribe", owner_id=owner_id)
        try:
            yield {"type": "snapshot", "data": record.to_snapshot(include_heavy=True)}
            while True:
                event = await queue.get()
                yield event
                if event.get("type") == "close":
                    break
        finally:
            stop_ping.set()
            ping_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ping_task
            if queue in record.subscribers:
                record.subscribers.remove(queue)

    async def _run_agent(self, record: SessionRecord, prompt: str) -> None:
        stream_started = False
        max_turns = _resolve_max_turns(record)
        self._start_idle_watch(record)
        try:
            if record.stage_index < 1:
                record.stage_index = 1
            if record.status == "starting":
                record.status = "running"
            record.updated_at = _utc_now()
            record.agent_run = {
                "started_at": _utc_now(),
                "max_turns": max_turns,
            }
            stream_started = True
            trace_event(
                record.session_id,
                "agent.stream_start",
                owner_id=record.owner_id,
                detail={"prompt_len": len(prompt), "max_turns": max_turns},
            )
            await self._publish(record, {"type": "snapshot", "data": {}})

            audit_record(
                "agent",
                "agent.stream_start",
                user_id=record.owner_id,
                session_id=record.session_id,
                message="Agent 开始执行",
                detail={"prompt_len": len(prompt), "max_turns": max_turns},
            )

            turn = record.turns[-1]
            workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)
            tree = ensure_tree(record)
            assistant = last_assistant_on_path(tree)
            if assistant and not assistant.get("git_ref_start"):
                await _checkpoint_workspace_for_node(
                    record, assistant["id"], "pre-run assistant", git_ref_start=True
                )
            prompt_to_send = prompt
            use_follow_up = False
            compaction_count = 0

            async def on_permission(req_id: str, pending: dict[str, Any]) -> None:
                turn["pending_action"] = pending
                record.status = "waiting_user"
                record.updated_at = _utc_now()
                await self._publish(record, {"type": "snapshot", "data": {}}, include_heavy=True)
                audit_record(
                    "agent",
                    "agent.permission",
                    user_id=record.owner_id,
                    session_id=record.session_id,
                    message="等待用户授权",
                    detail={"request_id": req_id, "type": pending.get("type")},
                )

            while True:
                _merge_agent_run(record, compacted=False)

                if not record._agent:
                    record._agent = _create_agent_session(
                        record,
                        workspace,
                        on_permission=on_permission,
                        max_turns=max_turns,
                    )

                stream_state: dict[str, Any] = {}
                if use_follow_up:
                    messages = stream_agent_follow_up(record._agent, prompt_to_send)
                else:
                    messages = stream_agent_prompt(
                        record._agent, prompt_to_send, on_permission=on_permission
                    )

                stream_result = await _consume_agent_stream(
                    self, record, turn, messages, stream_state
                )
                if stream_result in ("stopped", "waiting_user"):
                    return

                if stream_state.get("compacted"):
                    _merge_agent_run(record, compacted=True)

                exit_reason = _resolve_build_exit_reason(record)
                artifact_ok = _has_artifact(record.workspace_path())
                _merge_agent_run(
                    record,
                    ended_at=_utc_now(),
                    exit_reason=exit_reason,
                    artifact_found=artifact_ok,
                )
                _audit_agent_stream_end(record)

                if artifact_ok:
                    _merge_agent_run(record, auto_continuing=False)
                    await self._finalize_build_success(record)
                    return

                agent_run = record.agent_run or {}
                num_turns = agent_run.get("num_turns")
                max_turns_val = agent_run.get("max_turns") or max_turns

                if (
                    exit_reason == "compacted_early_stop"
                    and stream_state.get("compacted")
                    and num_turns is not None
                    and num_turns < max_turns_val
                    and record.agent_cli_session_id
                ):
                    compaction_count += 1
                    _merge_agent_run(
                        record,
                        auto_continuing=True,
                        compaction_count=compaction_count,
                    )
                    record.status = "running"
                    record.error_message = None
                    record.updated_at = _utc_now()
                    await self._publish(record, {"type": "snapshot", "data": {}}, include_heavy=True)
                    audit_record(
                        "agent",
                        "agent.compaction_auto_continue",
                        user_id=record.owner_id,
                        session_id=record.session_id,
                        message="上下文压缩后自动续写",
                        detail={
                            "compaction_count": compaction_count,
                            "cli_session_id": record.agent_cli_session_id,
                            "num_turns": num_turns,
                        },
                    )
                    record._agent = None
                    use_follow_up = True
                    prompt_to_send = AUTO_CONTINUE_AFTER_COMPACTION
                    continue

                if exit_reason == "compacted_early_stop":
                    record.status = "completed"
                    _merge_agent_run(record, auto_continuing=False)
                    record.error_message = "上下文已压缩，编写已暂停。发送跟进消息即可继续。"
                    record.updated_at = _utc_now()
                    if record.turns:
                        _freeze_turn_progress(record, record.turns[-1])
                    if not record.delivery:
                        record.delivery = _delivery_base(record)
                    else:
                        record.delivery.update(_delivery_base(record))
                    await self._persist(record)
                    await self._publish(record, {"type": "snapshot", "data": {}}, include_heavy=True)
                    audit_record(
                        "agent",
                        "agent.compacted_pause",
                        user_id=record.owner_id,
                        session_id=record.session_id,
                        message="上下文压缩后早停，等待跟进",
                        detail={"exit_reason": exit_reason, "num_turns": num_turns},
                    )
                    return

                _merge_agent_run(record, auto_continuing=False)
                diag = _format_agent_run_diagnostic(record.agent_run)
                record.status = "error"
                record.error_message = f"编译未完成：build/libs 中未找到产物 jar{diag}"
                record.updated_at = _utc_now()
                if not record.delivery:
                    record.delivery = _delivery_base(record)
                else:
                    record.delivery.update(_delivery_base(record))
                await self._persist(record)
                await self._publish(record, {"type": "snapshot", "data": {}}, include_heavy=True)
                audit_record(
                    "session",
                    "session.error",
                    user_id=record.owner_id,
                    session_id=record.session_id,
                    level="error",
                    message=record.error_message,
                )
                return

        except asyncio.CancelledError:
            if stream_started:
                _merge_agent_run(
                    record,
                    ended_at=_utc_now(),
                    exit_reason="stopped",
                    artifact_found=_has_artifact(record.workspace_path()),
                )
                _audit_agent_stream_end(record)
            if record.status != "stopped":
                record.status = "stopped"
                record.updated_at = _utc_now()
            _persist_active_turn(record)
            await self._persist(record)
            await self._publish(record, {"type": "snapshot", "data": {}}, include_heavy=True)
            raise
        except Exception as exc:
            logger.exception("Agent run failed session=%s", record.session_id)
            trace_event(
                record.session_id,
                "agent.exception",
                owner_id=record.owner_id,
                detail={"error": str(exc)[:200]},
            )
            if stream_started:
                _merge_agent_run(
                    record,
                    ended_at=_utc_now(),
                    exit_reason="exception",
                    artifact_found=_has_artifact(record.workspace_path()),
                    last_error=str(exc)[:200],
                )
                _audit_agent_stream_end(record)
            record.status = "error"
            record.error_message = _friendly_session_error(exc, record)
            record.updated_at = _utc_now()
            await self._safe_publish(
                record, {"type": "snapshot", "data": {}}, include_heavy=True
            )
            audit_record(
                "session",
                "session.error",
                user_id=record.owner_id,
                session_id=record.session_id,
                level="error",
                message=record.error_message[:200] if record.error_message else str(exc)[:200],
            )
        finally:
            self._stop_idle_watch(record)
            if record._agent:
                if sys.platform != "win32":
                    await record._agent.close()
                record._agent = None

    async def _finalize_build_success(
        self, record: SessionRecord, *, warning: str | None = None
    ) -> None:
        await _finalize_build_success(self, record, warning=warning)

    async def _run_follow_up(self, record: SessionRecord, message: str) -> None:
        stream_started = False
        max_turns = _resolve_max_turns(record)
        self._start_idle_watch(record)
        try:
            turn = record.turns[-1]
            stream_state: dict[str, Any] = {}
            record.stage_index = 0
            record.updated_at = _utc_now()
            record.agent_run = {
                "started_at": _utc_now(),
                "max_turns": max_turns,
            }
            stream_started = True
            trace_event(
                record.session_id,
                "agent.stream_start",
                owner_id=record.owner_id,
                detail={"prompt_len": len(message), "max_turns": max_turns, "kind": "follow_up"},
            )
            await self._publish(record, {"type": "snapshot", "data": {}})

            audit_record(
                "agent",
                "agent.stream_start",
                user_id=record.owner_id,
                session_id=record.session_id,
                message="Agent 跟进开始",
                detail={"prompt_len": len(message), "max_turns": max_turns},
            )

            async for msg in stream_agent_follow_up(record._agent, message):
                if record.status == "stopped":
                    _merge_agent_run(
                        record,
                        ended_at=_utc_now(),
                        exit_reason="stopped",
                        artifact_found=_has_artifact(record.workspace_path()),
                    )
                    _audit_agent_stream_end(record)
                    _persist_active_turn(record)
                    return
                turn, side_events = process_agent_message(msg, stream_state=stream_state, turn=turn)
                _apply_side_events(record, side_events)
                _sync_turn_to_tree(record, turn)
                record.updated_at = _utc_now()
                _trace_side_events(record, side_events, type(msg).__name__)

                patch = _build_sse_patch(record, turn, side_events)
                event_types = {ev.get("type") for ev in side_events}
                immediate = bool(event_types & {"tool_use", "tool_result", "result", "error"})
                if side_events:
                    await self._publish_patch(record, patch, immediate=immediate)

            agent_run = record.agent_run or {}
            num_turns = agent_run.get("num_turns")
            max_turns = agent_run.get("max_turns") or _resolve_max_turns(record)
            exit_reason = (
                "max_turns_reached"
                if num_turns is not None and num_turns >= max_turns
                else "follow_up_done"
            )
            _merge_agent_run(
                record,
                ended_at=_utc_now(),
                exit_reason=exit_reason,
                artifact_found=_has_artifact(record.workspace_path()),
            )
            _audit_agent_stream_end(record)

            record.stage_index = len(FOLLOW_UP_STAGES) - 1
            record.status = "completed"
            record.updated_at = _utc_now()
            _freeze_turn_progress(record, turn)
            _sync_turn_to_tree(record, turn)
            await _checkpoint_workspace(record, "follow-up complete")
            await self._persist(record)
            await self._publish(record, {"type": "snapshot", "data": {}}, include_heavy=True)
        except asyncio.CancelledError:
            if stream_started:
                _merge_agent_run(
                    record,
                    ended_at=_utc_now(),
                    exit_reason="stopped",
                    artifact_found=_has_artifact(record.workspace_path()),
                )
                _audit_agent_stream_end(record)
            if record.status != "stopped":
                record.status = "stopped"
                record.updated_at = _utc_now()
            _persist_active_turn(record)
            await self._persist(record)
            await self._publish(record, {"type": "snapshot", "data": {}}, include_heavy=True)
            raise
        except Exception as exc:
            logger.exception("Follow-up failed session=%s", record.session_id)
            trace_event(
                record.session_id,
                "agent.exception",
                owner_id=record.owner_id,
                detail={"error": str(exc)[:200], "kind": "follow_up"},
            )
            if stream_started:
                _merge_agent_run(
                    record,
                    ended_at=_utc_now(),
                    exit_reason="exception",
                    artifact_found=_has_artifact(record.workspace_path()),
                    last_error=str(exc)[:200],
                )
                _audit_agent_stream_end(record)
            record.status = "error"
            record.error_message = _friendly_session_error(exc, record)
            record.updated_at = _utc_now()
            await self._safe_publish(
                record, {"type": "snapshot", "data": {}}, include_heavy=True
            )
        finally:
            self._stop_idle_watch(record)


    async def shutdown(self) -> None:
        RuntimeState.drain()
        pending: list[asyncio.Task[None]] = []
        for record in list(self._sessions.values()):
            for queue in list(record.subscribers):
                try:
                    queue.put_nowait({"type": "close"})
                except Exception:
                    pass
            if record._agent_task and not record._agent_task.done():
                record._agent_task.cancel()
                pending.append(record._agent_task)
            if record._agent:
                try:
                    if sys.platform != "win32":
                        await record._agent.close()
                except Exception as exc:
                    logger.warning("Agent close on shutdown session=%s: %s", record.session_id, exc)
                record._agent = None
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


session_service = SessionService()


def sse_encode(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
