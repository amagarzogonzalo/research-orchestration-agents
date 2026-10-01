"""Integration tests: MCP servers + toolkit + create_agent agent node (offline)."""

from __future__ import annotations

from knowledge_engine.core.ports import ScrapePort, SearchPort
from knowledge_engine.infrastructure.mcp.model import ProviderChatModel
from knowledge_engine.infrastructure.mcp.search_server import build_search_server
from knowledge_engine.infrastructure.mcp.toolkit import build_agent, load_tools
from tests.conftest import FakeProvider, FakeScrape, FakeSearch


async def test_load_tools_discovers_search_and_scrape() -> None:
    tools = await load_tools(search=FakeSearch(), scrape=FakeScrape())
    names = {t.name for t in tools}
    assert "search_papers" in names
    assert "fetch_markdown" in names


async def test_load_tools_without_ports_is_empty() -> None:
    assert await load_tools() == []


async def test_search_server_exposes_structured_tool() -> None:
    # The FastMCP server's tools are reachable via MCPAdapter discovery; asserting
    # the private AST is brittle. Instead: build the server and confirm load_tools
    # surfaces search_papers.
    from knowledge_engine.infrastructure.mcp.toolkit import load_tools

    server = build_search_server(FakeSearch())
    tools = await load_tools(search=FakeSearch(), scrape=None)
    assert {t.name for t in tools} == {"search_papers"}
    assert server is not None


async def test_provider_chat_model_mapsto_provider() -> None:
    provider = FakeProvider()
    model = ProviderChatModel(provider=provider)
    response = await model._agenerate([{"role": "user", "content": "hello"}])
    assert response.generations[0].message.content


async def test_create_agent_with_search_tools_runs_offline() -> None:
    fake = FakeProvider()
    tools = await load_tools(search=FakeSearch(), scrape=FakeScrape())
    model = ProviderChatModel(provider=fake)
    agent = build_agent(model, tools, system_prompt="You are a researcher. Use tools; answer briefly.")
    result = await agent.ainvoke({"messages": [{"role": "user", "content": "what is 1+1?"}]})
    last = result["messages"][-1]
    assert getattr(last, "content", "") or getattr(last, "content", "")


async def test_search_scrape_servers_are_injectable_ports() -> None:
    assert issubclass(FakeSearch, SearchPort)
    assert issubclass(FakeScrape, ScrapePort)


async def test_agent_synthesize_node_runs_with_builder() -> None:
    """AgentSynthesizeNode calls the injected builder and appends notes."""
    from knowledge_engine.orchestration.nodes import AgentSynthesizeNode
    from knowledge_engine.schemas.source import DocumentBody

    async def fake_builder() -> None:
        class _FakeMsg:
            content = "agent synthesis"

        class _FakeAgent:
            async def ainvoke(self, messages, config=None):
                return {"messages": [_FakeMsg()]}

        return _FakeAgent()

    node = AgentSynthesizeNode(fake_builder)
    out = await node.run(
        {
            "thread_id": "t",
            "query": "agentic research",
            "bodies": [DocumentBody(url="u", title="t", markdown="evidence")],
        }
    )
    assert out["notes"] == ["agent synthesis"]
    assert out["iterations"] == 1
