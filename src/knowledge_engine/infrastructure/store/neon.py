"""Neon/Postgres knowledge-store adapter (ADR 0001 StorePort).

Writes standardized knowledge artifacts (ADR 0002 §7) to Postgres so downstream
RAG can query metadata without loading bodies.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from knowledge_engine.schemas.artifact import KnowledgeArtifact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_artifacts (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'KnowledgeBaseArticle',
    title TEXT NOT NULL,
    tldr_llm TEXT NOT NULL DEFAULT '',
    provenance JSONB NOT NULL DEFAULT '[]',
    body_markdown TEXT NOT NULL DEFAULT '',
    citations JSONB NOT NULL DEFAULT '[]',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class NeonStoreAdapter:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def ensure_schema(self) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            await conn.execute(_SCHEMA)
            await conn.commit()

    async def put(self, artifact: KnowledgeArtifact) -> None:
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            await conn.execute(
                """
                INSERT INTO knowledge_artifacts (id, type, title, tldr_llm, provenance, body_markdown, citations)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    tldr_llm = EXCLUDED.tldr_llm,
                    provenance = EXCLUDED.provenance,
                    body_markdown = EXCLUDED.body_markdown,
                    citations = EXCLUDED.citations
                """,
                (
                    artifact.id,
                    artifact.type,
                    artifact.title,
                    artifact.tldr_llm,
                    _dumps(artifact.provenance),
                    artifact.body_markdown,
                    _dumps(artifact.citations),
                ),
            )
            await conn.commit()

    async def get(self, artifact_id: str) -> dict | None:
        async with await psycopg.AsyncConnection.connect(self.dsn, row_factory=dict_row) as conn:
            cur = await conn.execute("SELECT * FROM knowledge_artifacts WHERE id = %s", (artifact_id,))
            row = await cur.fetchone()
            return row


def _dumps(obj) -> str:
    import json

    return json.dumps([m.model_dump(mode="json") if hasattr(m, "model_dump") else m for m in obj])
