"""System prompts for plan mode LLM."""

from __future__ import annotations

import json
import re
from typing import Any

TURN_OUTPUT_SCHEMA = {
    "assistant_message": "string — 本轮说明，结合用户知识水平调整措辞",
    "difficulty": {"level": "low|medium|high|extreme", "reason": "string"},
    "readiness_hint": {"sufficient": "boolean", "message": "string"},
    "open_questions": [
        "string — 不宜选项化的开放式追问，0–3 条；用户将在「自由回复」中作答"
    ],
    "questions": [
        {
            "id": "string",
            "prompt": "string",
            "why": "string",
            "multi_select": "boolean",
            "options": [
                {
                    "id": "string",
                    "label": "string",
                    "hint": "string",
                    "complexity": "low|medium|high — 实现复杂度，帮助用户控制 scope",
                }
            ],
            "allow_custom": "boolean",
            "custom_placeholder": "string",
        }
    ],
    "blueprint_tree": [
        {
            "id": "string",
            "title": "string",
            "status": "draft|open|done",
            "summary": "string",
            "children": [],
        }
    ],
    "source_lookups": [
        {"project_id": "string", "query": "string", "reason": "string"}
    ],
}

L1_LABELS = {
    "programming": ["完全不了解", "理解核心概念，并能动手写一点", "有较深基础或工程经验"],
    "ai_literacy": ["仅必要时使用", "日常会使用", "深度依赖，了解 LLM 通识"],
    "general_tech": ["会装整合包、加 mod 游玩", "能折腾整合包与联机环境", "熟悉专用服务端并可排查错误"],
    "mc_mechanics": ["配方与流程仍不熟", "懂基础运作与常见机制", "机制向较深入"],
}


def build_turn_system_prompt(config_prompt: str | None = None) -> str:
    base = config_prompt or (
        "你是 Minecraft 模组/插件规划助手。通过多轮结构化问答帮用户细化 Mod 需求与技术方案。"
        "每轮输出必须是合法 JSON，严格遵循给定 schema。"
    )
    return (
        f"{base}\n\n"
        "## 出题策略\n"
        "- 每轮 1–5 题，优先能显著改变实现路径的歧义点\n"
        "- 每个选项必须标注 complexity（low/medium/high），反映实现工作量与 scope 膨胀风险\n"
        "- 根据 difficulty 决定追问深度\n"
        "- 根据 knowledge_l1 调整术语：L0 用类比，L2 可直接谈 tick/NBT/注册\n"
        "- readiness_hint.sufficient=true 表示关键决策已足够定稿，用户可结束规划，但仍可继续追问细节\n"
        "- 无更多高价值问题时 questions 可为空数组\n"
        "- assistant_message：本轮思路、背景、对已答内容的解读，不写具体追问\n"
        "- questions：能用选项显著改变实现路径的歧义点（主路径）\n"
        "- open_questions：仅当开放描述比选项更准确时使用（如「用一句话描述期望难度曲线」），0–3 条，可为空；"
        "用户将在「自由回复」中作答，勿与 assistant_message 或 questions 重复同一语义\n"
        "- 不得重复已回答或历史轮次已明确的问题；若信息已足够应减少 questions 或留空\n"
        "- 参考源码已通过工具预读注入上下文，无需再填 source_lookups（该字段已废弃，服务端忽略）\n"
        "- blueprint_tree 输出 2–3 层嵌套结构（概述 → 功能模块 → 子项/风险），children 可递归\n\n"
        f"## 输出 JSON Schema\n{json.dumps(TURN_OUTPUT_SCHEMA, ensure_ascii=False, indent=2)}"
    )


def build_finalize_system_prompt(config_prompt: str | None = None) -> str:
    base = config_prompt or "你是 Minecraft 模组规划助手，负责将多轮问答结果整理为可执行的 Markdown 规划文档。"
    return (
        f"{base}\n\n"
        "输出完整 Markdown，包含：\n"
        "1. 项目概述\n"
        "2. 功能范围\n"
        "3. 技术方案（加载器、侧、核心机制）\n"
        "4. 约束与配置项\n"
        "5. 风险与待定项\n"
        "6. 验收标准\n"
        "只输出 Markdown，不要 JSON 包裹。"
    )


