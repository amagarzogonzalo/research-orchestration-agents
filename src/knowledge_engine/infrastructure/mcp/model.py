"""ProviderChatModel — a LangChain BaseChatModel bridging our ProviderPort.

Lets :func:`langchain.agents.create_agent` run on the zero-cost
:class:`~knowledge_engine.infrastructure.router.FallbackRouter` instead of a
vendor SDK: LangChain messages are mapped to our ``ChatRequest``, we call the
injected ``ProviderPort`` (rate-limited + circuit-broken + affinity-routed),
and map the result back. ``bind_tools`` caches schemas so MCP tools bind.

Kept intentionally thin; the port stays the single transport boundary.
"""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from knowledge_engine.core.ports import ProviderPort
from knowledge_engine.schemas.provider import Affinity, ChatMessage, ChatRequest, Role


class ProviderChatModel(BaseChatModel):
    """Bridge our zero-cost provider router into LangChain's ChatModel API."""

    provider: ProviderPort
    affinity: str = "macro"
    max_tokens: int = 1500
    _bound_tools: list[dict] = []

    @property
    def _llm_type(self) -> str:
        return "knowledge-engine-provider"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        self._bound_tools = [_tool_schema(t) for t in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return asyncio.run(self._agenerate_impl(messages))

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return await self._agenerate_impl(messages)

    async def _agenerate_impl(self, messages: list[BaseMessage]) -> ChatResult:
        affinity = Affinity[self.affinity.upper()] if self.affinity.upper() in Affinity.__members__ else Affinity.MACRO
        req = ChatRequest(
            messages=[_to_chat_message(m) for m in messages],
            affinity=affinity,
            max_tokens=self.max_tokens,
            tools=self._bound_tools or None,
        )
        res = await self.provider.complete(req)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=res.message.content))])


def _to_chat_message(m) -> ChatMessage:  # noqa: ANN001 - BaseMessage or dict (test doubles)
    if isinstance(m, dict):
        role = Role[m.get("role", "user").upper()] if m.get("role", "user").upper() in Role.__members__ else Role.user
        return ChatMessage(role=role, content=m.get("content", "") or "")
    role = {
        HumanMessage: Role.user,
        SystemMessage: Role.system,
        AIMessage: Role.assistant,
    }.get(type(m), Role.user)
    return ChatMessage(role=role, content=m.content or "")


def _tool_schema(tool: Any) -> dict[str, Any]:
    try:
        args = tool.args_schema.model_json_schema() if getattr(tool, "args_schema", None) else {"type": "object"}
    except Exception:  # noqa: BLE001 - schemaless tools degrade to free-form args
        args = {"type": "object"}
    func = {
        "name": tool.name,
        "description": tool.description or "",
        "parameters": args,
    }
    return {"type": "function", "function": func}


__all__ = ["ProviderChatModel"]
