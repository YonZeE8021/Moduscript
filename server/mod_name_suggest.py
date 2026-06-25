"""Suggest Fabric mod display names via admin-configured LLM API."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from audit_log import record as audit_record
from mod_metadata import _fallback_mod_name, _sanitize_mod_name
from storage.admin_store import admin_store

logger = logging.getLogger(__name__)


async def get_mod_name_suggest_config() -> dict[str, Any]:
    return await admin_store.get_mod_name_suggest()


async def suggest_mod_name_via_llm(
    *,
    prompt: str,
    task_title: str,
    mod_name: str | None = None,
    user_id: str | None = None,
) -> str:
    """Return mod display name; uses LLM when mod_name is empty and configured."""
    provided = (mod_name or "").strip()
    if provided:
        from mod_metadata_validation import validate_mod_name

        check = validate_mod_name(provided)
        if not check.valid:
            raise ValueError(check.message)
        return provided[:40]

    fallback = _fallback_mod_name(task_title)
    config = await get_mod_name_suggest_config()
    if not config.get("enabled", True):
        logger.info("mod name suggest disabled; using fallback")
        return fallback

    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        logger.info("mod name suggest API key not set; using fallback")
        return fallback

    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-chat").strip()
    system_prompt = (
        config.get("system_prompt")
        or (
            "你是 Minecraft Fabric 1.20.1 模组命名助手。"
            "根据用户任务描述输出 JSON 对象，仅包含 mod_name 一个字符串字段。"
            "mod_name 必须是英文显示名称：仅使用拉丁字母、数字、空格及 & . ' - 符号，2-40 字符。"
            "即使任务描述或 task_title 为中文，也必须翻译或意译为简洁自然的英文 Mod 名称，"
            "例如 Guild System、Sharp Sword、Magic Crop。"
            "禁止输出中文、日文或其他 CJK 字符；不要输出 markdown 或解释。"
        )
    ).strip()
    temperature = float(config.get("temperature") or 0.3)

    user = json.dumps(
        {
            "task_title": task_title,
            "prompt_excerpt": (prompt or "")[:2000],
        },
        ensure_ascii=False,
    )

    try:
        result = await asyncio.to_thread(
            _call_suggest_api,
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
            user_content=user,
            temperature=temperature,
        )
        name = _sanitize_mod_name(str(result.get("mod_name") or "").strip()[:40], fallback)
        _log_suggest_result(
            user_id=user_id,
            model=model,
            task_title=task_title,
            mod_name=name,
            success=True,
        )
        return name or fallback
    except Exception as exc:
        _log_suggest_result(
            user_id=user_id,
            model=model,
            task_title=task_title,
            mod_name="",
            success=False,
            error=str(exc),
        )
        logger.warning("mod name suggest failed: %s", exc)
        return fallback


def _call_suggest_api(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float,
) -> dict[str, Any]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    raw = (response.choices[0].message.content or "").strip()
    return json.loads(raw)


def _log_suggest_result(
    *,
    user_id: str | None,
    model: str,
    task_title: str,
    mod_name: str,
    success: bool,
    error: str = "",
) -> None:
    detail: dict[str, Any] = {
        "model": model,
        "task_title": task_title[:80],
        "mod_name": mod_name,
        "success": success,
    }
    if error:
        detail["error"] = error
    audit_record(
        "prompt",
        "mod_name.suggest",
        level="error" if not success else "info",
        user_id=user_id,
        message="Mod 名称生成失败" if not success else "Mod 名称生成完成",
        detail=detail,
    )
