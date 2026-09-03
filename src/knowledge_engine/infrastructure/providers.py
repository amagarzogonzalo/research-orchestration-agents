"""Concrete zero-cost LLM provider adapters (ADR 0001/0004 adapters).

Each adapter implements the minimal `BaseProviderAdapter` surface the router
needs. Structured extraction is handled generically by the router via
Instructor wrapping each adapter's raw client.
"""

from __future__ import annotations

import logging
import time

import litellm

from knowledge_engine.core.exceptions import ProviderUnavailable
from knowledge_engine.core.pricing import compute_cost_usd
from knowledge_engine.infrastructure.router import BaseProviderAdapter
from knowledge_engine.observability.telemetry import operation_duration, token_usage
from knowledge_engine.schemas.extraction import ExtractionRequest
from knowledge_engine.schemas.provider import Affinity, ChatRequest, ChatResult, UsageSummary

logger = logging.getLogger(__name__)

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


class LiteLLMProviderAdapter(BaseProviderAdapter):
    """Generic adapter routing through litellm's unified endpoint."""

    name = "litellm"
    model_id: str = "gpt-4o-mini"  # overridden by subclasses to a free model
    affinity: Affinity | None = None

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key
        self.raw_client = None  # placeholders for Instructor wrapping

    async def complete(self, req: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        messages = [m.model_dump() for m in req.messages]
        try:
            resp = await litellm.acompletion(
                model=self.model_id,
                messages=messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                **( {"api_key": self.api_key} if self.api_key else {}),
            )
        except litellm.exceptions.RateLimitError as exc:
            _emit_transient(self.name, started)
            raise ProviderUnavailable(f"{self.name} 429") from exc
        except litellm.exceptions.APIError as exc:
            _emit_transient(self.name, started)
            raise ProviderUnavailable(f"{self.name} API error") from exc

        usage = resp.usage
        finish_text = resp.choices[0].message.content or ""
        result = ChatResult.from_response(
            finish_text,
            provider=self.name,
            model=self.model_id,
            usage=UsageSummary(
                input_tokens=usage.prompt_tokens or 0,
                output_tokens=usage.completion_tokens or 0,
                total_tokens=(usage.prompt_tokens or 0) + (usage.completion_tokens or 0),
                cost_usd=compute_cost_usd(
                    self.name, self.model_id, usage.prompt_tokens or 0, usage.completion_tokens or 0
                ),
            ),
        )
        _emit_success(self.name, result.usage, started)
        return result

    async def extract(self, req: ExtractionRequest, response_model) -> object:
        import instructor

        wrapped = instructor.from_litellm(self._client())
        system = (
            f"Extract from source: {getattr(req.source, 'title', '')}"
            if req.source else "Extract as instructed."
        )
        return await wrapped.chat.completions.create_with_completion(
            model=self.model_id,
            response_model=response_model,
            max_retries=req.retries,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _source_as_prompt(req.source)},
            ],
        )

    def _client(self):
        if self.api_key:
            return litellm
        return litellm


def _emit_success(provider: str, usage: UsageSummary, started: float) -> None:
    token_usage.add(usage.input_tokens, {"gen_ai.system": provider, "component": "input"})
    token_usage.add(usage.output_tokens, {"gen_ai.system": provider, "component": "output"})
    operation_duration.record((time.perf_counter() - started) * 1000.0, {"gen_ai.system": provider})


def _emit_transient(provider: str, started: float) -> None:
    operation_duration.record((time.perf_counter() - started) * 1000.0, {"gen_ai.system": provider})


def _source_as_prompt(source) -> str:
    if source is None:
        return ""
    if hasattr(source, "markdown"):
        return source.markdown[:20000]
    if hasattr(source, "abstract"):
        return (source.title or "") + "\n\n" + (source.abstract or "")
    return str(source)


class GroqAdapter(LiteLLMProviderAdapter):
    name = "Groq"
    model_id = "groq/llama-3.3-70b-versatile"
    affinity = Affinity.MICRO


class OpenRouterAdapter(LiteLLMProviderAdapter):
    name = "OpenRouter"
    model_id = "openrouter/openai/gpt-4o-mini"
    affinity = Affinity.FALLBACK


class GeminiAdapter(LiteLLMProviderAdapter):
    name = "Gemini"
    model_id = "gemini/gemini-2.0-flash"
    affinity = Affinity.MACRO


class MistralAdapter(LiteLLMProviderAdapter):
    name = "Mistral"
    model_id = "mistral/mistral-large-latest"
    affinity = Affinity.MILLION
