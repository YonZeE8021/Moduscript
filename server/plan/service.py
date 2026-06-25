"""Plan mode orchestration service."""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from audit_log import record as audit_record
from config import WORKSPACE_ROOT
from plan.chat_llm import (
    generate_finalize_markdown_streaming,
    generate_regenerate_question_streaming,
    generate_regenerate_turn_streaming,
    generate_turn_streaming,
)
from plan.prompts import (
    build_handoff_appendix_fallback,
    format_research_findings_block,
    format_reference_cards_block,
)
from plan.reference_card import generate_metadata_fallback_card, generate_reference_card
from plan.reference_config import LOOKUPS_PER_TURN, MATERIALIZE_TIMEOUT_SEC, MAX_CODE_REFS, READ_LOOP_TIMEOUT_SEC, SNIPPETS_PER_LOOKUP
from plan.reference_index import materialize_reference, read_snippet, ref_project_dir, search_reference
from plan.reference_reader import run_reference_read_loop
from plan.store import _default_blueprint_tree, plan_store
from storage.file_io import ensure_dir
from storage.user_store import user_store

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _derive_task_title(user_concept: str, fallback: str = "新规划") -> str:
    text = (user_concept or "").strip()
    if not text:
        return fallback
    first = text.splitlines()[0].strip()
    return (first[:80] if first else fallback)


def _plan_read_depth(plan: dict[str, Any]) -> str:
    depth = (plan.get("context") or {}).get("plan_read_depth") or "standard"
    if depth not in ("fast", "standard", "deep"):
        return "standard"
    return depth


def _should_turn_pre_read(plan: dict[str, Any], *, is_first: bool) -> bool:
    depth = _plan_read_depth(plan)
    if depth == "fast":
        return False
    if depth == "standard":
        return is_first
    return True


def _should_finalize_pre_read(plan: dict[str, Any]) -> bool:
    return False


def _use_fast_reference_card(plan: dict[str, Any]) -> bool:
    return _plan_read_depth(plan) == "fast"


def _merge_tree_nodes(existing: list, updated: list) -> list:
    if not updated:
        return existing
    by_id = {node.get("id"): dict(node) for node in existing if node.get("id")}
    for node in updated:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if not nid:
            continue
        if nid in by_id:
            merged = {**by_id[nid], **node}
            old_children = by_id[nid].get("children") or []
            new_children = node.get("children") or []
            if new_children:
                merged["children"] = _merge_tree_nodes(old_children, new_children)
            elif "children" not in node and old_children:
                merged["children"] = old_children
            by_id[nid] = merged
        else:
            children = node.get("children") or []
            by_id[nid] = {**node, "children": _merge_tree_nodes([], children) if children else []}
    order = [n.get("id") for n in existing if n.get("id")]
    for node in updated:
        nid = node.get("id") if isinstance(node, dict) else None
        if nid and nid not in order:
            order.append(nid)
    return [by_id[nid] for nid in order if nid in by_id]


def _merge_blueprint_tree(existing: list, updated: list) -> list:
    return _merge_tree_nodes(existing, updated)


def _baseline_blueprint_tree(plan: dict[str, Any]) -> list[dict[str, Any]]:
    turns = plan.get("turns") or []
    if len(turns) <= 1:
        ctx = plan.get("context") or {}
        return deepcopy(_default_blueprint_tree(ctx.get("user_concept", "")))
    prior = turns[-2]
    return deepcopy(prior.get("blueprint_tree_snapshot") or [])


def _patch_reference_index_entry(
    plan: dict[str, Any],
    project_id: str,
    meta: dict[str, Any],
    *,
    card: str | None = None,
) -> dict[str, Any]:
    plan.setdefault("reference_index", {})[project_id] = meta
    if card is not None:
        plan.setdefault("reference_cards", {})[project_id] = card
    return plan


def _handoff_reference_note(plan: dict[str, Any]) -> str:
    index = plan.get("reference_index") or {}
    ready = [m for m in index.values() if isinstance(m, dict) and m.get("status") == "ready"]
    kinds = {m.get("source_kind") for m in ready}
    lines: list[str] = []
    if any(k in ("git", "decompiled", "decompiled_obfuscated") for k in kinds):
        lines.append("参考源码已 materialize 到工作区 references/ 目录，编写时请优先阅读上述路径。")
    if "decompiled_obfuscated" in kinds:
        lines.append("部分参考为反编译混淆代码（Yarn 重映射失败），阅读时需结合命名与上下文推断。")
    if "metadata_only" in kinds:
        lines.append("部分闭源参考仅提供元数据 Reference Card（反编译失败），无 references/ 源码目录。")
    if not lines:
        lines.append("参考上下文见上方 Reference Card 与 research findings。")
    return "\n".join(lines)


