"""Reference mod materialization for build sessions."""

from __future__ import annotations

import asyncio
import logging
import shutil
from copy import deepcopy
from typing import Any, Awaitable, Callable

from config import WORKSPACE_ROOT
from plan.reference_config import MATERIALIZE_TIMEOUT_SEC, MAX_CODE_REFS
from plan.reference_index import materialize_reference, ref_project_dir

logger = logging.getLogger(__name__)

RefStepCallback = Callable[[dict[str, Any]], Awaitable[None]]


def code_refs_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    ref = payload.get("reference_mods") or {}
    manual = ref.get("manual") or []
    out: list[dict[str, Any]] = []
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


def session_context_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "minecraft_version": payload.get("minecraft_version") or "1.20.1",
        "mod_loader": payload.get("mod_loader") or "fabric",
        "platform": payload.get("platform") or "unspecified",
    }


def copy_session_refs_to_workspace(
    user_id: str,
    session_id: str,
    reference_index: dict[str, Any],
) -> dict[str, str]:
    paths: dict[str, str] = {}
    workspace = WORKSPACE_ROOT / user_id / session_id / "references"
    for pid, meta in reference_index.items():
        if not isinstance(meta, dict) or meta.get("status") != "ready":
            continue
        slug = meta.get("slug") or pid
        src = ref_project_dir(user_id, session_id, pid, scope="session") / "repo"
        if not src.is_dir():
            continue
        dest = workspace / slug
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        paths[pid] = f"references/{slug}"
    return paths


def copy_from_plan_refs(
    user_id: str,
    plan_id: str,
    session_id: str,
    reference_index: dict[str, Any] | None,
) -> dict[str, str]:
    paths: dict[str, str] = {}
    index = reference_index or {}
    workspace = WORKSPACE_ROOT / user_id / session_id / "references"
    for pid, meta in index.items():
        if not isinstance(meta, dict) or meta.get("status") != "ready":
            continue
        slug = meta.get("slug") or pid
        src = ref_project_dir(user_id, plan_id, pid, scope="plan") / "repo"
        if not src.is_dir():
            continue
        dest = workspace / slug
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        paths[pid] = f"references/{slug}"
    return paths


def build_reference_prompt_append(
    reference_index: dict[str, Any],
    ref_paths: dict[str, str],
) -> str:
    if not reference_index and not ref_paths:
        return ""

    lines = ["<reference_workspace>"]
    for pid, path in ref_paths.items():
        meta = reference_index.get(pid) or {}
        title = meta.get("title") or pid
        lines.append(f"- **{title}**: `{path}/`")

    ready = [m for m in reference_index.values() if isinstance(m, dict) and m.get("status") == "ready"]
    failed = [m for m in reference_index.values() if isinstance(m, dict) and m.get("status") == "failed"]
    kinds = {m.get("source_kind") for m in ready}

    if ref_paths:
        lines.append("参考源码已 materialize 到工作区 references/ 目录，编写时请优先阅读上述路径。")
    if "decompiled_obfuscated" in kinds:
        lines.append("部分参考为反编译混淆代码（Yarn 重映射失败），阅读时需结合命名与上下文推断。")
    if "metadata_only" in kinds:
        lines.append("部分闭源参考仅提供元数据（反编译失败），无 references/ 源码目录。")
    for m in failed:
        title = m.get("title") or m.get("project_id") or "未知"
        err = m.get("error") or "索引失败"
        lines.append(f"- 参考「{title}」索引失败：{err}；请自行搜索或跳过。")
    if not ref_paths and not failed:
        lines.append("参考上下文见上方 Reference Card 与 research findings。")
    lines.append("</reference_workspace>")
    return "\n".join(lines)


async def materialize_one_session_ref(
    user_id: str,
    session_id: str,
    ref_item: dict[str, Any],
    reference_index: dict[str, Any],
    *,
    session_context: dict[str, Any],
    on_step: RefStepCallback | None = None,
) -> dict[str, Any]:
    pid = ref_item.get("project_id") or ref_item.get("slug") or "unknown"
    reference_index[pid] = {
        "status": "indexing",
        "title": ref_item.get("title") or pid,
        "slug": ref_item.get("slug") or pid,
        "source_url": ref_item.get("source_url") or "",
        "project_id": pid,
    }

    try:
        meta = await asyncio.wait_for(
            materialize_reference(
                user_id,
                session_id,
                ref_item,
                scope="session",
                plan_context=session_context,
                on_step=on_step,
            ),
            timeout=MATERIALIZE_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        logger.warning("session materialize timed out session=%s pid=%s", session_id, pid)
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
        logger.exception("session materialize failed session=%s pid=%s", session_id, pid)
        meta = {
            "project_id": pid,
            "status": "failed",
            "source_url": ref_item.get("source_url") or "",
            "error": str(exc)[:300],
            "title": ref_item.get("title") or pid,
            "slug": ref_item.get("slug") or pid,
            "degrade_reason": "decompile_failed",
        }

    reference_index[pid] = meta
    return meta


async def materialize_all_session_refs(
    user_id: str,
    session_id: str,
    refs: list[dict[str, Any]],
    reference_index: dict[str, Any],
    *,
    session_context: dict[str, Any],
    on_step: RefStepCallback | None = None,
) -> None:
    for ref_item in refs:
        await materialize_one_session_ref(
            user_id,
            session_id,
            ref_item,
            reference_index,
            session_context=session_context,
            on_step=on_step,
        )


def init_reference_index_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("reference_index")
    if isinstance(raw, dict):
        return deepcopy(raw)
    return {}
