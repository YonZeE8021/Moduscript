"""In-memory mock session store with staged agent simulation for frontend preview."""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from mod_metadata import TITLE_GENERATING_PLACEHOLDER, resolve_initial_task_title

BUILD_STAGES = ["创建环境", "编写代码", "编译完成"]
FOLLOW_UP_STAGES = ["进行中", "已完成"]

STATUS_LABELS = {
    "starting": "创建环境中…",
    "running": "正在编写…",
    "waiting_user": "等待你的回复…",
    "completed": "已完成",
    "stopped": "已停止",
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


def _loader_display(mod_loader: str) -> str:
    labels = {
        "neoforge": "NeoForge",
        "forge": "Forge",
        "fabric": "Fabric",
        "quilt": "Quilt",
    }
    return labels.get(mod_loader.lower(), mod_loader or "—")


def _concept_excerpt(prompt: str, max_len: int = 120) -> str:
    for line in prompt.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("<"):
            continue
        return stripped[:max_len]
    return "模组任务"


def _build_permission_action(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = payload.get("prompt") or ""
    excerpt = _concept_excerpt(prompt)
    loader = _loader_display(payload.get("mod_loader") or "")
    return {
        "type": "choice",
        "question": f"关于「{excerpt}」：目标效果范围应如何限定？",
        "choices": [
            {"id": "hostile", "label": "仅敌对生物"},
            {"id": "all", "label": "所有生物（可配置）"},
            {"id": "player", "label": "仅玩家"},
        ],
        "selected": None,
    }


def _build_turn_content(payload: dict[str, Any], stage_index: int) -> dict[str, Any]:
    loader = _loader_display(payload.get("mod_loader") or "")
    mc = payload.get("minecraft_version") or "1.21.1"
    mod_loader = payload.get("mod_loader") or "neoforge"
    excerpt = _concept_excerpt(payload.get("prompt") or "")

    summaries = {
        0: f"正在创建 {loader} {mc} 开发环境，并解析任务稿中的约束与参考模组。",
        1: f"正在编写「{excerpt}」的核心逻辑与项目骨架。",
        2: f"正在编译项目并完成 {loader} 构建验证。",
    }

    thinking = [
        f"{loader} {mc} 环境下优先使用官方推荐的事件总线与配置分离（common/client）。",
        f"任务核心：{excerpt}。参考 payload 中的 requirements 与 reference_mods 约束实现。",
    ]

    tools: list[dict[str, str]] = []
    if stage_index <= 1:
        tools.append(
            {
                "name": f"Read · build.gradle",
                "preview": f"plugins {{\n  id 'net.neoforged.moddev' version '2.0.78'\n}}\n\nminecraft_version = '{mc}'",
            }
        )
        tools.append(
            {
                "name": f"Write · MainMod.java",
                "preview": f"@Mod(value = \"{mod_loader}_agent_demo\")\npublic class MainMod {{\n  // {excerpt[:40]}\n}}",
            }
        )
    elif stage_index == 2:
        tools.append(
            {
                "name": "Shell · ./gradlew build",
                "preview": "BUILD SUCCESSFUL in 42s\n3 actionable tasks: 3 executed",
            }
        )
    turn: dict[str, Any] = {
        "summary": summaries.get(stage_index, summaries[1]),
        "thinking": thinking,
        "tools": tools,
        "pending_action": None,
    }
    if stage_index == 1:
        turn["pending_action"] = _build_permission_action(payload)
    return turn


def _build_follow_up_stage_content(stage_index: int, message: str) -> dict[str, Any]:
    summaries = {
        0: f"已收到跟进：「{message[:60]}」。正在调整实现。",
        1: f"跟进修改已处理完成，正在验证相关逻辑。",
    }
    return {
        "summary": summaries.get(stage_index, summaries[0]),
        "thinking": [
            f"根据用户跟进「{message}」评估影响范围，优先修改配置与事件处理。",
        ],
        "tools": [
            {
                "name": "Edit · handler",
                "preview": f"// follow-up: {message[:80]}",
            }
        ],
        "pending_action": None,
    }


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


def _turn_progress_snapshot(record: SessionRecord, stage_index: int | None = None) -> dict[str, Any]:
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


def _freeze_turn_progress(record: SessionRecord, turn: dict[str, Any]) -> None:
    turn["progress"] = _turn_progress_snapshot(record)


def _merge_stage_into_turn(turn: dict[str, Any], stage: dict[str, Any]) -> None:
    turn["summary"] = stage.get("summary") or turn.get("summary") or ""
    for line in stage.get("thinking") or []:
        if line not in turn["thinking"]:
            turn["thinking"].append(line)
    existing = {t["name"] for t in turn.get("tools") or []}
    for tool in stage.get("tools") or []:
        if tool["name"] not in existing:
            turn.setdefault("tools", []).append(tool)
            existing.add(tool["name"])
    if stage.get("pending_action"):
        turn["pending_action"] = stage["pending_action"]


def _active_interaction_turn(record: SessionRecord) -> dict[str, Any]:
    if not record.turns:
        record.turns.append(_new_interaction_turn(1))
    return record.turns[-1]


def _ensure_mock_workspace_files(workspace: Path) -> None:
    if (workspace / "build.gradle").is_file():
        return
    (workspace / "src" / "main" / "java" / "com" / "example").mkdir(parents=True, exist_ok=True)
    (workspace / "build.gradle").write_text(
        "plugins {\n    id 'fabric-loom'\n}\n",
        encoding="utf-8",
    )
    (workspace / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
    (workspace / "src" / "main" / "java" / "com" / "example" / "ExampleMod.java").write_text(
        "package com.example;\n\npublic class ExampleMod {\n}\n",
        encoding="utf-8",
    )


def _build_delivery(record: SessionRecord) -> dict[str, Any]:
    from http_utils import artifact_slug

    slug = artifact_slug(
        record.task_title,
        payload=record.payload,
        session_id=record.session_id,
    )
    return {
        "artifact_name": f"{slug}.jar",
        "download_path": f"/api/v1/sessions/{record.session_id}/artifact",
        "test_server_path": f"/api/v1/sessions/{record.session_id}/test-server",
        "project_path": str(record.workspace_path()),
    }


@dataclass
class SessionRecord:
    session_id: str
    payload: dict[str, Any]
    mode: str
    final_prompt: str
    readable_blueprint: str
    task_title: str
    owner_id: str = ""
    status: str = "starting"
    interaction_kind: str = "build"
    stage_index: int = 0
    turns: list[dict[str, Any]] = field(default_factory=list)
    user_messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    subscribers: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list, repr=False)
    _simulation_task: asyncio.Task[None] | None = field(default=None, repr=False)
    _wait_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    delivery: dict[str, Any] | None = None
    test_server_status: str = "idle"
    test_server_port: int | None = None
    test_server_host: str = "127.0.0.1"
    pinned: bool = False
    deleted_at: str | None = None

    def workspace_path(self) -> Path:
        from config import WORKSPACE_ROOT

        root = WORKSPACE_ROOT / "_mock" / self.session_id
        root.mkdir(parents=True, exist_ok=True)
        _ensure_mock_workspace_files(root)
        return root

    def to_snapshot(self) -> dict[str, Any]:
        stages = _stages_for_record(self)
        stage_index = self.stage_index
        if self.interaction_kind == "follow_up":
            if self.status == "completed":
                stage_index = len(FOLLOW_UP_STAGES) - 1
            elif self.status in {"running", "starting", "waiting_user"}:
                stage_index = min(max(self.stage_index, 0), len(FOLLOW_UP_STAGES) - 1)
        stage_name = stages[min(stage_index, len(stages) - 1)]
        pending = None
        active_turn = self.turns[-1] if self.turns else None
        if self.status == "waiting_user" and active_turn:
            pending = active_turn.get("pending_action")

        return {
            "session_id": self.session_id,
            "mode": self.mode,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
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
            "compose_enabled": self.status in {"waiting_user", "completed", "stopped"},
            "can_stop": self.status in {"starting", "running", "waiting_user"},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "delivery": deepcopy(self.delivery) if self.delivery else None,
            "artifact_ready": bool(self.delivery),
            "artifact_file": (self.delivery or {}).get("artifact_name"),
            "test_server": {
                "status": self.test_server_status,
                "port": self.test_server_port,
                "host": self.test_server_host,
                "address": (
                    f"{self.test_server_host}:{self.test_server_port}"
                    if self.test_server_status == "running" and self.test_server_port
                    else None
                ),
            },
        }


class SessionMockStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = asyncio.Lock()

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

    async def create(
        self,
        payload: dict[str, Any],
        *,
        owner_id: str = "",
        final_prompt: str | None = None,
        readable_blueprint: str | None = None,
        task_title: str | None = None,
    ) -> SessionRecord:
        session_id = f"mock-{uuid.uuid4().hex[:12]}"
        prompt = final_prompt or payload.get("prompt") or ""
        blueprint = readable_blueprint or prompt
        title = resolve_initial_task_title(payload, task_title)

        record = SessionRecord(
            session_id=session_id,
            owner_id=owner_id,
            payload=deepcopy(payload),
            mode=payload.get("mode") or "build",
            final_prompt=prompt,
            readable_blueprint=blueprint,
            task_title=title,
            status="starting",
            stage_index=0,
        )
        async with self._lock:
            self._sessions[session_id] = record

        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        record._simulation_task = asyncio.create_task(self._run_simulation(record))
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        return self._sessions.get(session_id)

    async def update_title(self, session_id: str, title: str) -> SessionRecord:
        return await self.update_session_meta(session_id, title=title)

    async def update_session_meta(
        self,
        session_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
    ) -> SessionRecord:
        record = self._require(session_id)
        if title is not None:
            trimmed = title.strip()
            if not trimmed:
                raise ValueError("title required")
            record.task_title = trimmed[:80]
        if pinned is not None:
            record.pinned = pinned
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def trash_session(self, session_id: str) -> SessionRecord:
        record = self._require(session_id)
        record.deleted_at = _utc_now()
        record.pinned = False
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def restore_session(self, session_id: str) -> SessionRecord:
        record = self._require(session_id)
        record.deleted_at = None
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def stop(self, session_id: str) -> SessionRecord:
        record = self._require(session_id)
        if record.status in {"completed", "stopped"}:
            return record
        record.status = "stopped"
        record.updated_at = _utc_now()
        record._wait_event.set()
        if record._simulation_task and not record._simulation_task.done():
            record._simulation_task.cancel()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    @staticmethod
    def _admin_intervention_meta(
        *,
        sent_by_admin: bool = False,
        admin_actor: str | None = None,
        admin_actor_id: str | None = None,
    ) -> dict:
        if not sent_by_admin:
            return {}
        meta = {"sent_by_admin": True}
        if admin_actor:
            meta["admin_actor"] = admin_actor
        if admin_actor_id:
            meta["admin_actor_id"] = admin_actor_id
        return meta

    async def submit_action(
        self,
        session_id: str,
        *,
        choice_id: str | None = None,
        answers: dict | None = None,
        request_id: str | None = None,
        sent_by_admin: bool = False,
        admin_actor: str | None = None,
        admin_actor_id: str | None = None,
    ) -> SessionRecord:
        record = self._require(session_id)
        if record.status != "waiting_user":
            raise ValueError("no pending action")

        active_turn = record.turns[-1] if record.turns else None
        if not active_turn or not active_turn.get("pending_action"):
            raise ValueError("no pending action")

        action = active_turn["pending_action"]
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
        else:
            raise ValueError("choice_id or answers required")
        record.status = "running"
        record.updated_at = _utc_now()
        record._wait_event.set()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def start_test_server(self, session_id: str) -> SessionRecord:
        record = self._require(session_id)
        if not record.delivery:
            raise ValueError("artifact not ready")
        if record.test_server_status == "running":
            return record
        record.test_server_status = "running"
        record.test_server_port = 25565
        record.test_server_host = "127.0.0.1"
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def stop_test_server(self, session_id: str) -> SessionRecord:
        record = self._require(session_id)
        if record.test_server_status != "running":
            return record
        record.test_server_status = "idle"
        record.test_server_port = None
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def add_message(
        self,
        session_id: str,
        content: str,
        *,
        sent_by_admin: bool = False,
        admin_actor: str | None = None,
        admin_actor_id: str | None = None,
    ) -> SessionRecord:
        record = self._require(session_id)
        trimmed = content.strip()
        if not trimmed:
            raise ValueError("message required")
        if record.status not in {"waiting_user", "completed", "stopped", "error"}:
            raise ValueError("agent busy")

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
        record.updated_at = _utc_now()

        if record.status in {"completed", "stopped"}:
            record.status = "running"
            record.interaction_kind = "follow_up"
            record.stage_index = 0
            record._simulation_task = asyncio.create_task(self._run_follow_up(record, trimmed))

        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return record

    async def build_artifact(self, session_id: str) -> dict[str, Any]:
        record = self._require(session_id)
        await asyncio.sleep(1.2)
        workspace = record.workspace_path()
        libs = workspace / "build" / "libs"
        libs.mkdir(parents=True, exist_ok=True)
        jar_name = (record.delivery or {}).get("artifact_name") or "mock-mod.jar"
        (libs / jar_name).write_bytes(b"PK\x03\x04mock jar")
        if not record.delivery:
            record.delivery = _build_delivery(record)
        record.delivery["artifact_name"] = jar_name
        record.updated_at = _utc_now()
        await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        return {
            "ok": True,
            "message": "编译成功（模拟）",
            "artifact_ready": True,
            "artifact_file": jar_name,
        }

    def list_workspace_entries(self, session_id: str, path: str = "") -> dict[str, Any]:
        from agent.options import validate_workspace
        from config import WORKSPACE_ROOT
        from workspace_ops import list_workspace_entries

        record = self._require(session_id)
        workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)
        return list_workspace_entries(workspace, path)

    def read_workspace_file(self, session_id: str, path: str) -> dict[str, Any]:
        from agent.options import validate_workspace
        from config import WORKSPACE_ROOT
        from workspace_ops import read_workspace_file

        record = self._require(session_id)
        workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)
        return read_workspace_file(workspace, path)

    def resolve_workspace_download(self, session_id: str, path: str) -> Path:
        from agent.options import validate_workspace
        from config import WORKSPACE_ROOT
        from workspace_ops import resolve_workspace_rel_path

        record = self._require(session_id)
        workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)
        target = resolve_workspace_rel_path(workspace, path)
        if not target.is_file():
            raise ValueError("not a file")
        return target

    def build_workspace_archive(self, session_id: str, path: str = "") -> tuple[Any, str]:
        from agent.options import validate_workspace
        from config import WORKSPACE_ROOT
        from workspace_ops import build_workspace_zip

        record = self._require(session_id)
        workspace = validate_workspace(record.workspace_path(), WORKSPACE_ROOT)
        return build_workspace_zip(workspace, path)

    async def subscribe(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        record = self._require(session_id)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        record.subscribers.append(queue)
        try:
            yield {"type": "snapshot", "data": record.to_snapshot()}
            while True:
                event = await queue.get()
                yield event
                if event.get("type") == "close":
                    break
        finally:
            if queue in record.subscribers:
                record.subscribers.remove(queue)

    def _require(self, session_id: str) -> SessionRecord:
        record = self._sessions.get(session_id)
        if not record:
            raise KeyError(session_id)
        return record

    async def _publish(self, record: SessionRecord, event: dict[str, Any]) -> None:
        for queue in list(record.subscribers):
            await queue.put(event)

    async def _run_simulation(self, record: SessionRecord) -> None:
        try:
            record.status = "starting"
            record.stage_index = 0
            record.updated_at = _utc_now()
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
            await asyncio.sleep(1.0)
            if record.status == "stopped":
                return

            mod_name = (record.payload.get("mod_name") or "").strip()
            if mod_name:
                record.task_title = mod_name[:80]
            elif record.task_title == TITLE_GENERATING_PLACEHOLDER:
                record.task_title = _derive_task_title(record.readable_blueprint, record.final_prompt)
            record.updated_at = _utc_now()
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
            await asyncio.sleep(0.2)
            if record.status == "stopped":
                return

            record.interaction_kind = "build"
            record.stage_index = 1
            record.status = "running"
            record.updated_at = _utc_now()
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})

            stage = _build_turn_content(record.payload, 1)
            turn = _active_interaction_turn(record)
            _merge_stage_into_turn(turn, stage)
            record.updated_at = _utc_now()

            if turn.get("pending_action") and not turn.get("user_reply"):
                record.status = "waiting_user"
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
                record._wait_event.clear()
                await record._wait_event.wait()
                if record.status == "stopped":
                    return
                record.status = "running"
                record.updated_at = _utc_now()

            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
            await asyncio.sleep(2.0)

            record.stage_index = len(BUILD_STAGES) - 1
            record.status = "completed"
            record.updated_at = _utc_now()
            turn = _active_interaction_turn(record)
            _freeze_turn_progress(record, turn)
            if not record.delivery:
                record.delivery = _build_delivery(record)
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        except asyncio.CancelledError:
            if record.status != "stopped":
                record.status = "stopped"
                record.updated_at = _utc_now()
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
            raise

    async def _run_follow_up(self, record: SessionRecord, message: str) -> None:
        try:
            await asyncio.sleep(0.8)
            if record.status == "stopped":
                return

            record.interaction_kind = "follow_up"
            turn = _new_interaction_turn(len(record.turns) + 1)
            record.turns.append(turn)

            for stage_index in range(len(FOLLOW_UP_STAGES)):
                if record.status == "stopped":
                    return

                record.stage_index = stage_index
                stage = _build_follow_up_stage_content(stage_index, message)
                _merge_stage_into_turn(turn, stage)
                record.status = "running"
                record.updated_at = _utc_now()
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
                await asyncio.sleep(1.2 if stage_index == 0 else 1.0)

            record.stage_index = len(FOLLOW_UP_STAGES) - 1
            record.status = "completed"
            record.updated_at = _utc_now()
            _freeze_turn_progress(record, turn)
            await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
        except asyncio.CancelledError:
            if record.status != "stopped":
                record.status = "stopped"
                record.updated_at = _utc_now()
                await self._publish(record, {"type": "snapshot", "data": record.to_snapshot()})
            raise


    async def shutdown(self) -> None:
        pending: list[asyncio.Task[None]] = []
        for record in list(self._sessions.values()):
            for queue in list(record.subscribers):
                try:
                    queue.put_nowait({"type": "close"})
                except Exception:
                    pass
            if record._simulation_task and not record._simulation_task.done():
                record._simulation_task.cancel()
                pending.append(record._simulation_task)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


session_store = SessionMockStore()


def sse_encode(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
