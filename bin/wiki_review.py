#!/usr/bin/env python3
"""Wiki draft review CLI — the human-in-the-loop review loop.

Loop 1/next-increment: ``compile_extraction`` files concept/entity pages as
drafts; a human reviews them here. Approve publishes (with the hand-edit hash
guard); reject records feedback that the next compile must address.

    uv run python bin/wiki_review.py list                          # default root
    uv run python bin/wiki_review.py /path/wiki accept <slug>
    uv run python bin/wiki_review.py /path/wiki reject <slug> "feedback text"
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "src")

from knowledge_engine.infrastructure.wiki import FileWikiStore

_COMMANDS = {"list", "accept", "reject"}


def main() -> int:
    """Run the review command.

    Returns:
        int: Exit code (1 on usage errors; 2 when a draft is missing).
    """
    args = sys.argv[1:]
    if not args:
        print(_usage())
        return 1
    if args[0] in _COMMANDS:
        root = "research_store/wiki"
        cmd = args[0]
        rest = args[1:]
    else:
        root = args[0]
        if len(args) < 2:
            print(_usage())
            return 1
        cmd = args[1]
        rest = args[2:]

    store = FileWikiStore(root=root)
    if cmd == "list":
        drafts_dir = Path(root) / "drafts"
        drafts = sorted(drafts_dir.glob("*.md")) if drafts_dir.is_dir() else []
        if not drafts:
            print("no drafts")
            return 0
        for path in drafts:
            print(f"{path.stem}\t{path}")
        return 0
    if cmd == "accept" and rest:
        try:
            locator = store.approve_draft(rest[0])
        except Exception as exc:  # noqa: BLE001 - surface the review decision
            print(f"refused: {exc}")
            return 2
        print(f"published: {locator}")
        return 0
    if cmd == "reject" and rest:
        store.reject_draft(rest[0], rest[1] if len(rest) > 1 else "no feedback provided")
        print(f"rejected: {rest[0]}")
        return 0
    print(_usage())
    return 1


def _usage() -> str:
    """Render the usage line.

    Returns:
        str: Usage text.
    """
    return 'usage: wiki_review [ROOT] list | accept <slug> | reject <slug> "feedback"'


if __name__ == "__main__":
    raise SystemExit(main())
