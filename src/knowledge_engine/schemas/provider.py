"""Provider transport + usage telemetry contracts (ADR 0002 §1–§2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"
    tool = "tool"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role
    content: str
    name: str | None = Field(default=None)


class Affinity(StrEnum):
    """Task-class routing hint consumed by the FallbackRouter (ADR 0004)."""

    MICRO = "micro"  # cheap, ultra-low-latency: boolean filter, entity extract
    MACRO = "macro"  # high-context aggregation: mid-document synthesis
    MILLION = "million"  # massive token volume: bulk parse, corpora
    FALLBACK = "fallback"  # no-SLA catch-all before local Ollama


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1)
    affinity: Affinity = Affinity.MACRO
    response_model: str | None = None  # dotted path to a Pydantic model for Instructor

    @classmethod
    def of(cls, system: str, user: str, **kwargs) -> ChatRequest:
        return cls(
            messages=[
                ChatMessage(role=Role.system, content=system),
                ChatMessage(role=Role.user, content=user),
            ],
            **kwargs,
        )


class UsageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0  # recomputed by a local pricing table, never trusted from vendor


class ChatResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: ChatMessage
    usage: UsageSummary
    provider: str  # adapter that served the request
    model: str  # actual model id returned
    meta: dict = Field(default_factory=dict)

    @classmethod
    def from_response(cls, text: str, *, provider: str, model: str, usage: UsageSummary) -> ChatResult:
        return cls(
            message=ChatMessage(role=Role.assistant, content=text),
            usage=usage,
            provider=provider,
            model=model,
        )
