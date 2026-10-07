"""langgraph.json parity: every declared graph must resolve; build deps declared.

This is the drift guard between the deploy surface (``langgraph.json``) and the
code (``studio.py`` exports). Adding a graph in one place without the other
fails here instead of failing a deploy.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _config() -> dict:
    """Load langgraph.json from the repo root.

    Returns:
        dict: Parsed configuration mapping.
    """
    return json.loads((ROOT / "langgraph.json").read_text(encoding="utf-8"))


def test_schema_reference_present() -> None:
    """Editor validation requires the published schema shortlink."""
    assert _config()["$schema"] == "https://langgra.ph/schema.json"


def test_python_version_matches_floor() -> None:
    """Deploy runtime must match the pyproject python floor (3.11)."""
    assert _config()["python_version"] == "3.11"


def test_every_declared_graph_is_exported() -> None:
    """Each graphs entry resolves to an attribute on its module, with a description."""
    graphs = _config()["graphs"]
    assert set(graphs) == {"deep", "research", "qa", "summary"}
    for graph_id, spec in graphs.items():
        assert isinstance(spec, dict), f"graph {graph_id!r}: use {{path, description}} form"
        path: str = spec["path"]
        module_ref, _, attr = path.partition(":")
        module_ref = module_ref.removeprefix("./").removesuffix(".py").replace("/", ".")
        module = importlib.import_module(module_ref)
        assert hasattr(module, attr), f"graph {graph_id!r}: {module_ref} has no attribute {attr!r}"
        assert len(spec.get("description", "")) > 5, f"graph {graph_id!r}: missing description"


def test_scrape_build_deps_declared() -> None:
    """The scrape tier must install into the image and chromium must be buildable."""
    lines = _config().get("dockerfile_lines", [])
    assert any("crawl4ai" in line for line in lines), "scrape extra missing from image build"
    assert any("playwright install chromium" in line for line in lines), "chromium build line missing"


def test_env_is_file_reference() -> None:
    """Env stays a file reference; never inline secrets into the config."""
    assert _config()["env"] == "./.env"
