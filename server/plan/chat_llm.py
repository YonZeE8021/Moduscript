"""OpenAI-compatible LLM for plan mode turns."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from plan.prompts import (
    build_finalize_system_prompt,
    build_finalize_user_message,
    build_regenerate_question_user_message,
    build_regenerate_turn_user_message,
    build_turn_system_prompt,
    build_turn_user_message,
)
from storage.admin_store import admin_store

logger = logging.getLogger(__name__)

LLM_NOT_CONFIGURED_MSG = "规划 LLM 未配置，请在管理后台「规划模式 LLM」中填写 API Key"


class PlanLlmError(RuntimeError):
    """规划 LLM 不可用或调用失败。"""


def _llm_configured(config: dict[str, Any]) -> bool:
    return bool(config.get("enabled", True) and (config.get("api_key") or "").strip())


def _require_llm_config(config: dict[str, Any]) -> None:
    if not _llm_configured(config):
        raise PlanLlmError(LLM_NOT_CONFIGURED_MSG)


async def _call_chat_with_tools(
    *,
    api_key: str,
    base_url: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    temperature: float,
) -> Any:
    """Single chat completion with tools (no json_mode)."""
    import asyncio

    def _run() -> Any:
        from openai import OpenAI

        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            temperature=temperature,
            stream=False,
        )
        return response.choices[0].message

    return await asyncio.to_thread(_run)


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            return json.loads(match.group(0))
        raise


def _normalize_option_complexity(value: Any) -> str:
    level = str(value or "medium").lower()
    if level not in ("low", "medium", "high"):
        return "medium"
    return level


def _normalize_options(options: list) -> list:
    normalized = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        normalized.append(
            {
                "id": str(opt.get("id") or ""),
                "label": str(opt.get("label") or opt.get("id") or ""),
                "hint": str(opt.get("hint") or ""),
                "complexity": _normalize_option_complexity(opt.get("complexity")),
            }
        )
    return normalized


def _normalize_open_questions(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text:
            out.append(text)
        if len(out) >= 3:
            break
    return out


def _normalize_questions(questions: list) -> list:
    normalized = []
    for q in questions:
        if not isinstance(q, dict):
            continue
        opts = q.get("options") or []
        if isinstance(opts, list):
            q = {
                **q,
                "options": [
                    {**opt, "complexity": _normalize_option_complexity(opt.get("complexity"))}
                    if isinstance(opt, dict)
                    else opt
                    for opt in opts
                ],
            }
        normalized.append(q)
    return normalized


def _normalize_turn(data: dict[str, Any]) -> dict[str, Any]:
    difficulty = data.get("difficulty") or {}
    if not isinstance(difficulty, dict):
        difficulty = {}
    level = str(difficulty.get("level") or "medium").lower()
    if level not in ("low", "medium", "high", "extreme"):
        level = "medium"

    readiness = data.get("readiness_hint") or {}
    if not isinstance(readiness, dict):
        readiness = {}
    sufficient = bool(readiness.get("sufficient"))

    questions = _normalize_questions(data.get("questions") or [])

    tree = data.get("blueprint_tree") or []
    if not isinstance(tree, list):
        tree = []

    reason = str(difficulty.get("reason") or "")

    return {
        "assistant_message": str(data.get("assistant_message") or "请继续补充细节。"),
        "difficulty": {"level": level, "reason": reason},
        "readiness_hint": {
            "sufficient": sufficient,
            "message": str(readiness.get("message") or ("可尝试定稿" if sufficient else "请先回答下列问题以细化方案")),
        },
        "questions": questions,
        "open_questions": _normalize_open_questions(data.get("open_questions")),
        "blueprint_tree": tree,
        "source_lookups": data.get("source_lookups") if isinstance(data.get("source_lookups"), list) else [],
    }


def _call_chat(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float,
    json_mode: bool = True,
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "stream": False,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    response = client.chat.completions.create(**kwargs)
    return (response.choices[0].message.content or "").strip()


async def _call_chat_async(**kwargs) -> str:
    return await asyncio.to_thread(_call_chat, **kwargs)


async def _get_plan_llm_config() -> dict[str, Any]:
    return await admin_store.get_plan_llm()


def _call_chat_stream(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float,
    json_mode: bool = True,
):
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": temperature,
        "stream": True,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def generate_turn_streaming(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    user_reply: dict[str, Any] | None = None,
    is_first: bool = False,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    plan: dict[str, Any] | None = None,
    read_context: str | None = None,
) -> dict[str, Any]:
    config = await _get_plan_llm_config()
    _require_llm_config(config)

    system_prompt = build_turn_system_prompt(config.get("system_prompt"))
    user_msg = build_turn_user_message(
        context=context,
        turns=turns,
        user_reply=user_reply,
        is_first=is_first,
        plan=plan,
        read_context=read_context,
    )
    return await _stream_json_turn(
        config=config,
        system_prompt=system_prompt,
        user_msg=user_msg,
        on_delta=on_delta,
        temperature=float(config.get("temperature") or 0.4),
    )


async def generate_turn(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    user_reply: dict[str, Any] | None = None,
    is_first: bool = False,
    plan: dict[str, Any] | None = None,
    read_context: str | None = None,
) -> dict[str, Any]:
    config = await _get_plan_llm_config()
    _require_llm_config(config)

    system_prompt = build_turn_system_prompt(config.get("system_prompt"))
    user_msg = build_turn_user_message(
        context=context,
        turns=turns,
        user_reply=user_reply,
        is_first=is_first,
        plan=plan,
        read_context=read_context,
    )
    temperature = float(config.get("temperature") or 0.4)
    api_key = config["api_key"].strip()
    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-chat").strip()

    try:
        raw = await _call_chat_async(
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
            user_content=user_msg,
            temperature=temperature,
            json_mode=True,
        )
        parsed = _extract_json(raw)
        return _normalize_turn(parsed)
    except Exception as exc:
        logger.warning("plan turn LLM failed: %s", exc)
        try:
            repair_raw = await _call_chat_async(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt + "\n\n上次输出无效，请只输出合法 JSON。",
                user_content=user_msg,
                temperature=temperature,
                json_mode=True,
            )
            parsed = _extract_json(repair_raw)
            return _normalize_turn(parsed)
        except Exception as repair_exc:
            raise PlanLlmError(f"规划 LLM 调用失败：{repair_exc}") from repair_exc


async def generate_finalize_markdown_streaming(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    blueprint_tree: list,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    plan: dict[str, Any] | None = None,
    read_context: str | None = None,
) -> str:
    config = await _get_plan_llm_config()
    _require_llm_config(config)

    system_prompt = build_finalize_system_prompt(config.get("finalize_system_prompt"))
    user_msg = build_finalize_user_message(
        context=context,
        turns=turns,
        blueprint_tree=blueprint_tree,
        plan=plan,
        read_context=read_context,
    )
    temperature = float(config.get("temperature") or 0.3)
    api_key = config["api_key"].strip()
    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-chat").strip()
    loop = asyncio.get_running_loop()

    async def _publish_chunk(chunk: str) -> None:
        if on_delta:
            await on_delta(chunk)

    def _stream_collect() -> str:
        parts: list[str] = []
        for chunk in _call_chat_stream(
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
            user_content=user_msg,
            temperature=temperature,
            json_mode=False,
        ):
            parts.append(chunk)
            if on_delta:
                future = asyncio.run_coroutine_threadsafe(_publish_chunk(chunk), loop)
                try:
                    future.result(timeout=10)
                except Exception:
                    pass
        return "".join(parts)

    try:
        return await asyncio.to_thread(_stream_collect)
    except Exception as exc:
        logger.warning("plan finalize stream failed: %s", exc)
        try:
            return await _call_chat_async(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_content=user_msg,
                temperature=temperature,
                json_mode=False,
            )
        except Exception as repair_exc:
            raise PlanLlmError(f"规划 LLM 调用失败：{repair_exc}") from repair_exc


async def generate_regenerate_turn_streaming(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    instruction: str | None = None,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
    plan: dict[str, Any] | None = None,
    baseline_tree: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    config = await _get_plan_llm_config()
    _require_llm_config(config)

    system_prompt = build_turn_system_prompt(config.get("system_prompt"))
    user_msg = build_regenerate_turn_user_message(
        context=context,
        turns=turns,
        instruction=instruction,
        plan=plan,
        baseline_tree=baseline_tree,
    )
    return await _stream_json_turn(
        config=config,
        system_prompt=system_prompt,
        user_msg=user_msg,
        on_delta=on_delta,
        temperature=temperature,
    )


async def generate_regenerate_question_streaming(
    *,
    context: dict[str, Any],
    turn: dict[str, Any],
    question: dict[str, Any],
    action: str,
    on_delta: Callable[[str], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    config = await _get_plan_llm_config()
    llm_ok = bool(config.get("enabled", True) and (config.get("api_key") or "").strip())
    existing = question.get("options") or []
    if not llm_ok:
        raise ValueError("规划 LLM 未配置，无法重新生成选项")

    system_prompt = (
        "你是 Minecraft 模组规划助手。只输出合法 JSON，格式 {\"options\": [...]}。"
        "每个选项含 id、label、hint、complexity(low/medium/high)。"
    )
    user_msg = build_regenerate_question_user_message(
        context=context, turn=turn, question=question, action=action
    )
    api_key = config["api_key"].strip()
    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-chat").strip()
    temperature = float(config.get("temperature") or 0.5)
    loop = asyncio.get_running_loop()

    async def _publish_chunk(chunk: str) -> None:
        if on_delta:
            await on_delta(chunk)

    def _stream_collect() -> str:
        parts: list[str] = []
        for chunk in _call_chat_stream(
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
            user_content=user_msg,
            temperature=temperature,
            json_mode=True,
        ):
            parts.append(chunk)
            if on_delta:
                future = asyncio.run_coroutine_threadsafe(_publish_chunk(chunk), loop)
                try:
                    future.result(timeout=10)
                except Exception:
                    pass
        return "".join(parts)

    try:
        raw = await asyncio.to_thread(_stream_collect)
        parsed = _extract_json(raw)
        opts = parsed.get("options") or []
        return _normalize_options(opts if isinstance(opts, list) else [])
    except Exception as exc:
        logger.warning("plan question regen failed: %s", exc)
        raise ValueError(f"重新生成选项失败: {exc}") from exc


async def _stream_json_turn(
    *,
    config: dict[str, Any],
    system_prompt: str,
    user_msg: str,
    on_delta: Callable[[str], Awaitable[None]] | None,
    temperature: float | None = None,
) -> dict[str, Any]:
    temperature = float(temperature if temperature is not None else config.get("temperature") or 0.4)
    api_key = config["api_key"].strip()
    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-chat").strip()
    loop = asyncio.get_running_loop()

    async def _publish_chunk(chunk: str) -> None:
        if on_delta:
            await on_delta(chunk)

    def _stream_collect() -> str:
        parts: list[str] = []
        for chunk in _call_chat_stream(
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
            user_content=user_msg,
            temperature=temperature,
            json_mode=True,
        ):
            parts.append(chunk)
            if on_delta:
                future = asyncio.run_coroutine_threadsafe(_publish_chunk(chunk), loop)
                try:
                    future.result(timeout=10)
                except Exception:
                    pass
        return "".join(parts)

    try:
        raw = await asyncio.to_thread(_stream_collect)
        parsed = _extract_json(raw)
        return _normalize_turn(parsed)
    except Exception as exc:
        logger.warning("plan json stream failed: %s", exc)
        try:
            raw = await _call_chat_async(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt + "\n\n上次输出无效，请只输出合法 JSON。",
                user_content=user_msg,
                temperature=temperature,
                json_mode=True,
            )
            parsed = _extract_json(raw)
            return _normalize_turn(parsed)
        except Exception as repair_exc:
            raise PlanLlmError(f"规划 LLM 调用失败：{repair_exc}") from repair_exc


async def generate_finalize_markdown(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    blueprint_tree: list,
    plan: dict[str, Any] | None = None,
    read_context: str | None = None,
) -> str:
    config = await _get_plan_llm_config()
    _require_llm_config(config)

    system_prompt = build_finalize_system_prompt(config.get("finalize_system_prompt"))
    user_msg = build_finalize_user_message(
        context=context,
        turns=turns,
        blueprint_tree=blueprint_tree,
        plan=plan,
        read_context=read_context,
    )
    temperature = float(config.get("temperature") or 0.3)
    api_key = config["api_key"].strip()
    base_url = (config.get("base_url") or "https://api.deepseek.com").strip()
    model = (config.get("model") or "deepseek-chat").strip()

    try:
        return await _call_chat_async(
            api_key=api_key,
            base_url=base_url,
            model=model,
            system_prompt=system_prompt,
            user_content=user_msg,
            temperature=temperature,
            json_mode=False,
        )
    except Exception as exc:
        raise PlanLlmError(f"规划 LLM 调用失败：{exc}") from exc
