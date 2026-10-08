"""synthesize node: abstractive research synthesis (LLM step, ADR 0008).

The :class:`SynthesizeNode` formats a prompt from the *raw* state (bodies +
optional judge critique), calls the MACRO-affinity provider, and appends the
notes via the ``notes`` add-reducer so the evaluator-optimizer loop retains
each iteration. ``iterations`` is bumped here so the route function can bound
the loop (Saga compensation lives at the graph layer via error_handler).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from knowledge_engine.core.exceptions import KnowledgeEngineError, ProviderExhausted
from knowledge_engine.core.ports import BodyStoragePort, ProviderPort
from knowledge_engine.orchestration.state import (
    MAX_SYNTHESIS_ITERATIONS,
    WorkflowNode,
    WorkflowState,
)
from knowledge_engine.schemas.provider import Affinity, ChatRequest
from knowledge_engine.schemas.source import BodyRef, DocumentBody

logger = logging.getLogger(__name__)

# Per-body evidence cap: full-text bodies (ar5iv renders 100k+ chars) must not
# flood the synthesis prompt. ~30 bodies x 2500 chars keeps the macro call
# inside the case token budgets; the cap binds cost, the budget stays fixed
# (never raised to fit richer evidence).
_MAX_BODY_CHARS = 2500

_SYNTHESIS_SYSTEM = (
    "You are a senior research synthesizer. Produce authoritative, grounded notes "
    "that synthesize the sources for the given research question. Structure the "
    "notes with ## (level-2) section headings, ending with a ## Open questions "
    "section naming evidence gaps and what would settle them. Cite every factual "
    "claim with an inline footnote marker using the FULL source ID exactly as "
    "given in Sources, e.g. "
    '"RAG grounds generation in retrieved passages [^openalex:W123]." Every '
    "number, percentage, quoted phrase, or named benchmark result MUST carry "
    "its footnote in the same sentence — a quantitative claim without a "
    "same-sentence citation is a failure. At the end, "
    "add one definition line per cited source, e.g. "
    '"[^openalex:W123]: Paper Title Here. Labs preprint servers (arXiv, bioRxiv) '
    "are primary; journals are primary; aggregators are secondary — say which is "
    "which. Load-bearing claims (mechanisms, effectiveness, numbers) must rest "
    "on primary sources: aggregators and encyclopedias supply background "
    "only, never the sole support for a load-bearing claim. Every section "
    "MUST cite at least two sources; a section with no "
    "citations is a failure. Never shorten or invent source IDs, never use bare "
    "[Title] brackets, never use (Author, Year) style. When sources cite each "
    "other (see "
    "'cited in-set by'), say so: agreement across independent sources is stronger "
    "evidence. Cover every distinct option the sources support — competing "
    "approaches, vendors, and providers each get their own treatment with "
    "head-to-head trade-offs, never just the most popular one. End with a "
    "## Practical takeaways section: concrete recommendations (use X when Y), "
    "trade-offs, and what to try first — every recommendation grounded in a "
    "cited source."
)


class SynthesizeNode(WorkflowNode):
    """Deterministic port-based synthesis (the offline-testable default)."""

    __slots__ = ("provider", "storage", "max_iterations")

    def __init__(
        self,
        provider: ProviderPort,
        *,
        storage: BodyStoragePort,
        max_iterations: int = MAX_SYNTHESIS_ITERATIONS,
    ) -> None:
        """Create a SynthesizeNode.

        Args:
            provider: Provider key for rate limiting.
            storage: Body persistence used to materialize BodyRef pointers.
            max_iterations: Maximum iterations.
        """
        self.provider = provider
        self.storage = storage
        self.max_iterations = max_iterations

    async def run(self, state: WorkflowState) -> dict[str, Any]:
        """Run the unit of work.

        Args:
            state: Current workflow state.

        Returns:
            dict[str, Any]: Result mapping.

        """
        critique = state.get("judge_critique")
        materialized: list[BodyRef | DocumentBody] = await _materialize_bodies(state, self.storage)
        prompt_state: WorkflowState = {**state, "bodies": materialized}
        prompt = self._build_prompt(prompt_state, critique)
        req = ChatRequest.of(
            system=_SYNTHESIS_SYSTEM,
            user=prompt,
            affinity=Affinity.MACRO,
            # 2500: 1500 truncated 5-section artifacts mid-sentence (the
            # mandated Practical Takeaways never rendered). Measured against
            # case token budgets, not raised to fit them.
            max_tokens=2500,
        )
        res = await self.provider.complete(req)
        return {"notes": [res.message.content], "iterations": state.get("iterations", 0) + 1}

    @staticmethod
    def _build_prompt(state: WorkflowState, critique: str | None) -> str:
        # Ground the synthesis on scraped bodies when available; otherwise fall
        # back to source titles + abstracts so the LLM is never ungrounded.
        """Build prompt.

        Args:
            state: Current workflow state.
            critique: Critique text.

        Returns:
            str: Result text.

        """
        sources = list(state.get("filtered") or state.get("sources", []))
        cited_by = _cited_by_map(sources)
        if state.get("bodies"):
            # Canonical order: the add-reducer appends bodies in completion
            # order, which varies run to run. Sorting keeps prompts (and
            # cassette keys) deterministic.
            ordered = sorted(state["bodies"], key=_body_sort_key)
            evidence = "\n---\n".join(_labeled_body(b, state) for b in ordered)
        else:
            parts = []
            for src in sources:
                # IDs are part of the evidence: the model can only cite exact
                # source IDs it has actually seen (see _SYNTHESIS_SYSTEM).
                parts.append(_ledger_line(src, cited_by.get(_norm_ref(_src_id(src)), [])))
            evidence = "\n---\n".join(parts) or "(no sources)"
        prompt = (
            f"Synthesize the research findings into structured notes for query:\n"
            f"{state.get('query', '')}\n\nSources:\n{evidence}"
        )
        if critique:
            prompt += f"\n\nReviewer feedback to address:\n{critique}"
        return prompt


async def _materialize_bodies(state: WorkflowState, storage: BodyStoragePort | None) -> list[BodyRef | DocumentBody]:
    """Resolve every body-channel entry into inline DocumentBody records.

    BodyRef objects (and their dict snapshots after a checkpoint resume) load
    their markdown through the storage port; inline DocumentBody records and
    legacy dict snapshots pass through untouched. The prompt formatter sees
    one canonical shape, so prompt bytes are identical across both state eras
    (ADR 0013 — the replay-neutrality contract).

    Args:
        state: Current workflow state.
        storage: Body persistence wired on the node.

    Returns:
        list[BodyRef | DocumentBody]: Bodies ready for prompt building.
    """
    out: list[BodyRef | DocumentBody] = []
    # Channel values are serde-reconstructed at runtime; treat as Any so the
    # legacy dict-snapshot branches stay live (checkpoint-era resilience).
    bodies: list[Any] = list(state.get("bodies", []))
    for body in bodies:
        if isinstance(body, BodyRef):
            out.append(await _ref_to_body(body, storage))
        elif isinstance(body, DocumentBody):
            out.append(body)
        elif isinstance(body, dict) and "path" in body and "markdown" not in body:
            out.append(await _ref_to_body(BodyRef.model_validate(body), storage))
        elif isinstance(body, dict):
            out.append(DocumentBody.model_validate(body))
        else:
            out.append(DocumentBody(url="", title="", markdown=""))
    return out


async def _ref_to_body(ref: BodyRef, storage: BodyStoragePort | None) -> DocumentBody:
    """Load a ref's markdown through storage; loud failure when unwired.

    Args:
        ref: The body pointer.
        storage: Body persistence (required for refs).

    Returns:
        DocumentBody: Inline body with the ref's metadata.

    Raises:
        KnowledgeEngineError: When storage is None.
    """
    if storage is None:
        raise KnowledgeEngineError("body refs present but no BodyStoragePort wired (ADR 0013)")
    return DocumentBody(
        url=ref.url,
        title=ref.title,
        markdown=await storage.load(ref),
        char_count=ref.char_count,
        crawl_method=ref.crawl_method,
        captured_at=ref.captured_at,
    )


def _body_sort_key(body: object) -> str:
    """Stable sort key for bodies regardless of fan-out completion order.

    Args:
        body: Document body.

    Returns:
        str: URL fallback title.
    """
    if isinstance(body, dict):
        return str(body.get("url") or body.get("title") or "")
    return str(getattr(body, "url", "") or getattr(body, "title", "") or "")


def _body_text(body: object) -> str:
    """Coerce a DocumentBody (Pydantic) or its raw dict snapshot to a snippet.

    Args:
        body: Document body.

    Returns:
        str: Result text.

    """
    if isinstance(body, DocumentBody):
        title, markdown = body.title or body.url, body.markdown
    elif isinstance(body, dict):
        title = body.get("title") or body.get("url") or ""
        markdown = body.get("markdown") or ""
    else:
        title, markdown = "", ""
    return f"{title}\n{markdown[:_MAX_BODY_CHARS]}"


def _norm_ref(value: str) -> str:
    """Normalize an ID/DOI for cross-source matching.

    Args:
        value: Raw identifier.

    Returns:
        str: Lowercased identifier without URL/DOI prefixes.
    """
    text = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text


def _cited_by_map(sources: Sequence[object]) -> dict[str, list[str]]:
    """Map each retrieved source to the in-set sources citing it.

    Matches on normalized IDs and DOIs so OpenAlex references resolve
    Crossref records and vice versa.

    Args:
        sources: Retrieved sources.

    Returns:
        dict[str, list[str]]: Normalized id -> citing source ids.
    """
    by_norm: dict[str, str] = {}
    for src in sources:
        sid = _src_id(src)
        if sid:
            by_norm.setdefault(_norm_ref(sid), sid)
        doi = _src_doi(src)
        if doi:
            by_norm.setdefault(_norm_ref(doi), sid or doi)
    cited: dict[str, list[str]] = {}
    for src in sources:
        sid = _src_id(src)
        refs = _src_references(src)
        for other_norm, other_sid in by_norm.items():
            if other_sid == sid or not other_norm:
                continue
            if any(_norm_ref(ref) == other_norm for ref in refs):
                cited.setdefault(other_norm, []).append(sid)
    return cited


def _ledger_line(src: object, cited_by: list[str]) -> str:
    """Render one evidence ledger line with venue + in-set citations.

    Args:
        src: Source record.
        cited_by: IDs of retrieved sources citing this one.

    Returns:
        str: Ledger line.
    """
    venue = _src_venue(src)
    meta = ", ".join(part for part in (venue, _src_year(src)) if part)
    header = f"[{_src_id(src)}] {_src_title(src)}"
    if meta:
        header += f" — {meta}"
    if cited_by:
        header += f" | cited in-set by: {', '.join(sorted(set(cited_by)))}"
    return f"{header}\n{_src_abstract(src)[:2000]}"


def _src_id(src: object) -> str:
    """ID from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Source ID.
    """
    if isinstance(src, dict):
        return src.get("id", "") or ""
    return getattr(src, "id", "") or ""


def _labeled_body(body: object, state: WorkflowState) -> str:
    """Render a body prefixed with its source ID when resolvable.

    Bodies carry URLs, not source IDs; reverse-map through the same URL
    derivation the fetch fan-out used. Unmatched bodies render unlabeled
    (dict snapshots after a resume carry no mappable attributes).

    Args:
        body: Document body.
        state: Current workflow state.

    Returns:
        str: Labeled body snippet.
    """
    from knowledge_engine.orchestration.nodes.bodies import _derive_url

    text = _body_text(body)
    url = body.get("url") if isinstance(body, dict) else getattr(body, "url", "")
    if not url:
        return text
    for raw in state.get("filtered") or state.get("sources", []):
        # Dict snapshots after a checkpoint resume carry no mappable attributes.
        src: object = raw
        if isinstance(src, dict):
            continue
        try:
            if _derive_url(src) == url:  # type: ignore[arg-type]
                sid = _src_id(src)
                return f"[{sid}]\n{text}" if sid else text
        except Exception:  # noqa: BLE001 - derivation must never break synthesis
            continue
    return text


def _src_title(src: object) -> str:
    """Title from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Result text.

    """
    if isinstance(src, dict):
        return src.get("title", "") or ""
    return getattr(src, "title", "") or ""


def _src_abstract(src: object) -> str:
    """Abstract from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Result text.

    """
    if isinstance(src, dict):
        return src.get("abstract", "") or ""
    return getattr(src, "abstract", "") or ""


def _src_doi(src: object) -> str:
    """DOI from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: DOI text.
    """
    if isinstance(src, dict):
        return src.get("doi", "") or ""
    return getattr(src, "doi", "") or ""


def _src_venue(src: object) -> str:
    """Venue from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Venue text.
    """
    if isinstance(src, dict):
        return src.get("venue", "") or ""
    return getattr(src, "venue", "") or ""


def _src_year(src: object) -> str:
    """Year from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        str: Year text.
    """
    if isinstance(src, dict):
        return str(src.get("year") or "")
    return str(getattr(src, "year", "") or "")


def _src_references(src: object) -> list[str]:
    """References from a SourceRecord (Pydantic) or its raw dict snapshot.

    Args:
        src: Source record.

    Returns:
        list[str]: Cited IDs.
    """
    if isinstance(src, dict):
        refs = src.get("references", []) or []
    else:
        refs = getattr(src, "references", []) or []
    return [str(r) for r in refs if r]


__all__ = ["SynthesizeNode", "ProviderExhausted"]
