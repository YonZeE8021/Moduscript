"""ClaudeSDKClient wrapper for a single mod session."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from agent.event_mapper import ask_user_to_pending_action, normalize_ask_user_answers
from agent.options import AUTO_ALLOW_TOOLS, build_agent_options
from storage.admin_store import admin_store

logger = logging.getLogger(__name__)


class AgentSession:
    def __init__(
        self,
        session_id: str,
        cwd: str,
        owner_id: str,
        mode: str = "build",
        on_permission_request: Any = None,
        max_turns: int | None = None,
        cli_resume_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.cwd = cwd
        self.owner_id = owner_id
        self.mode = mode
        self.max_turns = max_turns
        self.cli_resume_id = (cli_resume_id or "").strip() or None
        self._client_cm: Any = None
        self.client: Any = None
        self.lock = asyncio.Lock()
        self.busy = False
        self.has_conversation = False
        self.permission_futures: dict[str, asyncio.Future[Any]] = {}
        self._uses_permissions = False
        self.on_permission_request = on_permission_request

    def _make_can_use_tool(self) -> Any:
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny, ToolPermissionContext

        async def can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            ctx: ToolPermissionContext,
        ) -> Any:
            if tool_name in AUTO_ALLOW_TOOLS:
                return PermissionResultAllow()

            if tool_name == "AskUserQuestion":
                req_id = str(uuid.uuid4())
                loop = asyncio.get_running_loop()
                fut: asyncio.Future[Any] = loop.create_future()
                self.permission_futures[req_id] = fut
                pending = ask_user_to_pending_action(req_id, tool_input)
                pending["_tool_input"] = tool_input
                if self.on_permission_request:
                    await self.on_permission_request(req_id, pending)
                try:
                    result = await asyncio.wait_for(fut, timeout=3600.0)
                    if isinstance(result, PermissionResultDeny):
                        return result
                    if isinstance(result, dict):
                        updated = normalize_ask_user_answers(tool_input, result)
                        return PermissionResultAllow(updated_input=updated)
                    return PermissionResultAllow()
                except TimeoutError:
                    return PermissionResultDeny(message="等待用户回答超时")
                finally:
                    self.permission_futures.pop(req_id, None)

            return PermissionResultAllow()

        return can_use_tool

    async def _resolve_llm_env(self) -> dict[str, str]:
        llm = await admin_store.resolve_llm_for_user(self.owner_id)
        if not llm:
            raise RuntimeError("未配置 LLM，请在设置或管理后台添加 API")
        return admin_store.build_env(llm)

    async def connect(self, *, with_permissions: bool = True) -> Any:
        from claude_agent_sdk import ClaudeSDKClient

        async with self.lock:
            if self.client is not None and self._uses_permissions == with_permissions:
                return self.client

            if self._client_cm is not None:
                await self.close()

            llm_env = await self._resolve_llm_env()
            can_use_tool = self._make_can_use_tool() if with_permissions else None
            options = build_agent_options(
                self.cwd,
                llm_env,
                mode=self.mode,
                continue_conversation=self.has_conversation,
                resume=self.cli_resume_id,
                can_use_tool=can_use_tool,
                max_turns=self.max_turns,
            )
            self._client_cm = ClaudeSDKClient(options=options)
            self.client = await self._client_cm.__aenter__()
            self._uses_permissions = with_permissions
            return self.client

    async def close(self) -> None:
        if self._client_cm is not None:
            await self._client_cm.__aexit__(None, None, None)
            self._client_cm = None
            self.client = None

    async def send_prompt(self, prompt: str) -> Any:
        client = await self.connect(with_permissions=True)
        self.busy = True
        try:
            await client.query(prompt)
            self.has_conversation = True
            async for message in client.receive_response():
                yield message
        finally:
            self.busy = False

    async def send_follow_up(self, content: str) -> Any:
        async for message in self.send_prompt(content):
            yield message

    async def interrupt(self) -> bool:
        if self.client is None:
            return False
        try:
            await self.client.interrupt()
            return True
        except Exception as exc:
            logger.warning("interrupt failed: %s", exc)
            return False

    def resolve_permission(self, request_id: str, result: Any) -> bool:
        fut = self.permission_futures.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(result)
        return True