def format_knowledge_l1(l1: dict[str, Any] | None) -> str:
    if not l1:
        return "未知（按中等水平解释）"
    lines = []
    for key, labels in L1_LABELS.items():
        level = int(l1.get(key, 1))
        level = max(0, min(2, level))
        lines.append(f"- {key}: L{level} — {labels[level]}")
    return "\n".join(lines)


def format_requirements_block(context: dict[str, Any]) -> str:
    reqs = context.get("requirements") or []
    if not reqs:
        return ""
    detail = context.get("requirements_detail") or {}
    lines = ["## 已选其他要求"]
    for req in reqs:
        if not isinstance(req, dict):
            continue
        if req.get("enabled") is False:
            continue
        rid = req.get("id") or ""
        title = req.get("title") or rid
        lines.append(f"- **{title}**")
        extra = detail.get(rid) if isinstance(detail, dict) else None
        if isinstance(extra, dict) and extra.get("custom_prompt"):
            lines.append(f"  - 补充: {extra['custom_prompt']}")
        elif req.get("description"):
            lines.append(f"  - {req['description']}")
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


INTERRUPTION_PROMPT_TEXT: dict[int, str] = {
    0: "## Agent 沟通策略\n严格禁止向用户提问或请求确认，所有问题全部自行推断。",
    1: "## Agent 沟通策略\n仅在遇到无法继续的严重阻塞时才向用户提问；能自行合理推断的决策不要打扰用户。",
    3: "## Agent 沟通策略\n开发过程中尽可能多主动询问澄清需求、设计取舍与实现细节。",
}

REFERENCE_HEADING_BY_LOADER: dict[str, str] = {
    "datapack": "## 参考数据包",
}

REFERENCE_CODE_SECTION_NOTE_BUILD = (
    "编写 Agent 须优先阅读工作区 references/ 下已 materialize 的源码；"
    "仅当某参考索引失败时再自行搜索。"
)

MODRINTH_AUTO_SEARCH_LINE = "## 你需要使用 Modrinth API 自行搜索相关 mod/插件并参考"

GITHUB_REPO_RE = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", re.IGNORECASE)


def _parse_github_repo_url(url: str) -> tuple[str, str] | None:
    u = url.strip().rstrip("/")
    match = GITHUB_REPO_RE.match(u)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    return owner, repo


def _is_direct_github_ref(m: dict[str, Any]) -> bool:
    if not _parse_github_repo_url(str(m.get("source_url") or "")):
        return False
    link = str(m.get("url") or m.get("permalink") or "").strip()
    if not link:
        return False
    return _parse_github_repo_url(link) is not None


def _requirement_body(req: dict[str, Any], detail: dict[str, Any]) -> str:
    rid = req.get("id") or ""
    extra = detail.get(rid) if isinstance(detail, dict) else None
    if isinstance(extra, dict) and (extra.get("custom_prompt") or "").strip():
        return str(extra["custom_prompt"]).strip()
    return (req.get("description") or "").strip()


def _reference_heading(mod_loader: str) -> str:
    if mod_loader == "datapack":
        return REFERENCE_HEADING_BY_LOADER["datapack"]
    plugin_loaders = {"paper", "purpur", "spigot", "bukkit", "folia"}
    if mod_loader in plugin_loaders:
        return "## 参考插件"
    return "## 参考模组"


def _format_handoff_reference_code_item(mod: dict[str, Any]) -> str:
    title = mod.get("title") or mod.get("slug") or mod.get("project_id") or "unknown"
    permalink = mod.get("url") or mod.get("permalink") or ""
    slug = mod.get("slug") or mod.get("project_id") or "unknown"
    lines = [f"- **{title}**", f"  - 链接：{permalink}"]
    if mod.get("source_url"):
        lines.append(f"  - 开源：{mod['source_url']}")
        lines.append(f"  - 获取方式：会话启动时由服务端克隆开源仓库到工作区 references/{slug}/")
    elif mod.get("decompile_attempt"):
        lines.append(f"  - 获取方式：会话启动时由服务端下载/反编译到工作区 references/{slug}/")
    else:
        lines.append(f"  - 获取方式：会话启动时由服务端下载/反编译到工作区 references/{slug}/")
    if mod.get("description"):
        lines.append(f"  - 说明：{mod['description']}")
    if mod.get("note"):
        lines.append(f"  - 备注：{mod['note']}")
    return "\n".join(lines)


