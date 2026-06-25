"""Admin settings and shared LLM configuration."""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import (
    ADMIN_DIR,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_OPTIMIZE_MODEL,
    DEEPSEEK_OPTIMIZE_REASONING_EFFORT,
)
from storage.file_io import ensure_dir, read_json, write_json


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return key[:4] + "****" + key[-4:]


DEFAULT_SETTINGS = {
    "registration_enabled": True,
    "shared_llm_enabled": True,
    "beta_banner_enabled": True,
    "beta_banner_text": "Moduscript 正在封闭测试中，功能与数据可能随时变更，感谢您的参与与反馈。",
    "beta_agreement_url": "/docs/CLOSED_BETA.md",
    "feedback_email": "feedback@example.com",
    "updated_at": None,
}

DEFAULT_LLM = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "subagent_model": "",
    "updated_at": None,
}

DEFAULT_PROMPT_OPTIMIZE = {
    "enabled": True,
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-v4-pro",
    "system_prompt": "优化用户输入的Minecraft模组&插件描述，使用Markdown格式直接输出",
    "reasoning_effort": "high",
    "thinking_enabled": True,
    "updated_at": None,
}

DEFAULT_REQUIREMENTS = [
    {
        "id": "usage_docs",
        "title": "详细使用文档",
        "description": (
            "完成后创建简要版与详细技术版 MD。简要版面向玩家与非技术用户：简介、基本原理、"
            "使用方法、配置介绍（若有）、常见问题、环境依赖安装方法；详细技术版供其他开发者参考。"
        ),
        "detail": {
            "brief_sections": [
                "简介",
                "基本原理",
                "使用方法",
                "配置介绍",
                "常见问题",
                "环境依赖安装",
            ],
            "technical_for": "developers",
            "cost": "medium",
        },
        "enabled": True,
    },
    {
        "id": "production_arch",
        "title": "生产级架构质量",
        "description": "模组按生产级标准构建，保证清晰的模块边界、可维护性与可测试性。",
        "detail": {
            "standard": "production",
            "focus": ["architecture", "maintainability", "module_boundaries"],
            "cost": "high",
        },
        "enabled": True,
    },
    {
        "id": "rich_config",
        "title": "充足配置项",
        "description": "通过配置文件（如 common/client 分离）暴露充足可调参数，便于玩家与整合包作者自定义。",
        "detail": {
            "config_style": "file_based",
            "coverage": "comprehensive",
            "cost": "medium",
        },
        "enabled": True,
    },
    {
        "id": "graceful_upgrade",
        "title": "优雅升级兼容",
        "description": "在 mod 版本升级时保持优雅兼容，考虑数据迁移、配置迁移与 API 变更的向后兼容策略。",
        "detail": {
            "strategy": "graceful_upgrade",
            "focus": ["data_migration", "config_migration", "api_compatibility"],
            "cost": "high",
        },
        "enabled": True,
    },
    {
        "id": "evaluate_difficulty",
        "title": "评估实现难度",
        "description": (
            "对复杂 mod 与复杂功能评估实现难度；优先实现最小可行版本（MVP）验证核心机制，再迭代完善。"
        ),
        "detail": {
            "strategy": "mvp_first",
            "evaluate_difficulty": True,
            "cost": "low",
        },
        "enabled": True,
    },
]

DEFAULT_PLAN_LLM = {
    "enabled": True,
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "system_prompt": (
        "你是 Minecraft 模组/插件规划助手。通过多轮结构化问答帮用户细化 Mod 需求与技术方案。"
        "每轮输出必须是合法 JSON。"
    ),
    "finalize_system_prompt": (
        "你是 Minecraft 模组规划助手，负责将多轮问答结果整理为可执行的 Markdown 规划文档。"
    ),
    "temperature": 0.4,
    "updated_at": None,
}

DEFAULT_PROFILE_NAME = "默认"
PROFILE_NAME_MAX_LEN = 64