def _escape_xml(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _build_handoff_project_constraints(context: dict[str, Any]) -> str:
    lines = [
        "<project_constraints>",
        f"  <minecraft_version>{_escape_xml(context.get('minecraft_version', ''))}</minecraft_version>",
        f"  <loader>{_escape_xml(context.get('mod_loader', ''))}</loader>",
        f"  <deployment>{_escape_xml(context.get('platform', ''))}</deployment>",
    ]
    mod_name = (context.get("mod_name") or "").strip()
    mod_id = (context.get("mod_id") or "").strip()
    package_name = (context.get("package_name") or "").strip()
    if mod_name or mod_id or package_name:
        lines.append("  <mod_metadata>")
        if mod_name:
            lines.append(f"    <mod_name>{_escape_xml(mod_name)}</mod_name>")
        if mod_id:
            lines.append(f"    <mod_id>{_escape_xml(mod_id)}</mod_id>")
        if package_name:
            lines.append(f"    <package_name>{_escape_xml(package_name)}</package_name>")
        lines.append("  </mod_metadata>")
    lines.append("  <global_language>中文</global_language>")
    lines.append("</project_constraints>")
    return "\n".join(lines)


def _resolve_handoff_appendix(context: dict[str, Any]) -> str:
    appendix = (context.get("handoff_appendix") or "").strip()
    if appendix:
        return appendix
    return build_handoff_appendix_fallback(context)


def _build_handoff_prompt(context: dict[str, Any], final_markdown: str, plan: dict[str, Any] | None = None) -> str:
    ref_section = ""
    if plan:
        cards = format_reference_cards_block(plan).strip()
        findings = format_research_findings_block(plan).strip()
        if cards or findings:
            ref_section = (
                "\n<reference_context>\n"
                f"{cards}\n{findings}\n"
                f"{_handoff_reference_note(plan)}\n"
                "</reference_context>\n"
            )
    appendix = _resolve_handoff_appendix(context)
    lines = [
        "<task>",
        _build_handoff_project_constraints(context),
        "",
        f"<user_concept>{context.get('user_concept', '')}</user_concept>",
        ref_section,
        "<plan_summary>",
        final_markdown,
        "</plan_summary>",
    ]
    if appendix:
        lines.extend(["", appendix])
    lines.append("</task>")
    return "\n".join(lines)


def _code_refs_from_context(context: dict[str, Any]) -> list[dict[str, Any]]:
    ref = context.get("reference_mods") or {}
    manual = ref.get("manual") or []
    out = []
    for m in manual:
        if not isinstance(m, dict):
            continue
        if m.get("reference_type") != "code":
            continue
        if m.get("include_in_prompt") is False:
            continue
        out.append(m)
        if len(out) >= MAX_CODE_REFS:
            break
    return out


def _copy_refs_to_workspace(user_id: str, plan_id: str, session_id: str, plan: dict[str, Any]) -> dict[str, str]:
    paths: dict[str, str] = {}
    index = plan.get("reference_index") or {}
    workspace = WORKSPACE_ROOT / user_id / session_id / "references"
    for pid, meta in index.items():
        if meta.get("status") != "ready":
            continue
        slug = meta.get("slug") or pid
        src = ref_project_dir(user_id, plan_id, pid) / "repo"
        if not src.is_dir():
            continue
        dest = workspace / slug
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        paths[pid] = f"references/{slug}"
    return paths


class PlanService:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        self._ref_skip_events: dict[str, asyncio.Event] = {}
        self._ref_materialize_done: dict[str, asyncio.Event] = {}
        self._first_turn_started: set[str] = set()
        self._first_turn_locks: dict[str, asyncio.Lock] = {}
        self._active_bootstraps: set[str] = set()
        self._allow_source_read_sse: set[str] = set()

    def _plan_key(self, user_id: str, plan_id: str) -> str:
        return f"{user_id}:{plan_id}"

    async def _emit_ref_step(self, user_id: str, plan_id: str, data: dict[str, Any]) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if plan and plan.get("reference_wait_skipped"):
            return
        await self._publish(user_id, plan_id, {"type": "reference_step", "data": data})

    async def _emit_source_read_step(self, user_id: str, plan_id: str, data: dict[str, Any]) -> None:
        key = self._plan_key(user_id, plan_id)
        plan = await plan_store.get_plan(user_id, plan_id)
        if plan and plan.get("reference_wait_skipped") and key not in self._allow_source_read_sse:
            return
        await self._publish(user_id, plan_id, {"type": "source_read_step", "data": data})

    async def _append_read_finding(
        self, user_id: str, plan_id: str, plan: dict[str, Any], finding: dict[str, Any]
    ) -> None:
        plan.setdefault("research_findings", []).append(finding)
        await self._publish(user_id, plan_id, {"type": "research_finding", "data": finding})

    async def _run_plan_pre_read(
        self,
        user_id: str,
        plan_id: str,
        plan: dict[str, Any],
        *,
        purpose: str,
        user_context: str,
        turn_index: int | None = None,
    ) -> str:
        key = self._plan_key(user_id, plan_id)
        self._allow_source_read_sse.add(key)
        try:
            return await self._run_plan_pre_read_inner(
                user_id,
                plan_id,
                plan,
                purpose=purpose,
                user_context=user_context,
                turn_index=turn_index,
            )
        finally:
            self._allow_source_read_sse.discard(key)

    async def _run_plan_pre_read_inner(
        self,
        user_id: str,
        plan_id: str,
        plan: dict[str, Any],
        *,
        purpose: str,
        user_context: str,
        turn_index: int | None = None,
    ) -> str:
        async def on_tool_step(data: dict[str, Any]) -> None:
            await self._emit_source_read_step(user_id, plan_id, data)

        async def on_finding(finding: dict[str, Any]) -> None:
            await self._append_read_finding(user_id, plan_id, plan, finding)

        try:
            session = await asyncio.wait_for(
                run_reference_read_loop(
                    user_id=user_id,
                    plan_id=plan_id,
                    plan=plan,
                    purpose=purpose,  # type: ignore[arg-type]
                    user_context=user_context,
                    on_tool_step=on_tool_step,
                    on_finding=on_finding,
                    turn_index=turn_index,
                ),
                timeout=READ_LOOP_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("reference pre-read timed out plan=%s purpose=%s", plan_id, purpose)
            return "（参考源码阅读超时，已跳过剩余读取）"
        return session.transcript

    def _first_turn_lock(self, key: str) -> asyncio.Lock:
        if key not in self._first_turn_locks:
            self._first_turn_locks[key] = asyncio.Lock()
        return self._first_turn_locks[key]

    async def _maybe_start_first_turn(self, user_id: str, plan_id: str) -> None:
        key = self._plan_key(user_id, plan_id)
        async with self._first_turn_lock(key):
            if key in self._first_turn_started:
                return
            plan = await plan_store.get_plan(user_id, plan_id)
            if not plan or plan.get("turns"):
                return
            self._first_turn_started.add(key)
        await self._run_first_turn(user_id, plan_id)

    async def _wait_refs_or_skip(self, user_id: str, plan_id: str) -> None:
        key = self._plan_key(user_id, plan_id)
        done_ev = self._ref_materialize_done.get(key)
        skip_ev = self._ref_skip_events.get(key)
        if not done_ev:
            return
        tasks: list[asyncio.Task] = [asyncio.create_task(done_ev.wait())]
        if skip_ev:
            tasks.append(asyncio.create_task(skip_ev.wait()))
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

    async def _bootstrap_active_plan(self, user_id: str, plan_id: str) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if not plan:
            return
        code_refs = _code_refs_from_context(plan.get("context") or {})
        key = self._plan_key(user_id, plan_id)
        self._active_bootstraps.add(key)
        try:
            if not code_refs:
                await self._maybe_start_first_turn(user_id, plan_id)
                return

            self._ref_skip_events[key] = asyncio.Event()
            self._ref_materialize_done[key] = asyncio.Event()
            await self._publish(
                user_id,
                plan_id,
                {"type": "processing", "data": {"processing": True, "stage": "waiting_refs"}},
            )
            asyncio.create_task(self._materialize_all_references(user_id, plan_id))
            try:
                await asyncio.wait_for(
                    self._wait_refs_or_skip(user_id, plan_id),
                    timeout=MATERIALIZE_TIMEOUT_SEC + 120,
                )
            except asyncio.TimeoutError:
                logger.warning("bootstrap wait_refs timed out plan=%s", plan_id)
                await self._fail_stuck_references(
                    user_id,
                    plan_id,
                    f"参考索引等待超时（>{MATERIALIZE_TIMEOUT_SEC + 120}s），请重试索引或跳过",
                )
                done_ev = self._ref_materialize_done.get(key)
                if done_ev and not done_ev.is_set():
                    done_ev.set()
            await self._maybe_start_first_turn(user_id, plan_id)
        finally:
            self._active_bootstraps.discard(key)

    async def skip_reference_wait(self, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if plan.get("turns"):
            raise ValueError("已有规划轮次，无法跳过索引等待")
        if not _code_refs_from_context(plan.get("context") or {}):
            raise ValueError("当前规划无代码参考，无需跳过")
        key = self._plan_key(user_id, plan_id)
        plan["reference_wait_skipped"] = True
        await plan_store.save_plan(user_id, plan)
        skip_ev = self._ref_skip_events.get(key)
        if skip_ev and not skip_ev.is_set():
            skip_ev.set()
        await self._publish(
            user_id,
            plan_id,
            {"type": "reference_wait_skipped", "data": {"plan_id": plan_id}},
        )
        await self._publish(
            user_id,
            plan_id,
            {
                "type": "processing",
                "data": {"processing": True, "stage": "generating_first_turn"},
            },
        )
        await self._maybe_start_first_turn(user_id, plan_id)
        updated = await plan_store.get_plan(user_id, plan_id)
        return updated or plan

    async def retry_reference_materialize(self, user_id: str, plan_id: str, project_id: str) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        ref_item = None
        for m in _code_refs_from_context(plan.get("context") or {}):
            pid = m.get("project_id") or m.get("slug") or ""
            if pid == project_id:
                ref_item = m
                break
        if not ref_item:
            raise ValueError("未找到该参考模组")
        asyncio.create_task(self._materialize_one_reference(user_id, plan_id, ref_item))
        return plan

    async def _publish(self, user_id: str, plan_id: str, event: dict[str, Any]) -> None:
        key = self._plan_key(user_id, plan_id)
        queues = self._subscribers.get(key, [])
        for q in list(queues):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    async def _snapshot_event(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {"type": "snapshot", "data": plan}

    async def list_plans(self, user_id: str, *, recycled: bool = False) -> list[dict[str, Any]]:
        return await plan_store.list_plans(user_id, recycled=recycled)

    async def get_plan(self, user_id: str, plan_id: str) -> dict[str, Any] | None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if plan and plan.get("owner_id") != user_id:
            return None
        if plan and plan.get("processing") and not plan.get("turns"):
            key = self._plan_key(user_id, plan_id)
            index = plan.get("reference_index") or {}
            if (
                key not in self._active_bootstraps
                and any(isinstance(m, dict) and m.get("status") == "indexing" for m in index.values())
            ):
                plan = await self._recover_orphan_plan(user_id, plan)
        return plan

    async def _recover_orphan_plan(self, user_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Recover plans left processing=true after server restart (no live bootstrap task)."""
        plan_id = plan["plan_id"]
        key = self._plan_key(user_id, plan_id)
        if key in self._first_turn_started or key in self._active_bootstraps:
            return plan
        await self._fail_stuck_references(
            user_id,
            plan_id,
            "上次索引被中断（服务重启或超时），请重试索引",
        )
        plan = await plan_store.get_plan(user_id, plan_id) or plan
        plan["processing"] = False
        plan = await plan_store.save_plan(user_id, plan, prefer_incoming_meta=True)
        return plan

    async def _resolve_knowledge_l1(self, user_id: str, context: dict[str, Any]) -> dict[str, Any] | None:
        l1 = context.get("knowledge_l1")
        if l1 and isinstance(l1, dict) and l1.get("programming") is not None:
            l1 = dict(l1)
            l1["completed_at"] = l1.get("completed_at") or _utc_now()
            return l1
        prefs = await user_store.get_preferences(user_id)
        cached = prefs.get("knowledge_l1")
        if cached and isinstance(cached, dict):
            return cached
        return None

    async def create_plan(
        self,
        user_id: str,
        *,
        context: dict[str, Any],
        task_title: str | None = None,
    ) -> dict[str, Any]:
        ctx = dict(context)
        l1 = await self._resolve_knowledge_l1(user_id, ctx)
        if l1:
            ctx["knowledge_l1"] = l1

        title = (task_title or "").strip() or _derive_task_title(ctx.get("user_concept", ""))
        plan = await plan_store.create_plan(user_id, context=ctx, task_title=title, knowledge_l1=l1)

        if plan["status"] == "active":
            plan["processing"] = True
            plan = await plan_store.save_plan(user_id, plan)
            asyncio.create_task(self._bootstrap_active_plan(user_id, plan["plan_id"]))

        audit_record("plan", "plan.create", user_id=user_id, message="创建规划", detail={"plan_id": plan["plan_id"]})
        return plan

    async def _apply_reference_meta(
        self,
        user_id: str,
        plan_id: str,
        pid: str,
        ref_item: dict[str, Any],
        meta: dict[str, Any],
        plan_ctx: dict[str, Any],
        plan: dict[str, Any],
        *,
        on_step,
    ) -> tuple[dict[str, Any], str | None]:
        card = None
        ref_meta = {
            **ref_item,
            **{k: meta[k] for k in ("source_kind", "mapping", "degrade_reason", "version_id") if k in meta},
        }
        title = meta.get("title") or ref_item.get("title") or pid
        if (plan.get("reference_wait_skipped") or _use_fast_reference_card(plan)) and meta.get("status") == "ready":
            file_count = meta.get("file_count") or 0
            entry_points = meta.get("entry_points") or []
            key_files = meta.get("key_files") or []
            source_kind = meta.get("source_kind") or "unknown"
            if _use_fast_reference_card(plan) and not plan.get("reference_wait_skipped"):
                status_line = "快速模式：仅完成索引，未深度阅读"
            else:
                status_line = "已跳过索引等待，后台 materialize 完成"
            card = (
                f"## {title}\n\n"
                f"- 状态: {status_line}\n"
                f"- 源码文件数: {file_count}\n"
                f"- 来源: {source_kind}\n"
                f"- 入口/关键文件: {', '.join((entry_points + key_files)[:8]) or '（未识别）'}\n"
                f"- 编写时请在工作区 references/ 或 handoff 后深读\n"
            )
            base = ref_project_dir(user_id, plan_id, pid)
            ensure_dir(base)
            (base / "card.md").write_text(card, encoding="utf-8")
            return meta, card
        if meta.get("status") == "failed" and meta.get("degrade_reason") == "decompile_failed":
            decompile_error = meta.get("error") or ""
            zero_files = "0 个文件" in decompile_error
            if meta.get("failure_kind") == "decompile_error" and not zero_files:
                await on_step(
                    {
                        "project_id": pid,
                        "title": title,
                        "step": "reference_card",
                        "label": "生成元数据 Reference Card",
                        "status": "running",
                    }
                )
                card = await generate_metadata_fallback_card(
                    user_id=user_id,
                    plan_id=plan_id,
                    project_id=pid,
                    context=plan_ctx,
                    ref_item=ref_item,
                    decompile_error=decompile_error,
                )
                meta = {
                    **meta,
                    "status": "ready",
                    "source_kind": "metadata_only",
                    "agent_fallback": True,
                    "decompile_error": decompile_error,
                    "error": None,
                    "file_count": 0,
                    "entry_points": [],
                    "key_files": [],
                }
                await on_step(
                    {
                        "project_id": pid,
                        "title": title,
                        "step": "reference_card",
                        "label": "生成元数据 Reference Card",
                        "status": "ok",
                    }
                )
        elif meta.get("status") == "ready":
            await on_step(
                {
                    "project_id": pid,
                    "title": title,
                    "step": "reference_card",
                    "label": "生成 Reference Card",
                    "status": "running",
                }
            )
            card = await generate_reference_card(
                user_id=user_id,
                plan_id=plan_id,
                project_id=pid,
                context=plan_ctx,
                ref_meta=ref_meta,
                plan={**plan, "reference_index": {**(plan.get("reference_index") or {}), pid: meta}},
                on_source_read_step=lambda data: self._emit_source_read_step(user_id, plan_id, data),
                on_finding=lambda finding: self._append_read_finding(user_id, plan_id, plan, finding),
            )
            await on_step(
                {
                    "project_id": pid,
                    "title": title,
                    "step": "reference_card",
                    "label": "生成 Reference Card",
                    "status": "ok",
                }
            )
        return meta, card

    async def _fail_stuck_references(self, user_id: str, plan_id: str, error: str) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if not plan:
            return
        index = plan.setdefault("reference_index", {})
        changed = False
        for pid, meta in list(index.items()):
            if isinstance(meta, dict) and meta.get("status") == "indexing":
                index[pid] = {
                    **meta,
                    "status": "failed",
                    "error": error[:300],
                    "degrade_reason": "decompile_failed",
                }
                changed = True
                await self._publish(
                    user_id,
                    plan_id,
                    {"type": "reference_failed", "data": index[pid]},
                )
        if changed:
            await plan_store.save_plan(user_id, plan, prefer_incoming_meta=True)

    async def _materialize_one_reference(
        self, user_id: str, plan_id: str, ref_item: dict[str, Any]
    ) -> None:
        pid = ref_item.get("project_id") or ref_item.get("slug") or "unknown"

        async def on_step(data: dict[str, Any]) -> None:
            await self._emit_ref_step(user_id, plan_id, data)

        latest = await plan_store.get_plan(user_id, plan_id)
        if not latest:
            return
        _patch_reference_index_entry(
            latest,
            pid,
            {
                "status": "indexing",
                "title": ref_item.get("title") or pid,
                "slug": ref_item.get("slug") or pid,
                "source_url": ref_item.get("source_url") or "",
            },
        )
        await plan_store.save_plan(user_id, latest, prefer_incoming_meta=True)
        latest_for_ui = await plan_store.get_plan(user_id, plan_id) or latest
        if not (
            latest_for_ui.get("reference_wait_skipped") and not latest_for_ui.get("turns")
        ):
            await self._publish(user_id, plan_id, {"type": "reference_indexing", "data": {"project_id": pid}})

        plan_ctx = latest.get("context") or {}
        try:
            meta = await asyncio.wait_for(
                materialize_reference(
                    user_id,
                    plan_id,
                    ref_item,
                    plan_context=plan_ctx,
                    on_step=on_step,
                ),
                timeout=MATERIALIZE_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            logger.warning("materialize_reference timed out plan=%s pid=%s", plan_id, pid)
            meta = {
                "project_id": pid,
                "status": "failed",
                "source_url": ref_item.get("source_url") or "",
                "error": f"参考索引超时（>{MATERIALIZE_TIMEOUT_SEC}s），请重试",
                "title": ref_item.get("title") or pid,
                "slug": ref_item.get("slug") or pid,
                "degrade_reason": "decompile_failed",
                "failure_kind": "decompile_error",
            }
        except Exception as exc:
            logger.exception("materialize_reference failed plan=%s pid=%s", plan_id, pid)
            meta = {
                "project_id": pid,
                "status": "failed",
                "source_url": ref_item.get("source_url") or "",
                "error": str(exc)[:300],
                "title": ref_item.get("title") or pid,
                "slug": ref_item.get("slug") or pid,
                "degrade_reason": "decompile_failed",
            }
        latest = await plan_store.get_plan(user_id, plan_id)
        if not latest:
            return
        try:
            meta, card = await self._apply_reference_meta(
                user_id, plan_id, pid, ref_item, meta, plan_ctx, latest, on_step=on_step
            )
        except Exception as exc:
            logger.exception("apply_reference_meta failed plan=%s pid=%s", plan_id, pid)
            if meta.get("status") != "failed":
                meta = {**meta, "status": "failed", "error": str(exc)[:300]}
            card = None
        _patch_reference_index_entry(latest, pid, meta, card=card)
        await plan_store.save_plan(user_id, latest, prefer_incoming_meta=True)
        event_type = "reference_ready" if meta.get("status") == "ready" else "reference_failed"
        await self._publish(user_id, plan_id, {"type": event_type, "data": meta})

    async def _materialize_all_references(self, user_id: str, plan_id: str) -> None:
        key = self._plan_key(user_id, plan_id)
        try:
            initial = await plan_store.get_plan(user_id, plan_id)
            if not initial:
                return
            refs = _code_refs_from_context(initial.get("context") or {})
            for ref_item in refs:
                await self._materialize_one_reference(user_id, plan_id, ref_item)
        finally:
            done_ev = self._ref_materialize_done.get(key)
            if done_ev:
                done_ev.set()

    async def _apply_source_lookups(
        self, user_id: str, plan: dict[str, Any], lookups: list[dict[str, Any]]
    ) -> None:
        plan.setdefault("research_findings", [])
        user_id = plan.get("owner_id") or user_id
        plan_id = plan["plan_id"]
        turn_index = len(plan.get("turns") or [])
        for lookup in lookups[:LOOKUPS_PER_TURN]:
            if not isinstance(lookup, dict):
                continue
            pid = lookup.get("project_id") or ""
            query = (lookup.get("query") or "").strip()
            if not pid or not query:
                continue
            hits = search_reference(user_id, plan_id, pid, query)
            paths = []
            preview_parts = []
            for hit in hits[:SNIPPETS_PER_LOOKUP]:
                try:
                    snip = read_snippet(
                        user_id,
                        plan_id,
                        pid,
                        hit["path"],
                        start=max(1, hit["line"] - 5),
                        end=hit["line"] + 25,
                    )
                    paths.append({"path": snip["path"], "start": snip["start"], "end": snip["end"]})
                    preview_parts.append(snip["content"][:500])
                except ValueError:
                    continue
            finding = {
                "id": f"rf-{uuid.uuid4().hex[:10]}",
                "project_id": pid,
                "query": query,
                "reason": lookup.get("reason") or "",
                "paths": paths,
                "snippet_preview": "\n---\n".join(preview_parts)[:2000],
                "created_at": _utc_now(),
                "turn_index": turn_index,
            }
            plan["research_findings"].append(finding)
            await self._publish(
                user_id,
                plan_id,
                {"type": "research_finding", "data": finding},
            )

    async def get_references(self, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        return {
            "reference_index": plan.get("reference_index") or {},
            "reference_cards": plan.get("reference_cards") or {},
            "research_findings": plan.get("research_findings") or [],
        }

    async def lookup_reference(
        self, user_id: str, plan_id: str, project_id: str, query: str
    ) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if plan.get("processing"):
            raise ValueError("正在处理中，请稍候")
        await self._apply_source_lookups(
            user_id,
            plan,
            [{"project_id": project_id, "query": query, "reason": "用户手动检索"}],
        )
        await plan_store.save_plan(user_id, plan)
        await self._publish(user_id, plan_id, await self._snapshot_event(plan))
        return plan

    async def _run_first_turn(self, user_id: str, plan_id: str) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if not plan:
            return
        await self._generate_next_turn(user_id, plan, user_reply=None, is_first=True)

    async def retry_first_turn(self, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if plan.get("status") not in ("active", "ready"):
            raise ValueError("当前状态无法重试首轮")
        if plan.get("turns"):
            raise ValueError("已有规划轮次，无需重试首轮")
        if plan.get("processing"):
            plan["processing"] = False
            await plan_store.save_plan(user_id, plan)
        plan.pop("last_error", None)
        key = self._plan_key(user_id, plan_id)
        self._first_turn_started.discard(key)
        plan["processing"] = True
        plan = await plan_store.save_plan(user_id, plan)
        asyncio.create_task(self._run_first_turn(user_id, plan_id))
        fresh = await plan_store.get_plan(user_id, plan_id)
        return fresh or plan

    async def _persist_turn_progress(self, user_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        """Reload latest plan before persisting turn output so reference_index is not lost."""
        plan_id = plan["plan_id"]
        latest = await plan_store.get_plan(user_id, plan_id) or plan
        latest["turns"] = plan.get("turns") or []
        latest["processing"] = bool(plan.get("processing"))
        latest["status"] = plan.get("status", latest.get("status"))
        latest["blueprint_tree"] = plan.get("blueprint_tree") or latest.get("blueprint_tree") or []
        if plan.get("research_findings"):
            latest["research_findings"] = plan["research_findings"]
        if plan.get("last_error"):
            latest["last_error"] = plan["last_error"]
        else:
            latest.pop("last_error", None)
        return await plan_store.save_plan(user_id, latest)

    async def _generate_next_turn(
        self,
        user_id: str,
        plan: dict[str, Any],
        *,
        user_reply: dict[str, Any] | None,
        is_first: bool = False,
    ) -> dict[str, Any]:
        plan_id = plan["plan_id"]
        plan["processing"] = True
        await plan_store.save_plan(user_id, plan)
        await self._publish(user_id, plan_id, {"type": "processing", "data": {"processing": True}})

        try:
            async def _on_delta(text: str) -> None:
                await self._publish(user_id, plan_id, {"type": "llm_delta", "data": {"text": text}})

            turn_index = len(plan.get("turns") or [])
            read_context = ""
            if (
                _should_turn_pre_read(plan, is_first=is_first)
                and not (is_first and plan.get("reference_wait_skipped"))
            ):
                read_context = await self._run_plan_pre_read(
                    user_id,
                    plan_id,
                    plan,
                    purpose="turn",
                    user_context=(
                        f"MC {plan['context'].get('minecraft_version')} · "
                        f"{plan['context'].get('mod_loader')} · "
                        f"构想: {(plan['context'].get('user_concept') or '')[:600]}"
                    ),
                    turn_index=turn_index,
                )

            result = await generate_turn_streaming(
                context=plan["context"],
                turns=plan.get("turns") or [],
                user_reply=user_reply,
                is_first=is_first,
                on_delta=_on_delta,
                plan=plan,
                read_context=read_context or None,
            )

            if read_context and result.get("assistant_message"):
                result["assistant_message"] = (
                    str(result.get("assistant_message") or "")
                    + "\n\n（本轮已通过工具阅读参考源码，详见上下文中的阅读笔记与已检索片段。）"
                ).strip()

            if result.get("blueprint_tree"):
                plan["blueprint_tree"] = _merge_blueprint_tree(
                    plan.get("blueprint_tree") or [],
                    result["blueprint_tree"],
                )

            turn = {
                "assistant_message": result["assistant_message"],
                "difficulty": result["difficulty"],
                "readiness_hint": result["readiness_hint"],
                "questions": result.get("questions") or [],
                "open_questions": result.get("open_questions") or [],
                "user_reply": None,
                "blueprint_tree_snapshot": deepcopy(plan.get("blueprint_tree") or []),
                "created_at": _utc_now(),
            }
            plan.setdefault("turns", []).append(turn)

            if result["readiness_hint"].get("sufficient"):
                plan["status"] = "ready"
            else:
                plan["status"] = "active"

            plan.pop("last_error", None)
            plan["processing"] = False
            plan = await self._persist_turn_progress(user_id, plan)
            await self._publish(user_id, plan_id, await self._snapshot_event(plan))
            await self._publish(user_id, plan_id, {"type": "turn_ready", "data": {"plan_id": plan_id}})
            return plan
        except Exception as exc:
            logger.exception("plan turn failed: %s", exc)
            plan["processing"] = False
            plan["last_error"] = str(exc)
            plan = await self._persist_turn_progress(user_id, plan)
            await self._publish(user_id, plan_id, {"type": "error", "data": {"message": str(exc)}})
            raise

    async def submit_turn(self, user_id: str, plan_id: str, body: Any) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if plan.get("processing"):
            raise ValueError("正在处理中，请稍候")
        if plan.get("status") in ("finalized", "handed_off"):
            raise ValueError("规划已结束")

        turns = plan.get("turns") or []
        if not turns:
            raise ValueError("尚无待回答轮次")

        current = turns[-1]
        if current.get("user_reply") is not None:
            raise ValueError("当前轮次已提交，请等待下一轮")

        user_reply = {
            "answers": dict(body.answers or {}),
            "custom": dict(body.custom or {}),
            "overall_remarks": (body.overall_remarks or "").strip(),
            "submitted_at": _utc_now(),
        }
        if body.freeform_message:
            user_reply["freeform_message"] = body.freeform_message.strip()

        current["user_reply"] = user_reply
        plan["processing"] = True
        await plan_store.save_plan(user_id, plan)

        audit_record(
            "plan",
            "plan.turn",
            user_id=user_id,
            message="提交规划回答",
            detail={"plan_id": plan_id, "answers": user_reply.get("answers")},
        )

        asyncio.create_task(self._run_after_submit(user_id, plan_id))
        return plan

    async def _run_after_submit(self, user_id: str, plan_id: str) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if not plan:
            return
        turns = plan.get("turns") or []
        if not turns or turns[-1].get("user_reply") is None:
            plan["processing"] = False
            await plan_store.save_plan(user_id, plan)
            return
        user_reply = turns[-1]["user_reply"]
        try:
            await self._generate_next_turn(user_id, plan, user_reply=user_reply, is_first=False)
        except Exception:
            pass

    async def finalize(self, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if plan.get("processing"):
            raise ValueError("正在处理中，请稍候")

        plan["processing"] = True
        await plan_store.save_plan(user_id, plan)
        await self._publish(user_id, plan_id, {"type": "processing", "data": {"processing": True, "stage": "finalize"}})
        asyncio.create_task(self._run_finalize(user_id, plan_id))
        return plan

    async def _run_finalize(self, user_id: str, plan_id: str) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if not plan:
            return
        try:
            async def _on_delta(text: str) -> None:
                await self._publish(user_id, plan_id, {"type": "finalize_delta", "data": {"text": text}})

            read_context = ""
            if _should_finalize_pre_read(plan):
                read_context = await self._run_plan_pre_read(
                    user_id,
                    plan_id,
                    plan,
                    purpose="finalize",
                    user_context=(
                        f"定稿规划文档 · MC {plan['context'].get('minecraft_version')} · "
                        f"构想: {(plan['context'].get('user_concept') or '')[:600]}"
                    ),
                )

            md = await generate_finalize_markdown_streaming(
                context=plan["context"],
                turns=plan.get("turns") or [],
                blueprint_tree=plan.get("blueprint_tree") or [],
                on_delta=_on_delta,
                plan=plan,
                read_context=read_context or None,
            )
            plan["final_markdown"] = md
            plan["status"] = "finalized"
            plan.pop("last_error", None)
            plan["processing"] = False
            await plan_store.save_plan(user_id, plan)
            await self._publish(user_id, plan_id, await self._snapshot_event(plan))
            await self._publish(user_id, plan_id, {"type": "finalize_ready", "data": {"plan_id": plan_id}})
            audit_record("plan", "plan.finalize", user_id=user_id, message="规划定稿", detail={"plan_id": plan_id})
        except Exception as exc:
            logger.exception("plan finalize failed: %s", exc)
            plan["processing"] = False
            plan["last_error"] = str(exc)
            await plan_store.save_plan(user_id, plan)
            await self._publish(user_id, plan_id, {"type": "error", "data": {"message": str(exc)}})

    async def _wait_for_finalize(self, user_id: str, plan_id: str, *, timeout: float = 60.0) -> dict[str, Any] | None:
        elapsed = 0.0
        while elapsed < timeout:
            plan = await plan_store.get_plan(user_id, plan_id)
            if not plan:
                return None
            if plan.get("final_markdown"):
                return plan
            if not plan.get("processing"):
                return plan
            await asyncio.sleep(0.2)
            elapsed += 0.2
        return await plan_store.get_plan(user_id, plan_id)

    async def regenerate_turn(
        self,
        user_id: str,
        plan_id: str,
        instruction: str | None = None,
        *,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if plan.get("processing"):
            raise ValueError("正在处理中，请稍候")
        turns = plan.get("turns") or []
        if not turns:
            raise ValueError("尚无待重新生成的轮次")
        current = turns[-1]
        if current.get("user_reply") is not None:
            raise ValueError("当前轮次已提交，无法重新生成")

        plan["processing"] = True
        await plan_store.save_plan(user_id, plan)
        asyncio.create_task(self._run_regenerate_turn(user_id, plan_id, instruction, temperature))
        return plan

    async def _run_regenerate_turn(
        self,
        user_id: str,
        plan_id: str,
        instruction: str | None,
        temperature: float | None = None,
    ) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if not plan:
            return
        plan_id = plan["plan_id"]
        await self._publish(user_id, plan_id, {"type": "processing", "data": {"processing": True, "stage": "regenerate_turn"}})
        try:
            baseline = _baseline_blueprint_tree(plan)
            plan["blueprint_tree"] = deepcopy(baseline)
            await plan_store.save_plan(user_id, plan)
            await self._publish(user_id, plan_id, await self._snapshot_event(plan))

            async def _on_delta(text: str) -> None:
                await self._publish(user_id, plan_id, {"type": "llm_delta", "data": {"text": text}})

            result = await generate_regenerate_turn_streaming(
                context=plan["context"],
                turns=plan.get("turns") or [],
                instruction=instruction,
                on_delta=_on_delta,
                plan=plan,
                baseline_tree=baseline,
                temperature=temperature,
            )
            turns = plan.get("turns") or []
            if turns:
                turns[-1].update({
                    "assistant_message": result["assistant_message"],
                    "difficulty": result["difficulty"],
                    "readiness_hint": result["readiness_hint"],
                    "questions": result.get("questions") or [],
                    "open_questions": result.get("open_questions") or [],
                })
            if result.get("blueprint_tree"):
                plan["blueprint_tree"] = _merge_blueprint_tree(baseline, result["blueprint_tree"])
            else:
                plan["blueprint_tree"] = deepcopy(baseline)
            if turns:
                turns[-1]["blueprint_tree_snapshot"] = deepcopy(plan["blueprint_tree"])
            plan.pop("last_error", None)
            plan["processing"] = False
            await plan_store.save_plan(user_id, plan)
            await self._publish(user_id, plan_id, await self._snapshot_event(plan))
            await self._publish(user_id, plan_id, {"type": "turn_ready", "data": {"plan_id": plan_id}})
        except Exception as exc:
            logger.exception("regenerate turn failed: %s", exc)
            plan["processing"] = False
            plan["last_error"] = str(exc)
            await plan_store.save_plan(user_id, plan)
            await self._publish(user_id, plan_id, {"type": "error", "data": {"message": str(exc)}})

    async def regenerate_question(
        self,
        user_id: str,
        plan_id: str,
        question_id: str,
        *,
        action: str = "replace",
    ) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if plan.get("processing"):
            raise ValueError("正在处理中，请稍候")
        turns = plan.get("turns") or []
        if not turns:
            raise ValueError("尚无当前轮次")
        current = turns[-1]
        if current.get("user_reply") is not None:
            raise ValueError("当前轮次已提交，无法重新生成")

        target = None
        for q in current.get("questions") or []:
            if q.get("id") == question_id:
                target = q
                break
        if not target:
            raise ValueError("question not found")

        plan["processing"] = True
        await plan_store.save_plan(user_id, plan)
        asyncio.create_task(self._run_regenerate_question(user_id, plan_id, question_id, action))
        return plan

    async def _run_regenerate_question(
        self, user_id: str, plan_id: str, question_id: str, action: str
    ) -> None:
        plan = await plan_store.get_plan(user_id, plan_id)
        if not plan:
            return
        await self._publish(user_id, plan_id, {"type": "processing", "data": {"processing": True, "stage": "regenerate_question"}})
        try:
            current = plan["turns"][-1]
            target = next(q for q in current.get("questions") or [] if q.get("id") == question_id)

            async def _on_delta(text: str) -> None:
                await self._publish(user_id, plan_id, {"type": "llm_delta", "data": {"text": text}})

            new_opts = await generate_regenerate_question_streaming(
                context=plan["context"],
                turn=current,
                question=target,
                action=action,
                on_delta=_on_delta,
            )
            if action == "expand":
                existing_ids = {o.get("id") for o in target.get("options") or []}
                merged = list(target.get("options") or [])
                before_count = len(merged)
                for opt in new_opts:
                    if opt.get("id") not in existing_ids:
                        merged.append(opt)
                if len(merged) == before_count:
                    raise ValueError("未能扩增新选项，请尝试「重新生成」或稍后重试")
                target["options"] = merged
            else:
                target["options"] = new_opts

            plan.pop("last_error", None)
            plan["processing"] = False
            await plan_store.save_plan(user_id, plan)
            await self._publish(user_id, plan_id, await self._snapshot_event(plan))
            await self._publish(user_id, plan_id, {"type": "turn_ready", "data": {"plan_id": plan_id}})
        except Exception as exc:
            logger.exception("regenerate question failed: %s", exc)
            plan["processing"] = False
            plan["last_error"] = str(exc)
            await plan_store.save_plan(user_id, plan)
            await self._publish(user_id, plan_id, {"type": "error", "data": {"message": str(exc)}})

    async def handoff(self, user_id: str, plan_id: str, *, session_service: Any) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")

        if not plan.get("final_markdown"):
            plan = await self.finalize(user_id, plan_id)
            plan = await self._wait_for_finalize(user_id, plan_id) or plan
            if not plan.get("final_markdown"):
                raise ValueError("定稿尚未完成，请稍候再试")

        ctx = plan.get("context") or {}
        final_md = plan.get("final_markdown") or ""
        prompt = _build_handoff_prompt(ctx, final_md, plan)

        raw_reqs = ctx.get("requirements") or []
        if raw_reqs and isinstance(raw_reqs[0], dict):
            req_ids = [r["id"] for r in raw_reqs if isinstance(r, dict) and r.get("id")]
        else:
            req_ids = raw_reqs

        ref_mods = ctx.get("reference_mods")
        if not ref_mods:
            ref_mods = None

        payload = {
            "prompt": prompt,
            "mode": "build",
            "minecraft_version": ctx.get("minecraft_version", "1.20.1"),
            "mod_loader": ctx.get("mod_loader", "fabric"),
            "platform": ctx.get("platform", "unspecified"),
            "reference_mods": ref_mods,
            "requirements": req_ids,
            "requirements_detail": ctx.get("requirements_detail") or {},
            "locale": ctx.get("locale", "zh-CN"),
            "interruption_level": ctx.get("interruption_level", 2),
            "max_turns": ctx.get("max_turns", 150),
            "handoff_plan_id": plan_id,
            "reference_index": deepcopy(plan.get("reference_index") or {}),
        }
        if ctx.get("mod_name"):
            payload["mod_name"] = ctx["mod_name"]
        if ctx.get("mod_id"):
            payload["mod_id"] = ctx["mod_id"]
        if ctx.get("package_name"):
            payload["package_name"] = ctx["package_name"]

        readable = final_md
        title = plan.get("task_title") or _derive_task_title(ctx.get("user_concept", ""))

        record = await session_service.create(
            user_id,
            payload,
            final_prompt=prompt,
            readable_blueprint=readable,
            task_title=title,
        )

        plan["status"] = "handed_off"
        await plan_store.save_plan(user_id, plan)

        audit_record(
            "plan",
            "plan.handoff",
            user_id=user_id,
            message="规划移交编写",
            detail={"plan_id": plan_id, "session_id": record.session_id},
        )

        return {
            "plan_id": plan_id,
            "session_id": record.session_id,
            "redirect_url": f"/session.html?session_id={record.session_id}",
        }

    async def update_knowledge_l1(self, user_id: str, plan_id: str, l1: dict[str, Any]) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")

        l1 = dict(l1)
        l1["completed_at"] = _utc_now()
        plan.setdefault("context", {})["knowledge_l1"] = l1
        if plan.get("status") == "awaiting_l1":
            plan["status"] = "active"
            await plan_store.save_plan(user_id, plan)
            asyncio.create_task(self._run_first_turn(user_id, plan_id))
        else:
            await plan_store.save_plan(user_id, plan)

        await user_store.save_preferences(user_id, {"knowledge_l1": l1})
        return plan

    async def update_plan_meta(
        self,
        user_id: str,
        plan_id: str,
        *,
        task_title: str | None = None,
        pinned: bool | None = None,
    ) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        if task_title is not None:
            plan["task_title"] = task_title.strip()[:80]
        if pinned is not None:
            plan["pinned"] = pinned
        await plan_store.save_plan(user_id, plan, prefer_incoming_meta=True)
        return plan

    async def trash_plan(self, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        plan["deleted_at"] = _utc_now()
        plan["pinned"] = False
        await plan_store.save_plan(user_id, plan, prefer_incoming_meta=True)
        audit_record("plan", "plan.trash", user_id=user_id, message="规划移至回收站", detail={"plan_id": plan_id})
        return plan

    async def restore_plan(self, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("plan not found")
        plan["deleted_at"] = None
        await plan_store.save_plan(user_id, plan, prefer_incoming_meta=True)
        audit_record("plan", "plan.restore", user_id=user_id, message="规划已恢复", detail={"plan_id": plan_id})
        return plan

    async def subscribe(self, user_id: str, plan_id: str) -> asyncio.Queue:
        key = self._plan_key(user_id, plan_id)
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._subscribers.setdefault(key, []).append(q)
        return q

    async def unsubscribe(self, user_id: str, plan_id: str, q: asyncio.Queue) -> None:
        key = self._plan_key(user_id, plan_id)
        async with self._lock:
            queues = self._subscribers.get(key, [])
            if q in queues:
                queues.remove(q)

    async def event_stream(self, user_id: str, plan_id: str) -> AsyncIterator[dict[str, Any]]:
        plan = await self.get_plan(user_id, plan_id)
        if not plan:
            return
        yield await self._snapshot_event(plan)

        q = await self.subscribe(user_id, plan_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield event
                except asyncio.TimeoutError:
                    yield {"type": "ping"}
        finally:
            await self.unsubscribe(user_id, plan_id, q)


plan_service = PlanService()