def _format_handoff_reference_feature_item(mod: dict[str, Any]) -> str:
    title = mod.get("title") or mod.get("slug") or mod.get("project_id") or "unknown"
    permalink = mod.get("url") or mod.get("permalink") or ""
    lines = [f"- **{title}**", f"  - 链接：{permalink}"]
    if mod.get("description"):
        lines.append(f"  - 说明：{mod['description']}")
    if mod.get("note"):
        lines.append(f"  - 备注：{mod['note']}")
    return "\n".join(lines)


def build_handoff_appendix_fallback(context: dict[str, Any]) -> str:
    """Approximate concept-page appendix for plans created before handoff_appendix."""
    lines: list[str] = []
    level = context.get("interruption_level")
    if isinstance(level, int) and level in INTERRUPTION_PROMPT_TEXT:
        lines.extend([INTERRUPTION_PROMPT_TEXT[level], ""])

    reqs = context.get("requirements") or []
    detail = context.get("requirements_detail") or {}
    active_reqs = [r for r in reqs if isinstance(r, dict) and r.get("enabled") is not False]
    if active_reqs:
        lines.append("## 其他要求")
        for req in active_reqs:
            title = req.get("title") or req.get("id") or ""
            lines.append(f"### {title}")
            body = _requirement_body(req, detail)
            if body:
                lines.append(body)
            lines.append("")

    ref = context.get("reference_mods") or {}
    manual = [m for m in (ref.get("manual") or []) if isinstance(m, dict)]
    code_refs = [m for m in manual if m.get("reference_type") == "code" and m.get("include_in_prompt") is not False]
    feature_refs = [m for m in manual if m.get("reference_type") != "code"]
    if code_refs or feature_refs:
        lines.append(_reference_heading(context.get("mod_loader") or "fabric"))
        if code_refs:
            lines.append("### 参考代码")
            for m in code_refs:
                lines.append(_format_handoff_reference_code_item(m))
            lines.append(REFERENCE_CODE_SECTION_NOTE_BUILD)
        if feature_refs:
            lines.append("### 参考功能")
            for m in feature_refs:
                lines.append(_format_handoff_reference_feature_item(m))
        lines.append("")

    if ref.get("auto_search"):
        lines.append(MODRINTH_AUTO_SEARCH_LINE)

    return "\n".join(lines).strip()


def format_reference_mods_block(context: dict[str, Any]) -> str:
    ref = context.get("reference_mods") or {}
    manual = ref.get("manual") or []
    if not manual and not ref.get("auto_search"):
        return ""
    lines = ["## 参考模组"]
    has_modrinth_code_in_prompt = False
    for m in manual:
        if not isinstance(m, dict):
            continue
        title = m.get("title") or m.get("slug") or m.get("project_id")
        rtype = m.get("reference_type") or "feature"
        lines.append(f"- **{title}** ({rtype})")
        if m.get("source_url"):
            lines.append(f"  - 开源: {m['source_url']}")
        if rtype == "code" and m.get("include_in_prompt") is not False:
            if not _is_direct_github_ref(m):
                lines.append(
                    "  - 获取方式: 请通过 Modrinth API 获取该模组的开源代码并下载后参考"
                )
                has_modrinth_code_in_prompt = True
        if m.get("description"):
            lines.append(f"  - 说明: {m['description']}")
        if m.get("note"):
            lines.append(f"  - 备注: {m['note']}")
        if m.get("include_in_prompt") is False:
            lines.append("  - （未纳入 prompt：缺少可用源码）")
    if has_modrinth_code_in_prompt:
        lines.append(
            "编写 Agent 须通过 Modrinth API 获取上述参考模组开源代码并下载后参考实现。"
        )
    if ref.get("auto_search"):
        lines.append("- 启用 Modrinth 自动搜索相关 mod")
    return "\n".join(lines) + "\n"


