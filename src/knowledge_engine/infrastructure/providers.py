"""Concrete zero-cost LLM provider adapters (ADR 0001/0004 adapters).

Each adapter implements the minimal `BaseProviderAdapter` surface the router
needs. Structured extraction is handled generically by the router via
Instructor wrapping each adapter's raw client.
"""

from __future__ import annotations

import logging
import time
from typing import Any

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
    """Generic adapter routing through litellm's unified endpoint.

    Attributes:
        name: Attribute description.
        model_id: Attribute description.
        affinity: Attribute description.
    """

    name = "litellm"
    model_id: str = "gpt-4o-mini"  # overridden by subclasses to a free model
    affinity: Affinity | None = None

    def __init__(self, api_key: str | None = None, cassette: Any | None = None) -> None:
        """Create a LiteLLMProviderAdapter.

        Args:
            api_key: API key.
            cassette: Eval cassette routing model calls through record/replay.
        """
        self.api_key = api_key
        self.cassette = cassette
        self.raw_client = None  # placeholders for Instructor wrapping

    async def complete(self, req: ChatRequest) -> ChatResult:
        """Complete a request.

        Args:
            req: Request object.

        Returns:
            ChatResult: Model result.

        Raises:
            ProviderUnavailable: When the operation fails.

        """
        started = time.perf_counter()
        messages = [m.model_dump() for m in req.messages]
        request = {
            "model": self.model_id,
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }

        async def live(**kwargs: Any) -> dict[str, Any]:
            resp = await litellm.acompletion(
                **kwargs,
                **({"api_key": self.api_key} if self.api_key else {}),
            )
            return resp.model_dump(mode="json")

        try:
            if self.cassette is not None:
                data = await self.cassette.achat(live, **request)
            else:
                data = await live(**request)
        except litellm.exceptions.RateLimitError as exc:
            _emit_transient(self.name, started)
            raise ProviderUnavailable(f"{self.name} 429") from exc
        except litellm.exceptions.APIError as exc:
            _emit_transient(self.name, started)
            raise ProviderUnavailable(f"{self.name} API error") from exc

        usage = data.get("usage", {}) or {}
        choices = data.get("choices", []) or [{}]
        finish_text = ((choices[0].get("message") or {}).get("content")) or ""
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        result = ChatResult.from_response(
            finish_text,
            provider=self.name,
            model=self.model_id,
            usage=UsageSummary(
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                cost_usd=compute_cost_usd(self.name, self.model_id, prompt_tokens, completion_tokens),
            ),
        )
        _emit_success(self.name, result.usage, started)
        return result

    async def extract(self, req: ExtractionRequest, response_model: Any) -> object:
        """Extract structured data.

        Args:
            req: Extraction request.
            response_model: Target response model.

        Returns:
            object: Extracted instance.
        """
        import instructor

        wrapped = instructor.from_litellm(self._client())
        system = f"Extract from source: {getattr(req.source, 'title', '')}" if req.source else "Extract as instructed."
        return await wrapped.chat.completions.create_with_completion(
            model=self.model_id,
            response_model=response_model,
            max_retries=req.retries,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": _source_as_prompt(req.source)},
            ],
        )

    def _client(self) -> Any:
        """Return the litellm client module.

        Returns:
            Any: Litellm client.
        """
        if self.api_key:
            return litellm
        return litellm


def _emit_success(provider: str, usage: UsageSummary, started: float) -> None:
    """Record a success observation.

    Args:
        provider: Provider key for rate limiting.
        usage: Token usage summary.
        started: Start timestamp.

    """
    token_usage.add(usage.input_tokens, {"gen_ai.system": provider, "component": "input"})
    token_usage.add(usage.output_tokens, {"gen_ai.system": provider, "component": "output"})
    operation_duration.record((time.perf_counter() - started) * 1000.0, {"gen_ai.system": provider})


def _emit_transient(provider: str, started: float) -> None:
    """Record a transient-failure observation.

    Args:
        provider: Provider key for rate limiting.
        started: Start timestamp.

    """
    operation_duration.record((time.perf_counter() - started) * 1000.0, {"gen_ai.system": provider})


def _source_as_prompt(source: Any) -> str:
    """Render a source as prompt text.

    Args:
        source: Source record or body.

    Returns:
        str: Prompt text.
    """
    if source is None:
        return ""
    if hasattr(source, "markdown"):
        return source.markdown[:20000]
    if hasattr(source, "abstract"):
        return (source.title or "") + "\n\n" + (source.abstract or "")
    return str(source)


class GroqAdapter(LiteLLMProviderAdapter):
    """GroqAdapter.

    Attributes:
        name: Attribute description.
        model_id: Attribute description.
        affinity: Attribute description."""

    name = "Groq"
    model_id = "groq/openai/gpt-oss-20b"  # verified live 2026-10-03; llama-3.3 retired upstream
    affinity = Affinity.MICRO


class OpenRouterAdapter(LiteLLMProviderAdapter):
    """OpenRouterAdapter.

    Attributes:
        name: Attribute description.
        model_id: Attribute description.
        affinity: Attribute description."""

    name = "OpenRouter"
    model_id = "openrouter/openai/gpt-4o-mini"
    affinity = Affinity.FALLBACK


class GeminiAdapter(LiteLLMProviderAdapter):
    """GeminiAdapter.

    Attributes:
        name: Attribute description.
        model_id: Attribute description.
        affinity: Attribute description."""

    name = "Gemini"
    model_id = "gemini/gemini-flash-lite-latest"  # verified live 2026-10-03 (litellm 1.x maps only -latest aliases)
    affinity = Affinity.MACRO


class MistralAdapter(LiteLLMProviderAdapter):
    """MistralAdapter.

    Attributes:
        name: Attribute description.
        model_id: Attribute description.
        affinity: Attribute description."""

    name = "Mistral"
    model_id = "mistral/mistral-large-latest"
    affinity = Affinity.MILLION
