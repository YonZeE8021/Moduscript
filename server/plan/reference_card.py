"""Generate Reference Card summaries from indexed repos."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from plan.chat_llm import _call_chat_async, _get_plan_llm_config
from plan.reference_config import CARD_MAX_CHARS
from plan.reference_index import load_index, ref_project_dir
from plan.reference_reader import run_reference_read_loop
from storage.file_io import ensure_dir

logger = logging.getLogger(__name__)

OnSourceReadStep = Callable[[dict[str, Any]], Awaitable[None] | None]
OnFinding = Callable[[dict[str, Any]], Awaitable[None] | None]


async def generate_reference_card(
    *,
    user_id: str,
    plan_id: str,
    project_id: str,
    context: dict[str, Any],
    ref_meta: dict[str, Any],
    plan: dict[str, Any] | None = None,
    on_source_read_step: OnSourceReadStep | None = None,
    on_finding: OnFinding | None = None,
) -> str:
    index = load_index(user_id, plan_id, project_id) or {}
    entry_points = index.get("entry_points") or []
    key_files = index.get("key_files") or []
    file_count = index.get("file_count") or 0

    config = await _get_plan_llm_config()
    llm_ok = bool(config.get("enabled", True) and (config.get("api_key") or "").strip())

    title = ref_meta.get("title") or project_id
    fallback = (
        f"## {title}\n\n"
        f"- 源码文件数（索引）: {file_count}\n"
        f"- 入口/关键文件: {', '.join((entry_points + key_files)[:8]) or '（未识别）'}\n"
        f"- 用户构想相关: 请在 handoff 后于 references/ 目录深读\n"
    )

    if not llm_ok or file_count == 0:
        return fallback[:CARD_MAX_CHARS]

    read_context = ""
    if plan and file_count > 0:
        read_ctx_msg = (
            f"为 Reference Card 阅读 {title} ({project_id}) 源码。\n"
            f"用户构想: {(context.get('user_concept') or '')[:600]}\n"
            f"入口: {entry_points[:5]}\n"
        )
        session = await run_reference_read_loop(
            user_id=user_id,
            plan_id=plan_id,
            plan=plan,
            purpose="reference_card",
            user_context=read_ctx_msg,
            on_tool_step=on_source_read_step,
            on_finding=on_finding,
        )
        read_context = session.transcript

    system_prompt = (
        "你是 Minecraft 模组架构分析助手。根据仓库索引与已读源码笔记，输出简洁 Markdown Reference Card，"
        f"不超过 {CARD_MAX_CHARS // 2} 字。包含：架构概览、注册/入口方式、与用户构想可能相关的 3–5 个路径、loader 注意事项。"
        "不要编造不存在的文件。"
    )
    user_msg = (
        f"## 参考模组\n{title}\n\n"
        f"## 项目约束\n"
        f"- MC: {context.get('minecraft_version')}\n"
        f"- Loader: {context.get('mod_loader')}\n"
        f"- 用户构想: {context.get('user_concept', '')[:800]}\n\n"
        f"## 索引\n{json.dumps({'entry_points': entry_points, 'key_files': key_files, 'file_count': file_count}, ensure_ascii=False, indent=2)}"
    )
    if read_context:
        user_msg += f"\n\n## 源码阅读笔记\n{read_context}\n"

    try:
        raw = await _call_chat_async(
            api_key=config["api_key"].strip(),
            base_url=(config.get("base_url") or "https://api.deepseek.com").strip(),
            model=(config.get("model") or "deepseek-chat").strip(),
            system_prompt=system_prompt,
            user_content=user_msg,
            temperature=0.3,
            json_mode=False,
        )
        card = (raw or fallback).strip()
        if len(card) > CARD_MAX_CHARS:
            card = card[:CARD_MAX_CHARS] + "\n…"
    except Exception as exc:
        logger.warning("reference card LLM failed: %s", exc)
        card = fallback

    base = ref_project_dir(user_id, plan_id, project_id)
    ensure_dir(base)
    (base / "card.md").write_text(card, encoding="utf-8")
    return card


async def generate_metadata_fallback_card(
    *,
    user_id: str,
    plan_id: str,
    project_id: str,
    context: dict[str, Any],
    ref_item: dict[str, Any],
    decompile_error: str = "",
) -> str:
    """When fixed decompile fails, ask plan LLM for metadata-only Reference Card (no fake repo)."""
    from modrinth_client import ModrinthError, fetch_project

    title = ref_item.get("title") or project_id
    slug = ref_item.get("slug") or project_id
    description = (ref_item.get("description") or "").strip()
    categories = ref_item.get("categories") or []

    project_meta: dict[str, Any] = {}
    try:
        project = await fetch_project(project_id)
        project_meta = {
            "title": project.get("title") or title,
            "description": (project.get("description") or description)[:2000],
            "categories": project.get("categories") or categories,
            "loaders": project.get("loaders") or [],
            "game_versions": (project.get("game_versions") or [])[:8],
        }
        title = project_meta.get("title") or title
    except ModrinthError:
        project_meta = {"title": title, "description": description[:2000], "categories": categories}

    fallback = (
        f"## {title}\n\n"
        f"- 来源: Modrinth（{slug}）\n"
        f"- 状态: 闭源，反编译未成功，仅元数据参考\n"
        f"- 失败原因: {(decompile_error or '未知')[:200]}\n"
        f"- 说明: 编写时请结合 Modrinth 描述与用户构想，勿假设存在可读的 references/ 源码树\n"
    )

    config = await _get_plan_llm_config()
    llm_ok = bool(config.get("enabled", True) and (config.get("api_key") or "").strip())
    if not llm_ok:
        return fallback[:CARD_MAX_CHARS]

    system_prompt = (
        "你是 Minecraft 模组架构分析助手。参考模组无开源仓库且反编译失败。"
        f"根据 Modrinth 元数据输出简洁 Markdown Reference Card，不超过 {CARD_MAX_CHARS // 2} 字。"
        "包含：模组功能概览、可能相关的实现思路、loader/版本注意事项。"
        "明确标注「无源码索引，仅元数据」。不要编造具体类名或文件路径。"
    )
    user_msg = (
        f"## 参考模组\n{title} ({slug})\n\n"
        f"## 项目约束\n"
        f"- MC: {context.get('minecraft_version')}\n"
        f"- Loader: {context.get('mod_loader')}\n"
        f"- 用户构想: {context.get('user_concept', '')[:800]}\n\n"
        f"## 反编译失败\n{decompile_error[:300]}\n\n"
        f"## Modrinth 元数据\n{json.dumps(project_meta, ensure_ascii=False, indent=2)}"
    )

    try:
        raw = await _call_chat_async(
            api_key=config["api_key"].strip(),
            base_url=(config.get("base_url") or "https://api.deepseek.com").strip(),
            model=(config.get("model") or "deepseek-chat").strip(),
            system_prompt=system_prompt,
            user_content=user_msg,
            temperature=0.3,
            json_mode=False,
        )
        card = (raw or fallback).strip()
        if len(card) > CARD_MAX_CHARS:
            card = card[:CARD_MAX_CHARS] + "\n…"
    except Exception as exc:
        logger.warning("metadata fallback card LLM failed: %s", exc)
        card = fallback

    base = ref_project_dir(user_id, plan_id, project_id)
    ensure_dir(base)
    (base / "card.md").write_text(card, encoding="utf-8")
    return card