SHARED_LLM_KEYS = ("base_url", "api_key", "model", "subagent_model")
PROMPT_OPTIMIZE_KEYS = (
    "enabled",
    "base_url",
    "api_key",
    "model",
    "system_prompt",
    "reasoning_effort",
    "thinking_enabled",
)
MOD_NAME_SUGGEST_KEYS = (
    "enabled",
    "base_url",
    "api_key",
    "model",
    "system_prompt",
    "temperature",
)
PLAN_LLM_KEYS = (
    "enabled",
    "base_url",
    "api_key",
    "model",
    "system_prompt",
    "finalize_system_prompt",
    "temperature",
)

DEFAULT_MOD_NAME_SUGGEST = {
    "enabled": True,
    "base_url": "https://api.deepseek.com",
    "api_key": "",
    "model": "deepseek-chat",
    "system_prompt": (
        "你是 Minecraft Fabric 1.20.1 模组命名助手。"
        "根据用户任务描述输出 JSON 对象，仅包含 mod_name 一个字符串字段。"
        "mod_name 必须是英文显示名称：仅使用拉丁字母、数字、空格及 & . ' - 符号，2-40 字符。"
        "即使任务描述或 task_title 为中文，也必须翻译或意译为简洁自然的英文 Mod 名称，"
        "例如 Guild System、Sharp Sword、Magic Crop。"
        "禁止输出中文、日文或其他 CJK 字符；不要输出 markdown 或解释。"
    ),
    "temperature": 0.3,
    "updated_at": None,
}


class AdminStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.settings_path = base_dir / "settings.json"
        self.llm_path = base_dir / "llm_shared.json"
        self.prompt_optimize_path = base_dir / "prompt_optimize.json"
        self.mod_name_suggest_path = base_dir / "mod_name_suggest.json"
        self.plan_llm_path = base_dir / "plan_llm.json"
        self.llm_profiles_path = base_dir / "llm_profiles.json"
        self.default_requirements_path = base_dir / "default_requirements.json"

    def init(self) -> None:
        ensure_dir(self.base_dir)
        if not self.settings_path.is_file():
            settings = {**DEFAULT_SETTINGS, "updated_at": _utc_now()}
            write_json(self.settings_path, settings)
        if not self.llm_path.is_file():
            llm = self._seed_llm_from_env()
            write_json(self.llm_path, llm)
        if not self.prompt_optimize_path.is_file():
            prompt_optimize = self._seed_prompt_optimize_from_env()
            write_json(self.prompt_optimize_path, prompt_optimize)
        if not self.mod_name_suggest_path.is_file():
            mod_name_suggest = self._seed_mod_name_suggest_from_env()
            write_json(self.mod_name_suggest_path, mod_name_suggest)
        if not self.plan_llm_path.is_file():
            plan_llm = self._seed_plan_llm_from_env()
            write_json(self.plan_llm_path, plan_llm)
        if not self.default_requirements_path.is_file():
            write_json(
                self.default_requirements_path,
                {"items": [dict(item) for item in DEFAULT_REQUIREMENTS], "updated_at": _utc_now()},
            )
        if not self.llm_profiles_path.is_file():
            self._seed_llm_profiles_from_runtime()

    def _pick_config_fields(self, cfg: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
        return {key: cfg.get(key) for key in keys}

    def _snapshot_runtime_llm_configs(self) -> dict[str, Any]:
        return {
            "shared_llm": self._pick_config_fields(self._get_llm_sync(), SHARED_LLM_KEYS),
            "prompt_optimize": self._pick_config_fields(
                self._get_prompt_optimize_sync(), PROMPT_OPTIMIZE_KEYS
            ),
            "mod_name_suggest": self._pick_config_fields(
                self._get_mod_name_suggest_sync(), MOD_NAME_SUGGEST_KEYS
            ),
            "plan_llm": self._pick_config_fields(self._get_plan_llm_sync(), PLAN_LLM_KEYS),
        }

    def _apply_profile_to_runtime(self, profile: dict[str, Any]) -> None:
        now = _utc_now()
        shared = profile.get("shared_llm") or {}
        shared["updated_at"] = now
        write_json(self.llm_path, shared)

        prompt_optimize = {**DEFAULT_PROMPT_OPTIMIZE, **(profile.get("prompt_optimize") or {})}
        prompt_optimize["updated_at"] = now
        write_json(self.prompt_optimize_path, prompt_optimize)

        mod_name = {**DEFAULT_MOD_NAME_SUGGEST, **(profile.get("mod_name_suggest") or {})}
        mod_name["updated_at"] = now
        write_json(self.mod_name_suggest_path, mod_name)

        plan_llm = {**DEFAULT_PLAN_LLM, **(profile.get("plan_llm") or {})}
        plan_llm["updated_at"] = now
        write_json(self.plan_llm_path, plan_llm)

    def _normalize_profile_name(self, name: str) -> str:
        normalized = str(name or "").strip()
        if not normalized:
            raise ValueError("预设名称不能为空")
        if len(normalized) > PROFILE_NAME_MAX_LEN:
            raise ValueError(f"预设名称不能超过 {PROFILE_NAME_MAX_LEN} 个字符")
        return normalized

    def _new_profile_id(self) -> str:
        return f"profile-{uuid.uuid4().hex[:12]}"

    def _build_profile_entry(self, name: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        now = _utc_now()
        data = snapshot if snapshot is not None else self._snapshot_runtime_llm_configs()
        return {
            "id": self._new_profile_id(),
            "name": self._normalize_profile_name(name),
            "created_at": now,
            "updated_at": now,
            **data,
        }

    def _seed_llm_profiles_from_runtime(self) -> dict[str, Any]:
        profile = self._build_profile_entry(DEFAULT_PROFILE_NAME)
        profile["id"] = "profile-default"
        payload = {"active_profile_id": profile["id"], "profiles": [profile]}
        write_json(self.llm_profiles_path, payload)
        return payload

    def _get_profiles_sync(self) -> dict[str, Any]:
        data = read_json(self.llm_profiles_path, None)
        if not data or not isinstance(data.get("profiles"), list) or not data["profiles"]:
            return self._seed_llm_profiles_from_runtime()
        profiles = [p for p in data["profiles"] if isinstance(p, dict) and p.get("id")]
        if not profiles:
            return self._seed_llm_profiles_from_runtime()
        active_id = str(data.get("active_profile_id") or profiles[0]["id"])
        if not any(p.get("id") == active_id for p in profiles):
            active_id = profiles[0]["id"]
        return {"active_profile_id": active_id, "profiles": profiles}

    def _save_profiles_sync(self, active_profile_id: str, profiles: list[dict[str, Any]]) -> None:
        write_json(
            self.llm_profiles_path,
            {"active_profile_id": active_profile_id, "profiles": profiles},
        )

    def _find_profile(self, profiles: list[dict[str, Any]], profile_id: str) -> dict[str, Any] | None:
        for profile in profiles:
            if profile.get("id") == profile_id:
                return profile
        return None

    def _get_profiles_public_sync(self) -> dict[str, Any]:
        data = self._get_profiles_sync()
        active_id = data["active_profile_id"]
        return {
            "active_profile_id": active_id,
            "profiles": [
                {
                    "id": p["id"],
                    "name": p.get("name") or p["id"],
                    "updated_at": p.get("updated_at"),
                    "is_active": p["id"] == active_id,
                }
                for p in data["profiles"]
            ],
        }

    def _sync_active_profile_from_runtime(self) -> None:
        data = self._get_profiles_sync()
        profile = self._find_profile(data["profiles"], data["active_profile_id"])
        if not profile:
            return
        snapshot = self._snapshot_runtime_llm_configs()
        profile.update(snapshot)
        profile["updated_at"] = _utc_now()
        self._save_profiles_sync(data["active_profile_id"], data["profiles"])

    def _create_profile_sync(self, name: str) -> tuple[dict[str, Any], str]:
        data = self._get_profiles_sync()
        profile = self._build_profile_entry(name)
        data["profiles"].append(profile)
        self._save_profiles_sync(data["active_profile_id"], data["profiles"])
        return self._get_profiles_public_sync(), profile["id"]

    def _rename_profile_sync(self, profile_id: str, name: str) -> dict[str, Any]:
        data = self._get_profiles_sync()
        profile = self._find_profile(data["profiles"], profile_id)
        if not profile:
            raise ValueError("预设不存在")
        profile["name"] = self._normalize_profile_name(name)
        profile["updated_at"] = _utc_now()
        self._save_profiles_sync(data["active_profile_id"], data["profiles"])
        return self._get_profiles_public_sync()

    def _delete_profile_sync(self, profile_id: str) -> dict[str, Any]:
        data = self._get_profiles_sync()
        if len(data["profiles"]) <= 1:
            raise ValueError("至少保留一条预设")
        if profile_id == data["active_profile_id"]:
            raise ValueError("无法删除当前生效的预设，请先切换到其他预设")
        profile = self._find_profile(data["profiles"], profile_id)
        if not profile:
            raise ValueError("预设不存在")
        data["profiles"] = [p for p in data["profiles"] if p.get("id") != profile_id]
        self._save_profiles_sync(data["active_profile_id"], data["profiles"])
        return self._get_profiles_public_sync()

    def _activate_profile_sync(self, profile_id: str) -> dict[str, Any]:
        data = self._get_profiles_sync()
        profile = self._find_profile(data["profiles"], profile_id)
        if not profile:
            raise ValueError("预设不存在")
        self._apply_profile_to_runtime(profile)
        data["active_profile_id"] = profile_id
        self._save_profiles_sync(profile_id, data["profiles"])
        return self._get_profiles_public_sync()

    def _normalize_requirement_item(self, item: dict[str, Any]) -> dict[str, Any]:
        item_id = str(item.get("id") or "").strip()
        title = str(item.get("title") or "").strip()
        if not item_id:
            raise ValueError("要求项 id 不能为空")
        if not title:
            raise ValueError(f"要求项 {item_id} 的标题不能为空")
        detail = item.get("detail")
        if detail is not None and not isinstance(detail, dict):
            raise ValueError(f"要求项 {item_id} 的 detail 必须是对象")
        return {
            "id": item_id,
            "title": title,
            "description": str(item.get("description") or ""),
            "detail": dict(detail) if isinstance(detail, dict) else {},
            "enabled": item.get("enabled") is not False,
        }

    def _validate_requirements_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not items:
            raise ValueError("至少保留一项默认要求")
        seen: set[str] = set()
        normalized: list[dict[str, Any]] = []
        for raw in items:
            if not isinstance(raw, dict):
                raise ValueError("要求项格式无效")
            item = self._normalize_requirement_item(raw)
            if item["id"] in seen:
                raise ValueError(f"重复的要求项 id: {item['id']}")
            seen.add(item["id"])
            normalized.append(item)
        return normalized

    def _get_default_requirements_sync(self) -> dict[str, Any]:
        data = read_json(self.default_requirements_path, None)
        if not data or not isinstance(data.get("items"), list) or not data["items"]:
            return {"items": [dict(item) for item in DEFAULT_REQUIREMENTS], "updated_at": None}
        try:
            items = self._validate_requirements_items(data["items"])
        except ValueError:
            return {"items": [dict(item) for item in DEFAULT_REQUIREMENTS], "updated_at": None}
        return {"items": items, "updated_at": data.get("updated_at")}

    def _update_default_requirements_sync(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        normalized = self._validate_requirements_items(items)
        payload = {"items": normalized, "updated_at": _utc_now()}
        write_json(self.default_requirements_path, payload)
        return payload

    def _seed_llm_from_env(self) -> dict[str, Any]:
        llm = {**DEFAULT_LLM, "updated_at": _utc_now()}
        base_url = os.getenv("ANTHROPIC_BASE_URL", "")
        api_key = os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_API_KEY") or ""
        model = os.getenv("ANTHROPIC_MODEL", "")
        subagent = os.getenv("CLAUDE_CODE_SUBAGENT_MODEL", "")
        if base_url:
            llm["base_url"] = base_url
        if api_key:
            llm["api_key"] = api_key
        if model:
            llm["model"] = model
        if subagent:
            llm["subagent_model"] = subagent
        return llm

    def _seed_prompt_optimize_from_env(self) -> dict[str, Any]:
        cfg = {**DEFAULT_PROMPT_OPTIMIZE, "updated_at": _utc_now()}
        if DEEPSEEK_BASE_URL:
            cfg["base_url"] = DEEPSEEK_BASE_URL
        if DEEPSEEK_API_KEY:
            cfg["api_key"] = DEEPSEEK_API_KEY
        if DEEPSEEK_OPTIMIZE_MODEL:
            cfg["model"] = DEEPSEEK_OPTIMIZE_MODEL
        effort = (DEEPSEEK_OPTIMIZE_REASONING_EFFORT or "high").strip().lower()
        if effort in ("low", "medium", "high"):
            cfg["reasoning_effort"] = effort
        return cfg

    def _seed_mod_name_suggest_from_env(self) -> dict[str, Any]:
        cfg = {**DEFAULT_MOD_NAME_SUGGEST, "updated_at": _utc_now()}
        if DEEPSEEK_BASE_URL:
            cfg["base_url"] = DEEPSEEK_BASE_URL
        if DEEPSEEK_API_KEY:
            cfg["api_key"] = DEEPSEEK_API_KEY
        if DEEPSEEK_MODEL:
            cfg["model"] = DEEPSEEK_MODEL
        return cfg

    def _seed_plan_llm_from_env(self) -> dict[str, Any]:
        cfg = {**DEFAULT_PLAN_LLM, "updated_at": _utc_now()}
        if DEEPSEEK_BASE_URL:
            cfg["base_url"] = DEEPSEEK_BASE_URL
        if DEEPSEEK_API_KEY:
            cfg["api_key"] = DEEPSEEK_API_KEY
        if DEEPSEEK_MODEL:
            cfg["model"] = DEEPSEEK_MODEL
        return cfg

    def build_env(self, llm: dict[str, Any]) -> dict[str, str]:
        env: dict[str, str] = {}
        if llm.get("base_url"):
            env["ANTHROPIC_BASE_URL"] = llm["base_url"]
        if llm.get("api_key"):
            env["ANTHROPIC_AUTH_TOKEN"] = llm["api_key"]
            env["ANTHROPIC_API_KEY"] = llm["api_key"]
        model = llm.get("model") or ""
        if model:
            env["ANTHROPIC_MODEL"] = model
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = model
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = model
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = model
        subagent = llm.get("subagent_model") or ""
        if subagent:
            env["CLAUDE_CODE_SUBAGENT_MODEL"] = subagent
        return env

    def _get_settings_sync(self) -> dict[str, Any]:
        return read_json(self.settings_path, DEFAULT_SETTINGS) or DEFAULT_SETTINGS

    def _update_settings_sync(self, data: dict[str, Any]) -> dict[str, Any]:
        settings = self._get_settings_sync()
        for key in (
            "registration_enabled",
            "shared_llm_enabled",
            "beta_banner_enabled",
            "beta_banner_text",
            "beta_agreement_url",
            "feedback_email",
        ):
            if key in data and data[key] is not None:
                settings[key] = data[key]
        settings["updated_at"] = _utc_now()
        write_json(self.settings_path, settings)
        return settings

    def _get_llm_sync(self) -> dict[str, Any]:
        return read_json(self.llm_path, DEFAULT_LLM) or DEFAULT_LLM

    def _update_llm_sync(self, data: dict[str, Any]) -> dict[str, Any]:
        llm = self._get_llm_sync()
        for key in ("base_url", "api_key", "model", "subagent_model"):
            if key in data and data[key] is not None:
                if key == "api_key" and data[key] == "":
                    continue
                llm[key] = data[key]
        llm["updated_at"] = _utc_now()
        write_json(self.llm_path, llm)
        self._sync_active_profile_from_runtime()
        return llm

    def _public_llm(self, llm: dict[str, Any]) -> dict[str, Any]:
        return {
            "base_url": llm.get("base_url", ""),
            "model": llm.get("model", ""),
            "subagent_model": llm.get("subagent_model", ""),
            "api_key_masked": _mask_api_key(llm.get("api_key") or ""),
            "configured": bool(llm.get("api_key") or llm.get("base_url")),
            "updated_at": llm.get("updated_at"),
        }

    def _get_prompt_optimize_sync(self) -> dict[str, Any]:
        cfg = read_json(self.prompt_optimize_path, DEFAULT_PROMPT_OPTIMIZE) or DEFAULT_PROMPT_OPTIMIZE
        return {**DEFAULT_PROMPT_OPTIMIZE, **cfg}

    def _update_prompt_optimize_sync(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = self._get_prompt_optimize_sync()
        for key in (
            "enabled",
            "base_url",
            "api_key",
            "model",
            "system_prompt",
            "reasoning_effort",
            "thinking_enabled",
        ):
            if key in data and data[key] is not None:
                if key == "api_key" and data[key] == "":
                    continue
                cfg[key] = data[key]
        effort = str(cfg.get("reasoning_effort") or "high").strip().lower()
        if effort not in ("low", "medium", "high"):
            effort = "high"
        cfg["reasoning_effort"] = effort
        cfg["updated_at"] = _utc_now()
        write_json(self.prompt_optimize_path, cfg)
        self._sync_active_profile_from_runtime()
        return cfg

    def _public_prompt_optimize(self, cfg: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "system_prompt": cfg.get("system_prompt", ""),
            "reasoning_effort": cfg.get("reasoning_effort", "high"),
            "thinking_enabled": bool(cfg.get("thinking_enabled", True)),
            "api_key_masked": _mask_api_key(cfg.get("api_key") or ""),
            "configured": bool(cfg.get("api_key")),
            "updated_at": cfg.get("updated_at"),
        }

    async def get_settings(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_settings_sync)

    async def update_settings(self, data: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._update_settings_sync, data)

    async def get_shared_llm(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_llm_sync)

    async def get_shared_llm_public(self) -> dict[str, Any]:
        llm = await asyncio.to_thread(self._get_llm_sync)
        return self._public_llm(llm)

    async def update_shared_llm(self, data: dict[str, Any]) -> dict[str, Any]:
        llm = await asyncio.to_thread(self._update_llm_sync, data)
        return self._public_llm(llm)

    async def get_prompt_optimize(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_prompt_optimize_sync)

    async def get_prompt_optimize_public(self) -> dict[str, Any]:
        cfg = await asyncio.to_thread(self._get_prompt_optimize_sync)
        return self._public_prompt_optimize(cfg)

    async def update_prompt_optimize(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = await asyncio.to_thread(self._update_prompt_optimize_sync, data)
        return self._public_prompt_optimize(cfg)

    def _get_mod_name_suggest_sync(self) -> dict[str, Any]:
        cfg = read_json(self.mod_name_suggest_path, DEFAULT_MOD_NAME_SUGGEST) or DEFAULT_MOD_NAME_SUGGEST
        return {**DEFAULT_MOD_NAME_SUGGEST, **cfg}

    def _update_mod_name_suggest_sync(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = self._get_mod_name_suggest_sync()
        for key in (
            "enabled",
            "base_url",
            "api_key",
            "model",
            "system_prompt",
            "temperature",
        ):
            if key in data and data[key] is not None:
                if key == "api_key" and data[key] == "":
                    continue
                cfg[key] = data[key]
        try:
            cfg["temperature"] = float(cfg.get("temperature") or 0.3)
        except (TypeError, ValueError):
            cfg["temperature"] = 0.3
        cfg["updated_at"] = _utc_now()
        write_json(self.mod_name_suggest_path, cfg)
        self._sync_active_profile_from_runtime()
        return cfg

    def _public_mod_name_suggest(self, cfg: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "system_prompt": cfg.get("system_prompt", ""),
            "temperature": float(cfg.get("temperature") or 0.3),
            "api_key_masked": _mask_api_key(cfg.get("api_key") or ""),
            "configured": bool(cfg.get("api_key")),
            "updated_at": cfg.get("updated_at"),
        }

    async def get_mod_name_suggest(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_mod_name_suggest_sync)

    async def get_mod_name_suggest_public(self) -> dict[str, Any]:
        cfg = await asyncio.to_thread(self._get_mod_name_suggest_sync)
        return self._public_mod_name_suggest(cfg)

    async def update_mod_name_suggest(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = await asyncio.to_thread(self._update_mod_name_suggest_sync, data)
        return self._public_mod_name_suggest(cfg)

    def _get_plan_llm_sync(self) -> dict[str, Any]:
        cfg = read_json(self.plan_llm_path, DEFAULT_PLAN_LLM) or DEFAULT_PLAN_LLM
        return {**DEFAULT_PLAN_LLM, **cfg}

    def _update_plan_llm_sync(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = self._get_plan_llm_sync()
        for key in (
            "enabled",
            "base_url",
            "api_key",
            "model",
            "system_prompt",
            "finalize_system_prompt",
            "temperature",
        ):
            if key in data and data[key] is not None:
                if key == "api_key" and data[key] == "":
                    continue
                cfg[key] = data[key]
        try:
            cfg["temperature"] = float(cfg.get("temperature") or 0.4)
        except (TypeError, ValueError):
            cfg["temperature"] = 0.4
        cfg["updated_at"] = _utc_now()
        write_json(self.plan_llm_path, cfg)
        self._sync_active_profile_from_runtime()
        return cfg

    def _public_plan_llm(self, cfg: dict[str, Any]) -> dict[str, Any]:
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "base_url": cfg.get("base_url", ""),
            "model": cfg.get("model", ""),
            "system_prompt": cfg.get("system_prompt", ""),
            "finalize_system_prompt": cfg.get("finalize_system_prompt", ""),
            "temperature": float(cfg.get("temperature") or 0.4),
            "api_key_masked": _mask_api_key(cfg.get("api_key") or ""),
            "configured": bool(cfg.get("api_key")),
            "updated_at": cfg.get("updated_at"),
        }

    async def get_plan_llm(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_plan_llm_sync)

    async def get_plan_llm_public(self) -> dict[str, Any]:
        cfg = await asyncio.to_thread(self._get_plan_llm_sync)
        return self._public_plan_llm(cfg)

    async def update_plan_llm(self, data: dict[str, Any]) -> dict[str, Any]:
        cfg = await asyncio.to_thread(self._update_plan_llm_sync, data)
        return self._public_plan_llm(cfg)

    async def get_default_requirements(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_default_requirements_sync)

    async def update_default_requirements(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        return await asyncio.to_thread(self._update_default_requirements_sync, items)

    async def get_llm_profiles_public(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._get_profiles_public_sync)

    async def create_llm_profile(self, name: str) -> tuple[dict[str, Any], str]:
        return await asyncio.to_thread(self._create_profile_sync, name)

    async def rename_llm_profile(self, profile_id: str, name: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._rename_profile_sync, profile_id, name)

    async def delete_llm_profile(self, profile_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._delete_profile_sync, profile_id)

    async def activate_llm_profile(self, profile_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._activate_profile_sync, profile_id)

    async def resolve_llm_for_user(self, user_id: str) -> dict[str, Any] | None:
        from storage.user_store import user_store

        settings = await self.get_settings()
        if settings.get("shared_llm_enabled", True):
            llm = await self.get_shared_llm()
            if llm.get("api_key") or llm.get("base_url"):
                return llm
            return None
        llm = await user_store.get_llm_internal(user_id)
        if llm.get("api_key") or llm.get("base_url"):
            return llm
        return None


admin_store = AdminStore(ADMIN_DIR)
