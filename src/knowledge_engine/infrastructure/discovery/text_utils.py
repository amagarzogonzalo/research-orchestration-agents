"""Text helpers for cleaning LLM output (adapted from digest_agent)."""

from __future__ import annotations


def strip_reasoning_prefix(text: str) -> str:
    """Remove a leading reasoning/thinking block some models emit.

    Args:
        text: Raw model output.

    Returns:
        str: The visible answer only. Unchanged when no reasoning marker is found.
    """
    lowered = text.lower()
    markers = ("<thinking", "<|start|>", "thinking\n", "here's a thinking", "tool used")
    idx = -1
    for m in markers:
        j = lowered.find(m)
        if j != -1 and (idx == -1 or j < idx):
            idx = j
    if idx == -1:
        return text
    rest = text[idx:]
    for close in ("</thinking>", "<|end|>", "<end_of_turn>"):
        c = rest.lower().find(close)
        if c != -1:
            return rest[c + len(close) :].strip()
    lines = rest.splitlines()
    keep = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not _is_reasoning_line(stripped):
            keep = i
            break
    return "\n".join(lines[keep:]).strip()


def _is_reasoning_line(line: str) -> bool:
    """Return True if a line looks like part of a thinking block."""
    lowered = line.lower()
    starters = ("thinking", "<", "here's", "to answer", "to start", "i'll", "let me", "the user", "we need")
    if lowered.startswith(starters):
        return True
    if lowered.startswith(("* ", "- ", "# ", "**", "`")) and len(line) < 80:
        return False
    return bool(line.startswith((" ", "\t")))


__all__ = ["strip_reasoning_prefix"]
