"""Build ClaudeAgentOptions for mod authoring sessions."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from config import CHAT_EFFORT_DEFAULT, CHAT_MAX_TURNS, CHAT_THINKING_DEFAULT

logger = logging.getLogger(__name__)

_cli_stderr_lines: list[str] = []
_RESOLVED_CLI_PATH: str | None = None

AUTO_ALLOW_TOOLS = frozenset(
    {
        "EnterPlanMode",
        "ExitPlanMode",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "TaskOutput",
    }
)


def _cli_stderr_handler(line: str) -> None:
    line = line.rstrip()
    if not line:
        return
    _cli_stderr_lines.append(line)
    if len(_cli_stderr_lines) > 80:
        del _cli_stderr_lines[:-80]
    logger.warning("Claude CLI: %s", line)


def resolve_cli_path() -> str:
    global _RESOLVED_CLI_PATH
    if _RESOLVED_CLI_PATH:
        return _RESOLVED_CLI_PATH

    env_path = (os.getenv("CLAUDE_CLI_PATH") or "").strip()
    if env_path:
        p = Path(env_path).expanduser().resolve()
        if p.is_file():
            _RESOLVED_CLI_PATH = str(p)
            return _RESOLVED_CLI_PATH
        raise FileNotFoundError(f"CLAUDE_CLI_PATH 不存在: {p}")

    import claude_agent_sdk as _sdk

    bundled = Path(_sdk.__file__).resolve().parent / "_bundled" / (
        "claude.exe" if os.name == "nt" else "claude"
    )
    if bundled.is_file():
        _RESOLVED_CLI_PATH = str(bundled)
        return _RESOLVED_CLI_PATH

    found = shutil.which("claude")
    if found:
        found_path = Path(found)
        if found_path.suffix.lower() in {".cmd", ".bat"}:
            npm_exe = (
                found_path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
            )
            if npm_exe.is_file():
                _RESOLVED_CLI_PATH = str(npm_exe)
                return _RESOLVED_CLI_PATH
        _RESOLVED_CLI_PATH = found
        return _RESOLVED_CLI_PATH

    raise FileNotFoundError(
        "未找到 Claude Code CLI。请安装 claude-agent-sdk 或设置 CLAUDE_CLI_PATH"
    )


def verify_cli_at_startup() -> tuple[bool, str, str]:
    try:
        cli = resolve_cli_path()
        result = subprocess.run(
            [cli, "-v"],
            capture_output=True,
            text=True,
            timeout=30,
            env=os.environ.copy(),
        )
        version = (result.stdout or result.stderr or "").strip()
        if result.returncode != 0:
            return False, cli, version or f"exit code {result.returncode}"
        return True, cli, version
    except Exception as exc:
        return False, "", str(exc)


async def verify_cli_async_transport() -> tuple[bool, str]:
    """Verify asyncio can spawn CLI subprocess (Windows Proactor requirement)."""
    if sys.platform != "win32":
        return True, ""

    try:
        cli = resolve_cli_path()
        proc = await asyncio.create_subprocess_exec(
            cli,
            "-v",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        return True, ""
    except NotImplementedError:
        return (
            False,
            "asyncio 无法创建子进程；请通过 python main.py 启动（已启用 ProactorEventLoop）",
        )
    except Exception as exc:
        return False, str(exc)


def _parse_thinking_config(mode: str | None) -> dict[str, Any] | None:
    m = (mode or CHAT_THINKING_DEFAULT or "adaptive").strip().lower()
    if m in ("off", "false", "disabled", "none", "0"):
        return {"type": "disabled"}
    if m in ("on", "true", "enabled"):
        return {"type": "enabled", "budget_tokens": 8192}
    return {"type": "adaptive"}


def _parse_effort(level: str | None) -> str | None:
    v = (level or CHAT_EFFORT_DEFAULT or "").strip().lower()
    if v in ("low", "medium", "high", "xhigh", "max"):
        return v
    return CHAT_EFFORT_DEFAULT or None


def build_mod_system_prompt(mode: str) -> str:
    """Append-only Agent 沟通语言规则（见 build_agent_options 的 preset append）。

    TODO(i18n): 实现语言切换时需同步改此处与前端 js/prompt-builder.js 的
    <global_language>，并按会话 locale 动态生成 append 内容。
    详见 docs/AGENT_SDK.md「System Prompt 与语言」。
    """
    _ = mode
    return (
        "<system_prompt>\n"
        "  <communication>\n"
        "    <language>中文</language>\n"
        "    <rule>与用户沟通必须使用中文。</rule>\n"
        "  </communication>\n"
        "</system_prompt>"
    )


def build_agent_options(
    cwd: str,
    llm_env: dict[str, str],
    *,
    mode: str = "build",
    continue_conversation: bool = False,
    resume: str | None = None,
    can_use_tool: Any = None,
    max_turns: int | None = None,
) -> Any:
    from claude_agent_sdk import ClaudeAgentOptions

    permission_mode = "plan" if mode == "plan" else "acceptEdits"
    return ClaudeAgentOptions(
        cwd=cwd,
        cli_path=resolve_cli_path(),
        model=llm_env.get("ANTHROPIC_MODEL") or None,
        env=llm_env,
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": build_mod_system_prompt(mode),
        },
        continue_conversation=continue_conversation and not resume,
        resume=resume,
        permission_mode=permission_mode,
        max_turns=max_turns if max_turns is not None else CHAT_MAX_TURNS,
        thinking=_parse_thinking_config(None),
        effort=_parse_effort(None),
        include_partial_messages=True,
        can_use_tool=can_use_tool,
        stderr=_cli_stderr_handler,
    )


def is_under_workspace(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        pass
    if os.name == "nt":
        path_s = os.path.normcase(str(path))
        root_s = os.path.normcase(str(root))
        return path_s == root_s or path_s.startswith(root_s + os.sep)
    return False


def validate_workspace(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        resolved.mkdir(parents=True, exist_ok=True)
    if not is_under_workspace(resolved, root):
        raise ValueError(f"路径必须在 {root} 下")
    return resolved
