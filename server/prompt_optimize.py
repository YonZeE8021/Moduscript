"""Optimize user mod/plugin descriptions via admin-configured DeepSeek API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from audit_log import record as audit_record
from storage.admin_store import admin_store

logger = logging.getLogger(__name__)


async def get_prompt_optimize_config() -> dict[str, Any]:
    return await admin_store.get_prompt_optimize()


async def optimize_description(prompt: str, *, user_id: str | None = None) -> str:
    """Return an optimized Markdown description for the given prompt."""
    text = (prompt or "").strip()
    if not text:
        raise ValueError("prompt required")

    config = await get_prompt_optimize_config()
    if not config.get("enabled", True):
        raise RuntimeError("描述优化功能未启用")

    api_key = (config.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("描述优化 API Key 未配置")

    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-v4-pro").strip()
    system_prompt = (
        config.get("system_prompt")
        or "优化用户输入的Minecraft模组&插件描述，使用Markdown格式直接输出"
    ).strip()
    reasoning_effort = str(config.get("reasoning_effort") or "high").strip().lower()
    if reasoning_effort not in ("low", "medium", "high"):
        reasoning_effort = "high"
    thinking_type = "enabled" if config.get("thinking_enabled", True) else "disabled"

    try:
        result = await asyncio.to_thread(
            _call_optimize_api,
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
            user_content=text,
            reasoning_effort=reasoning_effort,
            thinking_type=thinking_type,
        )
        output = (result.get("output") or "").strip()
        thinking = (result.get("thinking") or "").strip()
        if not output:
            raise ValueError("empty response from model")
        _log_optimize_result(
            user_id=user_id,
            model=model,
            input_len=len(text),
            output=output,
            thinking=thinking,
            success=True,
        )
        return output
    except Exception as exc:
        _log_optimize_result(
            user_id=user_id,
            model=model,
            input_len=len(text),
            output="",
            thinking="",
            success=False,
            error=str(exc),
        )
        logger.warning("DeepSeek description optimize failed: %s", exc)
        raise


def _extract_thinking(message: Any) -> str:
    for attr in ("reasoning_content", "thinking", "reasoning"):
        val = getattr(message, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()

    model_extra = getattr(message, "model_extra", None)
    if isinstance(model_extra, dict):
        for key in ("reasoning_content", "thinking", "reasoning"):
            val = model_extra.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    try:
        dumped = message.model_dump(exclude_none=True)
    except Exception:
        dumped = {}

    for key in ("reasoning_content", "thinking", "reasoning"):
        val = dumped.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()

    return ""


def _call_optimize_api(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_content: str,
    reasoning_effort: str,
    thinking_type: str,
) -> dict[str, str]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        stream=False,
        reasoning_effort=reasoning_effort,
        extra_body={"thinking": {"type": thinking_type}},
    )
    message = response.choices[0].message
    return {
        "output": (message.content or "").strip(),
        "thinking": _extract_thinking(message),
    }


def _log_optimize_result(
    *,
    user_id: str | None,
    model: str,
    input_len: int,
    output: str,
    thinking: str,
    success: bool,
    error: str = "",
) -> None:
    detail: dict[str, Any] = {
        "model": model,
        "input_len": input_len,
        "output": output,
        "output_len": len(output),
        "thinking": thinking,
        "thinking_len": len(thinking),
        "success": success,
    }
    if error:
        detail["error"] = error

    audit_record(
        "prompt",
        "prompt.optimize",
        level="error" if not success else "info",
        user_id=user_id,
        message="描述优化失败" if not success else "描述优化完成",
        detail=detail,
    )
    logger.info(
        "prompt.optimize success=%s model=%s input_len=%d output_len=%d thinking_len=%d",
        success,
        model,
        input_len,
        len(output),
        len(thinking),
    )