def format_reference_cards_block(plan: dict[str, Any] | None) -> str:
    if not plan:
        return ""
    cards = plan.get("reference_cards") or {}
    index = plan.get("reference_index") or {}
    if not cards:
        return ""
    lines = ["## 参考模组架构摘要 (Reference Card)"]
    for pid, card in cards.items():
        meta = index.get(pid) or {}
        if meta.get("status") != "ready":
            continue
        title = meta.get("title") or pid
        lines.append(f"### {title}")
        lines.append(str(card).strip())
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def format_research_findings_block(plan: dict[str, Any] | None) -> str:
    if not plan:
        return ""
    findings = plan.get("research_findings") or []
    if not findings:
        return ""
    lines = ["## 已检索参考片段摘要"]
    for f in findings[-5:]:
        preview = (f.get("snippet_preview") or f.get("query") or "")[:200]
        paths = f.get("paths") or []
        path_hint = paths[0].get("path") if paths else ""
        lines.append(f"- [{f.get('project_id')}] {f.get('query', '')}: {path_hint} — {preview}")
    return "\n".join(lines) + "\n"


def _label_for_answer(turn: dict[str, Any], qid: str, opt_id: str) -> str:
    for q in turn.get("questions") or []:
        if q.get("id") != qid:
            continue
        for opt in q.get("options") or []:
            if opt.get("id") == opt_id:
                return opt.get("label") or opt_id
    return opt_id


def format_user_reply_readable(turn: dict[str, Any], reply: dict[str, Any]) -> str:
    parts = []
    for qid, val in (reply.get("answers") or {}).items():
        q_prompt = next((q.get("prompt") for q in (turn.get("questions") or []) if q.get("id") == qid), qid)
        ids = val if isinstance(val, list) else [val]
        labels = [_label_for_answer(turn, qid, str(i)) for i in ids]
        parts.append(f"Q「{q_prompt}」→ {', '.join(labels)}")
    for text in (reply.get("custom") or {}).values():
        if text:
            parts.append(f"自定义: {text}")
    if reply.get("overall_remarks"):
        parts.append(f"自由回复: {reply['overall_remarks']}")
    return "; ".join(parts) if parts else json.dumps(reply, ensure_ascii=False)


def build_turn_user_message(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    user_reply: dict[str, Any] | None = None,
    is_first: bool = False,
    plan: dict[str, Any] | None = None,
    read_context: str | None = None,
) -> str:
    ctx = context
    parts = [
        f"## 项目约束\n"
        f"- MC 版本: {ctx.get('minecraft_version')}\n"
        f"- 加载器: {ctx.get('mod_loader')}\n"
        f"- 部署: {ctx.get('platform')}\n"
        f"- 用户构想: {ctx.get('user_concept')}\n",
        f"## 用户知识水平 (L1)\n{format_knowledge_l1(ctx.get('knowledge_l1'))}\n",
    ]
    req_block = format_requirements_block(ctx)
    if req_block:
        parts.append(req_block)
    ref_block = format_reference_mods_block(ctx)
    if ref_block:
        parts.append(ref_block)
    cards_block = format_reference_cards_block(plan)
    if cards_block:
        parts.append(cards_block)
    findings_block = format_research_findings_block(plan)
    if findings_block:
        parts.append(findings_block)
    if read_context and read_context.strip():
        parts.append(f"## 本轮参考源码阅读笔记\n{read_context.strip()}\n")

    if turns:
        parts.append("## 历史轮次摘要\n")
        for i, turn in enumerate(turns[-6:], 1):
            msg = turn.get("assistant_message", "")
            q_lines = []
            for q in turn.get("questions") or []:
                q_lines.append(f"  - 问过: {q.get('prompt', q.get('id', ''))}")
            open_q_lines = []
            for oq in turn.get("open_questions") or []:
                text = str(oq).strip()
                if text:
                    open_q_lines.append(f"  - 开放问: {text}")
            parts.append(f"### 轮次 {i}\n助手: {msg}\n")
            if q_lines:
                parts.append("\n".join(q_lines) + "\n")
            if open_q_lines:
                parts.append("\n".join(open_q_lines) + "\n")
            reply = turn.get("user_reply")
            if reply:
                parts.append(f"用户回答: {format_user_reply_readable(turn, reply)}\n")

    if is_first:
        parts.append("请评估难度并生成首轮高价值问题。")
    elif user_reply:
        parts.append(f"## 本轮用户回答\n{json.dumps(user_reply, ensure_ascii=False)}\n")
        parts.append("请根据回答更新方案树，并生成下一轮问题（若无高价值问题则 questions 为空）。")
    else:
        parts.append("请继续规划。")

    return "\n".join(parts)


