-- Optional pgvector semantic tier (free on Supabase). Apply AFTER wiki_v2.
-- Not required for FTS search; enable it to add embeddings-based recall.
create extension if not exists vector with schema extensions;

alter table wiki.revisions
  add column if not exists embedding extensions.vector(1024),
  add column if not exists embedding_model text;

create index if not exists revisions_embedding_idx
  on wiki.revisions using hnsw (embedding extensions.vector_cosine_ops);

-- Semantic search over CURRENT approved revisions (cosine distance, nearest first).
create or replace function wiki.search_pages_semantic(
  p_query_embedding extensions.vector(1024),
  p_kinds           text[] default null,
  p_limit           int    default 10
)
returns table (slug text, kind text, title text, tldr text, confidence text,
               query_sha256 text, distance real)
language sql stable as $$
  select p.slug, p.kind, r.title, r.tldr, r.confidence,
         (r.frontmatter->>'query_sha256') as query_sha256,
         (r.embedding <=> p_query_embedding)::real as distance
    from wiki.pages p
    join wiki.revisions r on r.id = p.current_revision_id
   where r.state = 'approved'
     and r.embedding is not null
     and (p_kinds is null or p.kind = any(p_kinds))
   order by r.embedding <=> p_query_embedding
   limit greatest(p_limit, 1);
$$;
