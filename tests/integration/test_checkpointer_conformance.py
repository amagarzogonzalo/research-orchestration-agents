"""Official conformance suite for FileCheckpointer (W10 follow-up).

Runs ``langgraph-checkpoint-conformance`` (dev-only extra) against the custom
saver: any drift between our implementation and LangGraph's saver contract
fails HERE, not in production. Extended capabilities (delta channels, prune,
copy/fork) auto-detect; base capabilities must all pass.
"""

from __future__ import annotations

import shutil
import tempfile

from langgraph.checkpoint.conformance import checkpointer_test, validate

from knowledge_engine.store.file_checkpointer import FileCheckpointer


@checkpointer_test(name="FileCheckpointer")
async def conformance_saver():
    """Yield a FileCheckpointer backed by a throwaway directory.

    Yields:
        FileCheckpointer: The saver under test.
    """
    root = tempfile.mkdtemp()
    saver = FileCheckpointer(root=root)
    try:
        yield saver
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def test_file_checkpointer_passes_conformance() -> None:
    """Every base saver capability passes; the report is printed for the log."""
    report = await validate(conformance_saver)
    report.print_report()
    assert report.passed_all_base(), "FileCheckpointer drifted from the LangGraph saver contract"
