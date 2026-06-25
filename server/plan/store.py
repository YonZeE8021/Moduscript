"""Plan session file storage."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import USERS_DIR
from storage.file_io import ensure_dir, list_json_files, read_json, write_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plans_dir(user_id: str) -> Path:
    return USERS_DIR / user_id / "plans"


class PlanStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def _plan_path(self, user_id: str, plan_id: str) -> Path:
        return _plans_dir(user_id) / f"{plan_id}.json"

    def _list_sync(self, user_id: str, *, recycled: bool = False) -> list[dict[str, Any]]:
        plans_dir = _plans_dir(user_id)
        if not plans_dir.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in list_json_files(plans_dir):
            data = read_json(path)
            if not data:
                continue
            deleted_at = data.get("deleted_at")
            is_deleted = bool(deleted_at)
            if recycled and not is_deleted:
                continue
            if not recycled and is_deleted:
                continue
            items.append(
                {
                    "plan_id": data.get("plan_id"),
                    "task_title": data.get("task_title"),
                    "status": data.get("status"),
                    "mode": "plan",
                    "pinned": bool(data.get("pinned")),
                    "deleted_at": deleted_at,
                    "created_at": data.get("created_at"),
                    "updated_at": data.get("updated_at"),
                }
            )
        items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return items

    def _get_sync(self, user_id: str, plan_id: str) -> dict[str, Any] | None:
        return read_json(self._plan_path(user_id, plan_id))

    def _save_sync(self, user_id: str, data: dict[str, Any], *, prefer_incoming_meta: bool = False) -> dict[str, Any]:
        plan_id = data["plan_id"]
        ensure_dir(_plans_dir(user_id))
        path = self._plan_path(user_id, plan_id)
        existing = read_json(path)
        if existing and not prefer_incoming_meta:
            existing_at = existing.get("updated_at") or ""
            incoming_at = data.get("updated_at") or ""
            if existing_at > incoming_at:
                existing_turns = existing.get("turns") or []
                incoming_turns = data.get("turns") or []
                incoming_has_progress = len(incoming_turns) > len(existing_turns) or (
                    len(incoming_turns) >= len(existing_turns)
                    and not data.get("processing")
                    and existing.get("processing")
                )
                if incoming_has_progress:
                    for key in ("pinned", "deleted_at", "task_title"):
                        if key in existing:
                            data[key] = existing[key]
                    data["reference_index"] = {
                        **(existing.get("reference_index") or {}),
                        **(data.get("reference_index") or {}),
                    }
                    data["reference_cards"] = {
                        **(existing.get("reference_cards") or {}),
                        **(data.get("reference_cards") or {}),
                    }
                    ex_rf = existing.get("research_findings") or []
                    inc_rf = data.get("research_findings") or []
                    data["research_findings"] = inc_rf if len(inc_rf) >= len(ex_rf) else ex_rf
                else:
                    stale_merge_keys = (
                        "pinned",
                        "deleted_at",
                        "task_title",
                        "turns",
                        "processing",
                        "blueprint_tree",
                        "status",
                        "final_markdown",
                    )
                    for key in stale_merge_keys:
                        if key in existing:
                            data[key] = existing[key]
                    data["reference_index"] = {
                        **(data.get("reference_index") or {}),
                        **(existing.get("reference_index") or {}),
                    }
                    data["reference_cards"] = {
                        **(data.get("reference_cards") or {}),
                        **(existing.get("reference_cards") or {}),
                    }
                    ex_rf = existing.get("research_findings") or []
                    inc_rf = data.get("research_findings") or []
                    data["research_findings"] = ex_rf if len(ex_rf) >= len(inc_rf) else inc_rf
        data["updated_at"] = _utc_now()
        write_json(path, data)
        return data

    def _new_plan_id(self) -> str:
        return f"plan-{uuid.uuid4().hex[:12]}"

    async def list_plans(self, user_id: str, *, recycled: bool = False) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_sync, user_id, recycled=recycled)

    def _list_all_plans_sync(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if not self.base_dir.is_dir():
            return []
        for user_dir in self.base_dir.iterdir():
            if not user_dir.is_dir():
                continue
            user_id = user_dir.name
            plans_dir = user_dir / "plans"
            if not plans_dir.is_dir():
                continue
            for path in list_json_files(plans_dir):
                data = read_json(path)
                if not data:
                    continue
                turns = data.get("turns") or []
                result.append(
                    {
                        "plan_id": data.get("plan_id"),
                        "owner_id": user_id,
                        "task_title": data.get("task_title"),
                        "status": data.get("status"),
                        "mode": "plan",
                        "pinned": bool(data.get("pinned")),
                        "deleted_at": data.get("deleted_at"),
                        "created_at": data.get("created_at"),
                        "updated_at": data.get("updated_at"),
                        "turn_count": len(turns),
                    }
                )
        result.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
        return result

    async def list_all_plans(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_all_plans_sync)

    async def get_plan(self, user_id: str, plan_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get_sync, user_id, plan_id)

    async def save_plan(
        self, user_id: str, data: dict[str, Any], *, prefer_incoming_meta: bool = False
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self._save_sync, user_id, data, prefer_incoming_meta=prefer_incoming_meta)

    async def create_plan(
        self,
        user_id: str,
        *,
        context: dict[str, Any],
        task_title: str,
        knowledge_l1: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        plan_id = self._new_plan_id()
        ctx = dict(context)
        if knowledge_l1:
            ctx["knowledge_l1"] = knowledge_l1

        status = "active" if ctx.get("knowledge_l1") else "awaiting_l1"
        data = {
            "plan_id": plan_id,
            "owner_id": user_id,
            "status": status,
            "task_title": task_title,
            "context": ctx,
            "turns": [],
            "blueprint_tree": _default_blueprint_tree(ctx.get("user_concept", "")),
            "final_markdown": None,
            "processing": False,
            "reference_index": {},
            "reference_cards": {},
            "research_findings": [],
            "pinned": False,
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        return await self.save_plan(user_id, data)


def _default_blueprint_tree(user_concept: str) -> list[dict[str, Any]]:
    summary = (user_concept or "").strip()[:500]
    return [
        {
            "id": "overview",
            "title": "需求概述",
            "status": "draft",
            "summary": summary or "待补充",
            "children": [],
            "detail": None,
            "tags": [],
        },
        {
            "id": "tech",
            "title": "技术方案",
            "status": "open",
            "summary": "待细化",
            "children": [],
        },
        {
            "id": "risks",
            "title": "风险与待定项",
            "status": "open",
            "summary": "待识别",
            "children": [],
        },
    ]


plan_store = PlanStore(USERS_DIR)