def build_finalize_user_message(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    blueprint_tree: list,
    plan: dict[str, Any] | None = None,
    read_context: str | None = None,
) -> str:
    extra = ""
    if plan:
        extra = format_reference_cards_block(plan) + format_research_findings_block(plan)
    if read_context and read_context.strip():
        extra += f"\n## 定稿前参考源码阅读笔记\n{read_context.strip()}\n"
    return (
        f"## 项目约束\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"{extra}"
        f"## 方案树\n{json.dumps(blueprint_tree, ensure_ascii=False, indent=2)}\n\n"
        f"## 全部对话轮次\n{json.dumps(turns, ensure_ascii=False, indent=2)}\n\n"
        "请生成最终 Markdown 规划文档，包含「参考实现」章节（若有 Reference Card 或检索片段）。"
    )


QUESTION_OPTIONS_SCHEMA = {
    "options": [
        {"id": "string", "label": "string", "hint": "string", "complexity": "low|medium|high"}
    ]
}

TURN_REGEN_SCHEMA = TURN_OUTPUT_SCHEMA


def build_regenerate_turn_user_message(
    *,
    context: dict[str, Any],
    turns: list[dict[str, Any]],
    instruction: str | None = None,
    plan: dict[str, Any] | None = None,
    baseline_tree: list[dict[str, Any]] | None = None,
) -> str:
    prior = turns[:-1] if turns else []
    current = turns[-1] if turns else None
    parts = [
        build_turn_user_message(
            context=context,
            turns=prior,
            is_first=len(prior) == 0,
            plan=plan,
        ),
        "\n## 任务\n请重新生成本轮结构化问题（完整 turn JSON），与当前主题一致但换一组更有价值的问题。",
        "\n本轮为**替换**尚未提交的当前轮次；blueprint_tree 仅基于下方基线与已提交历史更新，"
        "勿保留本轮已废弃草稿中的节点。",
    ]
    tree = baseline_tree if baseline_tree is not None else (plan or {}).get("blueprint_tree") or []
    if tree:
        parts.append(f"\n## 方案树基线\n{json.dumps(tree, ensure_ascii=False, indent=2)}")
    if current:
        parts.append(
            f"\n## 当前轮（待替换）\n"
            f"说明: {current.get('assistant_message', '')}\n"
            f"问题: {json.dumps(current.get('questions') or [], ensure_ascii=False, indent=2)}"
        )
    if instruction:
        parts.append(f"\n用户补充要求：{instruction}")
    return "\n".join(parts)


def build_regenerate_question_user_message(
    *,
    context: dict[str, Any],
    turn: dict[str, Any],
    question: dict[str, Any],
    action: str,
) -> str:
    action_desc = "在保留现有选项基础上追加新选项（不重复 id）" if action == "expand" else "完全替换为新的一组选项"
    return (
        f"## 项目约束\n{json.dumps(context, ensure_ascii=False, indent=2)}\n\n"
        f"## 当前轮次说明\n{turn.get('assistant_message', '')}\n\n"
        f"## 目标问题\n{json.dumps(question, ensure_ascii=False, indent=2)}\n\n"
        f"## 任务\n请{action_desc}。只输出 JSON：{{\"options\": [...]}}，每个选项含 id/label/hint/complexity。"
    )
