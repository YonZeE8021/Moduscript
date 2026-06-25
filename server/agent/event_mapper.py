"""Map Claude Agent SDK messages to session snapshot fields."""

from __future__ import annotations

import json
from typing import Any


def extract_stream_text(event: Any) -> str | None:
    ev = event.event
    if ev.get("type") != "content_block_delta":
        return None
    delta = ev.get("delta") or {}
    if delta.get("type") == "text_delta":
        return delta.get("text") or ""
    return None


def extract_stream_thinking(event: Any) -> str | None:
    ev = event.event
    if ev.get("type") != "content_block_delta":
        return None
    delta = ev.get("delta") or {}
    if delta.get("type") == "thinking_delta":
        return delta.get("thinking") or ""
    return None


def _reset_stream_flags(stream_state: dict[str, Any]) -> None:
    stream_state.pop("text", None)
    stream_state.pop("thinking", None)


def _handle_compaction(
    stream_state: dict[str, Any],
    side_events: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> None:
    compacted = bool(stream_state.get("compacted"))
    stream_state.clear()
    stream_state["compacted"] = True
    meta = metadata or {}
    side_events.append(
        {
            "type": "compaction",
            "trigger": meta.get("trigger"),
            "pre_tokens": meta.get("pre_tokens"),
            "repeated": compacted,
        }
    )


def _tool_preview(name: str, tool_input: dict[str, Any]) -> str:
    if name in ("Read", "Write", "Edit"):
        path = tool_input.get("file_path") or tool_input.get("path") or ""
        content = tool_input.get("content") or tool_input.get("new_string") or ""
        if content:
            return str(content)[:500]
        return str(path)[:200]
    if name == "Bash":
        return str(tool_input.get("command") or "")[:300]
    try:
        return json.dumps(tool_input, ensure_ascii=False)[:400]
    except Exception:
        return str(tool_input)[:400]


def process_agent_message(
    message: Any,
    *,
    stream_state: dict[str, Any],
    turn: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Update turn dict in place; return (turn, side_events)."""
    from claude_agent_sdk import (
        AssistantMessage,
        ResultMessage,
        StreamEvent,
        SystemMessage,
        TextBlock,
        ThinkingBlock,
        ToolResultBlock,
        ToolUseBlock,
    )

    side_events: list[dict[str, Any]] = []

    if isinstance(message, StreamEvent):
        ev_type = (message.event or {}).get("type")
        if ev_type in ("content_block_stop", "message_stop"):
            _reset_stream_flags(stream_state)
        text = extract_stream_text(message)
        if text:
            stream_state["text"] = True
            turn["summary"] = (turn.get("summary") or "") + text
            side_events.append({"type": "delta", "field": "summary", "append": text})
        think = extract_stream_thinking(message)
        if think:
            stream_state["thinking"] = True
            thinking = turn.setdefault("thinking", [])
            if thinking:
                thinking[-1] = thinking[-1] + think
            else:
                thinking.append(think)
            side_events.append(
                {
                    "type": "delta",
                    "field": "thinking",
                    "append": think,
                    "index": max(len(thinking) - 1, 0),
                }
            )

    elif isinstance(message, SystemMessage):
        if message.subtype == "compact_boundary":
            meta = (message.data or {}).get("compact_metadata") or {}
            _handle_compaction(stream_state, side_events, meta)

    elif isinstance(message, AssistantMessage):
        if message.error:
            side_events.append({"type": "error", "message": str(message.error)})
        for block in message.content:
            if isinstance(block, TextBlock) and block.text:
                if not stream_state.get("text"):
                    turn["summary"] = (turn.get("summary") or "") + block.text
                stream_state["text"] = False
            elif isinstance(block, ThinkingBlock) and block.thinking:
                if not stream_state.get("thinking"):
                    thinking = turn.setdefault("thinking", [])
                    if thinking:
                        thinking[-1] = thinking[-1] + block.thinking
                    else:
                        thinking.append(block.thinking)
                stream_state["thinking"] = False
            elif isinstance(block, ToolUseBlock):
                preview = _tool_preview(block.name, block.input or {})
                turn.setdefault("tools", []).append(
                    {"name": f"{block.name} · {preview[:40]}", "preview": preview}
                )
                side_events.append({"type": "tool_use", "name": block.name, "tool": turn["tools"][-1]})
            elif isinstance(block, ToolResultBlock):
                preview = ""
                for item in block.content or []:
                    if isinstance(item, TextBlock):
                        preview += item.text
                if preview:
                    tool_entry = {
                        "name": "Result",
                        "preview": preview[:2000],
                    }
                    turn.setdefault("tools", []).append(tool_entry)
                    side_events.append({"type": "tool_result", "tool": tool_entry})

    elif isinstance(message, ResultMessage):
        result_event: dict[str, Any] = {
            "type": "result",
            "cost_usd": message.total_cost_usd,
            "duration_ms": message.duration_ms,
            "turns": message.num_turns,
            "num_turns": message.num_turns,
            "session_id": message.session_id,
            "subtype": message.subtype,
            "is_error": message.is_error,
        }
        side_events.append(result_event)
        if message.is_error or (message.subtype or "").startswith("error"):
            err_msg = message.result or (message.errors[0] if message.errors else message.subtype)
            side_events.append({"type": "error", "message": str(err_msg or "agent error")})

    return turn, side_events


def ask_user_to_pending_action(request_id: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    questions = tool_input.get("questions") or []
    if questions:
        return {
            "type": "ask_user",
            "request_id": request_id,
            "questions": questions,
        }
    return {
        "type": "choice",
        "request_id": request_id,
        "question": tool_input.get("question") or "请确认",
        "choices": [{"id": "yes", "label": "确认"}, {"id": "no", "label": "取消"}],
        "selected": None,
    }


def normalize_ask_user_answers(original: dict[str, Any], submitted: dict[str, Any] | None) -> dict[str, Any]:
    questions = list((original.get("questions") or []) or [])
    if submitted and submitted.get("questions"):
        questions = list(submitted["questions"])

    def _normalize_answer_value(val: Any) -> Any | None:
        if val is None or val == "":
            return None
        if isinstance(val, list):
            cleaned = [str(v).strip() for v in val if v not in (None, "")]
            cleaned = [v for v in cleaned if v]
            return cleaned if cleaned else None
        if isinstance(val, str):
            stripped = val.strip()
            return stripped if stripped else None
        return val

    answers: dict[str, Any] = {}
    raw = (submitted or {}).get("answers")

    if isinstance(raw, dict):
        for key, val in raw.items():
            qkey = str(key).strip()
            norm = _normalize_answer_value(val)
            if qkey and norm is not None:
                answers[qkey] = norm
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                qtext = (item.get("question") or "").strip()
                label = (item.get("label") or item.get("answer") or "").strip()
                if qtext and label:
                    answers[qtext] = label

    return {"questions": questions, "answers": answers}
