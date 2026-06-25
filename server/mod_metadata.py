"""Suggest Fabric mod metadata and session titles."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    MOD_TEMPLATE_PACKAGE,
)
from mod_metadata_validation import (
    ValidationResult,
    normalize_mod_id,
    validate_mod_id,
    validate_mod_metadata_fields,
    validate_mod_name,
    validate_package_name,
)

logger = logging.getLogger(__name__)

TITLE_GENERATING_PLACEHOLDER = "标题生成中……"


def resolve_initial_task_title(
    payload: dict[str, Any],
    task_title: str | None = None,
) -> str:
    """Prefer mod display name for session hero title; fall back to LLM placeholder."""
    mod_name = (payload.get("mod_name") or "").strip()
    if mod_name:
        return mod_name[:80]
    provided = (task_title or "").strip()
    if provided and provided != TITLE_GENERATING_PLACEHOLDER:
        return provided[:80]
    return TITLE_GENERATING_PLACEHOLDER

_ASCII_MOD_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 &'.\-]{0,39}$")


def derive_mod_id(mod_name: str) -> str:
    s = re.sub(r"[^\w]+", "_", mod_name.strip().lower())
    s = re.sub(r"_+", "_", s).strip("_")
    return normalize_mod_id(s or "moduscript")


def derive_package_name(mod_id: str) -> str:
    suffix = mod_id.replace("_", "").lower() or "mod"
    return f"{MOD_TEMPLATE_PACKAGE}.{suffix}"


def _fallback_mod_name(task_title: str, mod_name: str | None = None) -> str:
    return _sanitize_mod_name((mod_name or task_title or "Moduscript Mod").strip()[:40], "Moduscript Mod")


def _sanitize_mod_name(name: str, fallback: str = "Moduscript Mod") -> str:
    """Ensure mod display name is ASCII-only for Fabric tooling compatibility."""
    cleaned = (name or "").strip()[:40]
    if cleaned and _ASCII_MOD_NAME_RE.fullmatch(cleaned):
        return cleaned

    ascii_words = re.findall(r"[A-Za-z][A-Za-z0-9]*(?:[ &'.\-][A-Za-z0-9]+)*", cleaned)
    if ascii_words:
        candidate = ascii_words[0].strip()[:40]
        if len(candidate) >= 2 and _ASCII_MOD_NAME_RE.fullmatch(candidate):
            return candidate

    ascii_fallback = re.sub(r"[^A-Za-z0-9 ]+", " ", fallback).strip()
    ascii_fallback = re.sub(r"\s+", " ", ascii_fallback).strip()[:40]
    if len(ascii_fallback) >= 2:
        titled = ascii_fallback.title()
        if _ASCII_MOD_NAME_RE.fullmatch(titled):
            return titled

    return "Moduscript Mod"


async def suggest_mod_name_async(
    *,
    prompt: str,
    task_title: str,
    mod_name: str | None = None,
    user_id: str | None = None,
) -> str:
    provided = (mod_name or "").strip()
    if provided:
        check = validate_mod_name(provided)
        if not check.valid:
            raise ValueError(check.message)
        return _sanitize_mod_name(provided[:40], _fallback_mod_name(task_title))

    from mod_name_suggest import suggest_mod_name_via_llm

    return await suggest_mod_name_via_llm(
        prompt=prompt,
        task_title=task_title,
        mod_name=mod_name,
        user_id=user_id,
    )


def suggest_mod_name(
    *,
    prompt: str,
    task_title: str,
    mod_name: str | None = None,
) -> str:
    """Sync entry for asyncio.to_thread (session setup worker)."""
    import asyncio

    return asyncio.run(
        suggest_mod_name_async(prompt=prompt, task_title=task_title, mod_name=mod_name),
    )


async def suggest_mod_metadata(
    *,
    prompt: str,
    task_title: str,
    mod_name: str | None = None,
    mod_id: str | None = None,
    package_name: str | None = None,
    user_id: str | None = None,
) -> dict[str, str]:
    """API helper: validate fields, suggest mod_name via LLM, derive id/package when empty."""
    check = validate_mod_metadata_fields(
        mod_name=mod_name,
        mod_id=mod_id,
        package_name=package_name,
    )
    if not check.valid:
        raise ValueError(check.message)

    name_provided = (mod_name or "").strip()
    if name_provided:
        name = _sanitize_mod_name(name_provided[:40], _fallback_mod_name(task_title))
    else:
        name = await suggest_mod_name_async(
            prompt=prompt,
            task_title=task_title,
            user_id=user_id,
        )

    mid_raw = (mod_id or "").strip().lower()
    mid = normalize_mod_id(mid_raw) if mid_raw else derive_mod_id(name)
    mid_check = validate_mod_id(mid)
    if not mid_check.valid:
        raise ValueError(mid_check.message)

    pkg_raw = (package_name or "").strip().lower()
    pkg = pkg_raw if pkg_raw else derive_package_name(mid)
    pkg_check = validate_package_name(pkg)
    if not pkg_check.valid:
        raise ValueError(pkg_check.message)

    return {"mod_name": name, "mod_id": mid, "package_name": pkg}


def validate_mod_metadata_payload(
    *,
    mod_name: str | None = None,
    mod_id: str | None = None,
    package_name: str | None = None,
) -> ValidationResult:
    return validate_mod_metadata_fields(
        mod_name=mod_name,
        mod_id=mod_id,
        package_name=package_name,
    )


def _derive_task_title(readable_blueprint: str, prompt: str, fallback: str = "新任务") -> str:
    text = (readable_blueprint or prompt or "").strip()
    if not text:
        return fallback
    match = re.search(r"^##\s+(.+)$", text, re.MULTILINE)
    if match:
        return match.group(1).strip()[:80]
    first = text.splitlines()[0].lstrip("#").strip()
    return (first[:80] if first else fallback)


def suggest_task_title(*, prompt: str, readable_blueprint: str) -> str:
    """Generate a concise Chinese session title (<=80 chars)."""
    fallback = _derive_task_title(readable_blueprint, prompt)

    if not DEEPSEEK_API_KEY:
        logger.info("DEEPSEEK_API_KEY not set; using fallback task title")
        return fallback

    system = (
        "你是 Moduscript 平台的任务标题生成助手。"
        "根据用户模组开发任务描述，输出 JSON 对象，仅包含 task_title 一个字符串字段。"
        "标题须为中文，简洁概括任务核心，最多 80 字，不要引号或 markdown。"
    )
    user = json.dumps(
        {
            "prompt_excerpt": (prompt or "")[:2000],
            "blueprint_excerpt": (readable_blueprint or "")[:2000],
        },
        ensure_ascii=False,
    )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
        )
        raw = (response.choices[0].message.content or "").strip()
        data: dict[str, Any] = json.loads(raw)
        title = str(data.get("task_title") or "").strip()[:80]
        return title or fallback
    except Exception as exc:
        logger.warning("DeepSeek task title failed: %s", exc)
        return fallback
