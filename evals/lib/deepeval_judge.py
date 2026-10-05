"""Pinned judge model for DeepEval metrics (offline-safe).

Wraps the harness's existing OpenRouter judge client in DeepEval's
``DeepEvalBaseLLM`` interface, so every DeepEval metric judges with the SAME
pinned model as ``evals/lib/judge.py`` — and judge traffic keeps flowing
through the current ``Cassette`` record/replay path instead of hitting the
network on every run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from deepeval.models.base_model import DeepEvalBaseLLM


class PinnedJudge(DeepEvalBaseLLM):  # type: ignore[no-untyped-call]
    """DeepEval judge backed by the harness's pinned judge client.

    Attributes:
        model_name: Pinned judge model identifier (for reporting only).
    """

    def __init__(self, client: Callable[[str], str], model_name: str) -> None:
        """Create a PinnedJudge.

        Args:
            client: Callable mapping a prompt to judge text (usually a closure
                over the cassette-routed OpenRouter client).
            model_name: Pinned judge model identifier (for reporting only).
        """
        self._client = client
        self._model_name = model_name
        super().__init__(model_name)

    def load_model(self, *args: Any, **kwargs: Any) -> PinnedJudge:
        """Return this wrapper as the loaded model.

        Args:
            *args: Ignored.
            **kwargs: Ignored.

        Returns:
            PinnedJudge: This wrapper.
        """
        return self

    def generate(self, *args: Any, **kwargs: Any) -> str:
        """Generate judge text for a prompt.

        Args:
            *args: First positional arg is the prompt.
            **kwargs: May carry ``prompt=`` instead.

        Returns:
            str: Judge text.
        """
        if args:
            prompt = args[0]
        else:
            prompt = kwargs.get("prompt", "")
        return self._client(prompt)

    async def a_generate(self, *args: Any, **kwargs: Any) -> str:
        """Async mirror of :meth:`generate` (client is synchronous).

        Args:
            *args: First positional arg is the prompt.
            **kwargs: May carry ``prompt=`` instead.

        Returns:
            str: Judge text.
        """
        return self.generate(*args, **kwargs)

    def get_model_name(self, *args: Any, **kwargs: Any) -> str:
        """Return the pinned judge model identifier.

        Args:
            *args: Ignored.
            **kwargs: Ignored.

        Returns:
            str: Pinned judge model identifier.
        """
        return self._model_name
