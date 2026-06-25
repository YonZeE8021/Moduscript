"""Multi-turn tool loop for plan LLM to freely read reference sources."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from plan.chat_llm import PlanLlmError, _call_chat_with_tools, _get_plan_llm_config, _llm_configured
from plan.reference_config import (
    READ_CONTEXT_MAX_CHARS,
    READ_TOOL_MAX_ROUNDS,
    READABLE_SOURCE_KINDS,
)
from plan.reference_tools import REFERENCE_TOOL_DEFINITIONS, ReferenceToolContext, execute_reference_tool

logger = logging.getLogger(__name__)

ReadPurpose = Literal["turn", "reference_card", "finalize"]
OnToolStep = Callable[[dict[str, Any]], Awaitable[None] | None]
OnFinding = Callable[[dict[str, Any]], Awaitable[None] | None]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def readable_ref_project_ids(plan: dict[str, Any]) -> list[str]:
    index = plan.get("reference_index") or {}
    out: list[str] = []
    for pid, meta in index.items():
        if not isinstance(meta, dict):
            continue
        if meta.get("status") != "ready":
            continue
        if meta.get("source_kind") not in READABLE_SOURCE_KINDS:
            continue
        if (meta.get("file_count") or 0) <= 0:
            continue
        out.append(str(pid))
    return out


@dataclass
class ReferenceReadSession:
    transcript: str = ""
    findings_added: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    skipped: bool = False


def _build_read_system_prompt(*, purpose: ReadPurpose, project_ids: list[str], plan: dict[str, Any]) -> str:
    index = plan.get("reference_index") or {}
    summaries: list[str] = []
    for pid in project_ids:
        meta = index.get(pid) or {}
        title = meta.get("title") or pid
        eps = meta.get("entry_points") or []
        summaries.append(
            f"- {pid} ({title}): {meta.get('file_count', 0)} files, "
            f"entry_points={eps[:5]}, source_kind={meta.get('source_kind')}"
        )
    purpose_hint = {
        "turn": "为下一轮规划问题阅读参考实现，关注与用户构想相关的注册/交互/数据结构。",
        "reference_card": "阅读参考模组架构，准备生成 Reference Card 摘要。",
        "finalize": "阅读参考实现细节，为最终规划文档「参考实现」章节收集依据。",
    }[purpose]
    return (
        "你是 Minecraft 模组参考源码阅读助手。你可以通过工具自由浏览已索引的参考模组源码。\n"
        f"{purpose_hint}\n"
        "建议流程：get_reference_index → read_reference_file 读入口类 → search_reference 按需搜索。\n"
        "读够后调用 finish_reading 并给出简短 summary。\n"
        "混淆反编译代码命名可能不可读，优先读 fabric.mod.json、注册类、主入口。\n\n"
        "可用参考 project_id：\n"
        + "\n".join(summaries)
    )


def _truncate_transcript(ctx: ReferenceToolContext, finish_summary: str) -> str:
    parts = []
    if finish_summary:
        parts.append(f"## 阅读总结\n{finish_summary}\n")
    if ctx.transcript_lines:
        parts.append("## 已读片段\n" + "\n\n".join(ctx.transcript_lines))
    text = "\n".join(parts).strip()
    if len(text) > READ_CONTEXT_MAX_CHARS:
        text = text[: READ_CONTEXT_MAX_CHARS - 20] + "\n…(截断)"
    return text


async def run_reference_read_loop(
    *,
    user_id: str,
    plan_id: str,
    plan: dict[str, Any],
    purpose: ReadPurpose,
    user_context: str,
    on_tool_step: OnToolStep | None = None,
    on_finding: OnFinding | None = None,
    turn_index: int | None = None,
) -> ReferenceReadSession:
    project_ids = readable_ref_project_ids(plan)
    session = ReferenceReadSession()
    if not project_ids:
        session.skipped = True
        return session

    config = await _get_plan_llm_config()
    if not _llm_configured(config):
        session.skipped = True
        return session

    ctx = ReferenceToolContext(user_id=user_id, plan_id=plan_id)
    system_prompt = _build_read_system_prompt(purpose=purpose, project_ids=project_ids, plan=plan)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_context},
    ]
    temperature = float(config.get("temperature") or 0.3)
    api_key = config["api_key"].strip()
    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-chat").strip()

    async def _emit_step(data: dict[str, Any]) -> None:
        if not on_tool_step:
            return
        result = on_tool_step(data)
        if result is not None:
            await result

    try:
        for _round in range(READ_TOOL_MAX_ROUNDS):
            if ctx.finished:
                break
            message = await _call_chat_with_tools(
                api_key=api_key,
                base_url=base_url,
                model=model,
                messages=messages,
                tools=REFERENCE_TOOL_DEFINITIONS,
                temperature=temperature,
            )
            tool_calls = getattr(message, "tool_calls", None) or []
            content = (getattr(message, "content", None) or "").strip()
            if content:
                ctx.transcript_lines.append(f"[assistant] {content[:500]}")

            if not tool_calls:
                if content:
                    ctx.finish_summary = content[:1000]
                ctx.finished = True
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if not isinstance(args, dict):
                    args = {}

                path_hint = str(args.get("path") or args.get("query") or args.get("project_id") or "")
                await _emit_step(
                    {
                        "project_id": str(args.get("project_id") or project_ids[0]),
                        "tool": name,
                        "path": path_hint,
                        "status": "running",
                        "preview": json.dumps(args, ensure_ascii=False)[:120],
                    }
                )

                result_str, finding = await asyncio.to_thread(execute_reference_tool, ctx, name, args)
                session.tool_calls += 1

                if finding:
                    finding = {
                        **finding,
                        "id": f"rf-{uuid.uuid4().hex[:10]}",
                        "created_at": _utc_now(),
                        "turn_index": turn_index,
                        "purpose": purpose,
                    }
                    session.findings_added.append(finding)
                    if on_finding:
                        fb = on_finding(finding)
                        if fb is not None:
                            await fb

                await _emit_step(
                    {
                        "project_id": str(args.get("project_id") or project_ids[0]),
                        "tool": name,
                        "path": path_hint,
                        "status": "ok" if not ctx.finished or name == "finish_reading" else "running",
                        "preview": result_str[:200],
                    }
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_str,
                    }
                )

                if ctx.finished:
                    break
    except PlanLlmError as exc:
        logger.warning("reference read loop LLM failed (%s): %s", purpose, exc)
        session.skipped = True
        return session
    except Exception as exc:
        logger.warning("reference read loop failed (%s): %s", purpose, exc)
        session.skipped = True
        return session

    session.transcript = _truncate_transcript(ctx, ctx.finish_summary)
    return session
