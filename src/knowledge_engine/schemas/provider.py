"""Provider transport + usage telemetry contracts (ADR 0002 §1–§2)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    """Message role.

    Attributes:
        system: System role.
        user: User role.
        assistant: Assistant role.
        tool: Tool role.
    """

    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ChatMessage(BaseModel):
    """ChatMessage.

    Attributes:
        model_config: Attribute description.
        role: Attribute description.
        content: Attribute description.
        name: Attribute description."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str
    name: str | None = Field(default=None)


class Affinity(StrEnum):
    """Task-class routing hint consumed by the FallbackRouter (ADR 0004).

    Attributes:
        MICRO: Attribute description.
        MACRO: Attribute description.
        MILLION: Attribute description.
        FALLBACK: Attribute description.
    """

    MICRO = "micro"  # cheap, ultra-low-latency: boolean filter, entity extract
    MACRO = "macro"  # high-context aggregation: mid-document synthesis
    MILLION = "million"  # massive token volume: bulk parse, corpora
    FALLBACK = "fallback"  # no-SLA catch-all before local Ollama


class ChatRequest(BaseModel):
    """ChatRequest.

    Attributes:
        model_config: Attribute description.
        messages: Attribute description.
        temperature: Attribute description.
        max_tokens: Attribute description.
        affinity: Attribute description.
        response_model: Attribute description.
        tools: Attribute description."""

    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1)
    affinity: Affinity = Affinity.MACRO
    response_model: str | None = None  # dotted path to a Pydantic model for Instructor
    tools: list[dict[str, Any]] | None = None  # OpenAI-style JSON tool schemas (agent nodes)

    @classmethod
    def of(cls, system: str, user: str, **kwargs: Any) -> ChatRequest:
        """Build a system/user request.

        Args:
            system: System message.
            user: User message.
            **kwargs: Extra request fields.

        Returns:
            ChatRequest: Built request.
        """
        return cls(
            messages=[
                ChatMessage(role=Role.system, content=system),
                ChatMessage(role=Role.user, content=user),
            ],
            **kwargs,
        )


class UsageSummary(BaseModel):
    """UsageSummary.

    Attributes:
        model_config: Attribute description.
        input_tokens: Attribute description.
        output_tokens: Attribute description.
        total_tokens: Attribute description.
        cost_usd: Attribute description."""

    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0  # recomputed by a local pricing table, never trusted from vendor


class ChatResult(BaseModel):
    """ChatResult.

    Attributes:
        model_config: Attribute description.
        message: Attribute description.
        usage: Attribute description.
        provider: Attribute description.
        model: Attribute description.
        meta: Attribute description."""

    model_config = ConfigDict(extra="forbid")

    message: ChatMessage
    usage: UsageSummary
    provider: str  # adapter that served the request
    model: str  # actual model id returned
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_response(cls, text: str, *, provider: str, model: str, usage: UsageSummary) -> ChatResult:
        """From response.

        Args:
            text: Encoded text.
            provider: Provider key for rate limiting.
            model: Model name.
            usage: Token usage summary.

        Returns:
            ChatResult: Model result.
        """
        return cls(
            message=ChatMessage(role=Role.assistant, content=text),
            usage=usage,
            provider=provider,
            model=model,
        )
