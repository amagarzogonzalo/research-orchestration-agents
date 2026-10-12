create schema if not exists wiki;
-- NOTE: single-user, backend connects via DSN. RLS is intentionally NOT enabled.
-- Add RLS + policies before ANY client-side (anon/authenticated) access.

-- ===== identity =====
create table wiki.pages (
  id                  uuid primary key default gen_random_uuid(),
  kind                text not null check (kind in ('source','concept','entity','comparison','synthesis','query')),
  slug                text not null check (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  status              text not null default 'draft' check (status in ('draft','active','archived')),
  current_revision_id uuid,
  lang                text not null default 'en',
  volatility          text not null default 'slow' check (volatility in ('stable','slow','fast')),
  review_after        timestamptz,
  pinned              boolean not null default false,
  archived_at         timestamptz,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  unique (kind, slug)
);
create index pages_kind_status_idx on wiki.pages (kind, status);
create index pages_review_idx on wiki.pages (review_after) where review_after is not null;

-- ===== runs (provenance) =====
create table wiki.runs (
  thread_id   text primary key,
  query_text  text not null,
  status      text not null default 'finished' check (status in ('running','finished','failed','cancelled')),
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  n_sources   int,
  providers   jsonb not null default '{}'::jsonb,
  settings    jsonb not null default '{}'::jsonb
);
create index runs_started_idx on wiki.runs (started_at desc);

-- ===== files registry (content-addressed pointers into Supabase Storage) =====
create table wiki.files (
  sha256      text primary key check (sha256 ~ '^[0-9a-f]{64}$'),
  bucket      text not null default 'bodies',
  path        text not null,
  size_bytes  bigint not null check (size_bytes >= 0),
  mime        text not null default 'text/markdown',
  created_at  timestamptz not null default now(),
  verified_at timestamptz,                         -- last successful integrity check
  unique (bucket, path),
  check (path like substr(sha256, 1, 2) || '/' || sha256 || '%')   -- sharded, hash-derived key
);

-- ===== content =====
create table wiki.revisions (
  id             uuid primary key default gen_random_uuid(),
  page_id        uuid not null references wiki.pages(id) on delete cascade,
  rev_no         int  not null check (rev_no > 0),
  state          text not null default 'draft' check (state in ('draft','approved','rejected','superseded')),
  title          text not null,
  body_md        text not null,
  frontmatter    jsonb not null default '{}'::jsonb check (jsonb_typeof(frontmatter) = 'object'),
  tldr           text,
  confidence     text check (confidence in ('EXTRACTED','INFERRED','AMBIGUOUS')),
  author_kind    text not null default 'ingest' check (author_kind in ('ingest','llm','human')),
  thread_id      text references wiki.runs(thread_id) deferrable initially deferred,
  model          text,
  prompt_version text,
  schema_version smallint not null default 1,
  reviewed_by    text,
  reviewed_at    timestamptz,
  word_count     int,
  content_sha256 text not null check (content_sha256 ~ '^[0-9a-f]{64}$'),
  created_at     timestamptz not null default now(),
  search_tsv     tsvector generated always as (
                   setweight(to_tsvector('english', coalesce(title,'')), 'A') ||
                   setweight(to_tsvector('english', coalesce(body_md,'')), 'B')
                 ) stored,
  unique (page_id, rev_no),
  unique (page_id, content_sha256),
  unique (page_id, id)
);
create unique index revisions_one_approved on wiki.revisions (page_id) where state = 'approved';
create index revisions_tsv_idx    on wiki.revisions using gin (search_tsv);
create index revisions_fm_idx     on wiki.revisions using gin (frontmatter jsonb_path_ops);
create index revisions_sha_idx    on wiki.revisions (content_sha256);
create index revisions_thread_idx on wiki.revisions (thread_id);

-- current revision must belong to the SAME page (composite FK; NULL skips the check)
alter table wiki.pages
  add constraint pages_current_revision_fk
  foreign key (id, current_revision_id) references wiki.revisions (page_id, id)
  deferrable initially deferred;

-- ===== sources / provenance =====
create table wiki.sources (
  id               uuid primary key default gen_random_uuid(),
  kind             text not null check (kind in ('doi','arxiv','pmid','url','other')),
  external_id      text not null,
  title            text,
  url              text,
  authors          text[] not null default '{}',
  published_at     date,
  year             int check (year between 1000 and 2100),   -- plain column: providers give year even without a full date
  venue            text,
  publication_type text check (publication_type in
                     ('journal','preprint','conference','review','benchmark','dataset','book','news','web','other')),
  peer_reviewed    boolean,
  open_access      boolean,
  citation_count   int,
  abstract         text,
  lang             text,
  provider         text,
  metadata         jsonb not null default '{}'::jsonb,
  fetched_at       timestamptz,
  unique (kind, external_id)
);
create index sources_year_idx    on wiki.sources (year);
create index sources_type_idx    on wiki.sources (publication_type);
create index sources_authors_idx on wiki.sources using gin (authors);
create index sources_meta_idx    on wiki.sources using gin (metadata jsonb_path_ops);

create table wiki.revision_sources (
  revision_id uuid not null references wiki.revisions(id) on delete cascade,
  source_id   uuid not null references wiki.sources(id) on delete restrict,
  role        text not null default 'cited' check (role in ('cited','background','contradicting','methodology')),
  primary key (revision_id, source_id)
);

create table wiki.claims (
  id          uuid primary key default gen_random_uuid(),
  revision_id uuid not null references wiki.revisions(id) on delete cascade,
  source_id   uuid references wiki.sources(id) on delete set null,
  claim_text  text not null,
  excerpt     text,
  stance      text not null default 'supports' check (stance in ('supports','contradicts','neutral')),
  status      text not null default 'active' check (status in ('active','disputed','stale'))
);
create index claims_revision_idx on wiki.claims (revision_id);

-- ===== stored bodies / run artifacts (the 0003 layer) =====
create table wiki.source_bodies (
  source_id   uuid not null references wiki.sources(id) on delete cascade,
  file_sha256 text not null references wiki.files(sha256) on delete restrict,
  tier        text not null check (tier in ('ar5iv','europepmc','crawl4ai','trafilatura','jina','manual','other')),
  fetched_at  timestamptz not null default now(),
  is_current  boolean not null default true,
  primary key (source_id, file_sha256)
);
-- exactly one current body per source; adapter flips the old one to false in the same txn
create unique index source_bodies_one_current on wiki.source_bodies (source_id) where is_current;

create table wiki.run_files (
  thread_id   text not null references wiki.runs(thread_id) on delete cascade,
  role        text not null check (role in ('article','sources','metadata','events')),
  file_sha256 text not null references wiki.files(sha256) on delete restrict,
  primary key (thread_id, role)
);

-- ===== graph =====
create table wiki.links (
  src_page_id uuid not null references wiki.pages(id) on delete cascade,
  dst_slug    text not null,
  dst_page_id uuid references wiki.pages(id) on delete set null,   -- NULL = broken/unresolved
  rel         text not null default 'mentions'
                check (rel in ('mentions','related','supports','contradicts','derived_from','supersedes')),
  primary key (src_page_id, dst_slug)
);
create index links_dst_idx on wiki.links (dst_page_id);
create index links_rel_idx on wiki.links (rel);

-- ===== tags (controlled vocabulary) =====
create table wiki.tags (
  id          uuid primary key default gen_random_uuid(),
  name        text not null unique check (name ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
  facet       text not null default 'topic' check (facet in ('topic','domain','method','keyword','project')),
  parent_id   uuid references wiki.tags(id) on delete set null,
  description text,
  created_at  timestamptz not null default now()
);
create table wiki.page_tags (
  page_id uuid not null references wiki.pages(id) on delete cascade,
  tag_id  uuid not null references wiki.tags(id) on delete cascade,
  origin  text not null default 'llm' check (origin in ('llm','human','rule')),
  weight  real not null default 1.0 check (weight between 0 and 1),
  primary key (page_id, tag_id)
);
create index page_tags_tag_idx on wiki.page_tags (tag_id);

-- ===== behavior =====
create table wiki.queries (
  query_sha256    text primary key,               -- hash of NORMALIZED text
  query_text      text not null,
  normalized_text text not null,
  lang            text,
  page_id         uuid not null references wiki.pages(id) on delete cascade,
  answered_at     timestamptz not null default now(),
  stale_after     timestamptz,
  hit_count       int not null default 0,
  last_hit_at     timestamptz
);

create table wiki.events (
  id        bigint generated always as identity primary key,
  ts        timestamptz not null default now(),
  op        text not null check (op in ('ingest','compile','thesis','query','approve','reject','feedback','lint')),
  page_id   uuid references wiki.pages(id) on delete set null,
  thread_id text,
  title     text,
  detail    jsonb not null default '{}'::jsonb      -- feedback: {"feedback": "..."}
);
create index events_page_idx on wiki.events (page_id, ts desc);
create index events_op_idx   on wiki.events (op, ts desc);

-- ===== functions =====
-- Approve a draft revision atomically. TTL mapping lives HERE (single place):
-- fast=30d, slow=180d, stable=never.
create or replace function wiki.approve_revision(p_revision_id uuid)
returns void language plpgsql as $$
declare v_page uuid;
begin
  select page_id into v_page from wiki.revisions where id = p_revision_id;
  if v_page is null then raise exception 'revision % not found', p_revision_id; end if;
  perform 1 from wiki.pages where id = v_page for update;      -- lock page first (stable lock order)
  update wiki.revisions set state = 'superseded'
   where page_id = v_page and state = 'approved' and id <> p_revision_id;
  update wiki.revisions set state = 'approved', reviewed_at = coalesce(reviewed_at, now())
   where id = p_revision_id;
  update wiki.pages
     set current_revision_id = p_revision_id,
         status = 'active',
         updated_at = now(),
         review_after = case volatility
                          when 'fast' then now() + interval '30 days'
                          when 'slow' then now() + interval '180 days'
                          else null end
   where id = v_page;
  insert into wiki.events (op, page_id, detail)
    values ('approve', v_page, jsonb_build_object('revision_id', p_revision_id));
end $$;

-- Filtered search over CURRENT approved revisions. Every filter optional.
-- p_broad=false -> websearch_to_tsquery (strict, ranked). p_broad=true -> OR of all terms.
-- Adapter: call strict first; retry with p_broad=true ONLY when strict returns zero rows,
-- and flag those hits as broad-match. Empty query = pure browse/filter.
create or replace function wiki.search_pages_filtered(
  p_query         text        default null,
  p_broad         boolean     default false,
  p_kinds         text[]      default null,
  p_tags          text[]      default null,      -- page must have ALL of these
  p_lang          text        default null,
  p_confidence    text[]      default null,
  p_updated_since timestamptz default null,
  p_min_src_year  int         default null,
  p_pub_types     text[]      default null,
  p_thread_id     text        default null,
  p_limit         int         default 10
)
returns table (slug text, kind text, title text, tldr text, confidence text,
               query_sha256 text, rank real, matched_broad boolean)
language sql stable as $$
  with q as (
    select case
             when nullif(btrim(p_query), '') is null then null
             when p_broad then replace(plainto_tsquery('english', p_query)::text, '&', '|')::tsquery
             else websearch_to_tsquery('english', p_query)
           end as tsq
  )
  select p.slug, p.kind, r.title, r.tldr, r.confidence,
         r.frontmatter->>'query_sha256' as query_sha256,
         coalesce(ts_rank_cd(r.search_tsv, q.tsq), 0)::real as rank,
         p_broad as matched_broad
    from q
    cross join wiki.pages p
    join wiki.revisions r on r.id = p.current_revision_id
   where p.status = 'active'
     and (q.tsq is null or r.search_tsv @@ q.tsq)
     and (p_kinds is null         or p.kind = any(p_kinds))
     and (p_lang is null          or p.lang = p_lang)
     and (p_confidence is null    or r.confidence = any(p_confidence))
     and (p_updated_since is null or p.updated_at >= p_updated_since)
     and (p_thread_id is null     or r.thread_id = p_thread_id)
     and (p_tags is null or (
           select count(distinct t.name)
             from wiki.page_tags pt join wiki.tags t on t.id = pt.tag_id
            where pt.page_id = p.id and t.name = any(p_tags)
         ) = cardinality(p_tags))
     and ((p_min_src_year is null and p_pub_types is null) or exists (
           select 1 from wiki.revision_sources rs join wiki.sources s on s.id = rs.source_id
            where rs.revision_id = r.id
              and (p_min_src_year is null or s.year >= p_min_src_year)
              and (p_pub_types is null    or s.publication_type = any(p_pub_types))
         ))
   order by rank desc, r.created_at desc, p.slug asc
   limit p_limit
$$;

-- ===== views =====
create or replace view wiki.v_pages as
  select p.id, p.kind, p.slug, p.status, r.title, r.tldr, r.confidence, r.thread_id,
         r.frontmatter, r.body_md, p.updated_at, p.lang, p.volatility, p.review_after,
         p.pinned, r.word_count, r.model, r.reviewed_by,
         coalesce((select array_agg(t.name order by t.name)
                     from wiki.page_tags pt join wiki.tags t on t.id = pt.tag_id
                    where pt.page_id = p.id), '{}') as tags
    from wiki.pages p join wiki.revisions r on r.id = p.current_revision_id;

create or replace view wiki.v_index as
  select kind, slug, title, tldr from wiki.v_pages where status = 'active' order by kind, title;

create or replace view wiki.v_broken_links as
  select p.slug as src, l.dst_slug
    from wiki.links l join wiki.pages p on p.id = l.src_page_id
   where l.dst_page_id is null;

create or replace view wiki.v_orphans as
  select p.id, p.kind, p.slug from wiki.pages p
   where p.status = 'active'
     and not exists (select 1 from wiki.links l where l.dst_page_id = p.id);

create or replace view wiki.v_stale_queries as
  select * from wiki.queries where stale_after is not null and stale_after < now();

create or replace view wiki.v_needs_review as
  select id, kind, slug, volatility, review_after from wiki.pages
   where status = 'active' and review_after is not null and review_after < now();

create or replace view wiki.v_tag_counts as
  select t.name, t.facet, count(p.id) filter (where p.status = 'active') as pages
    from wiki.tags t
    left join wiki.page_tags pt on pt.tag_id = t.id
    left join wiki.pages p on p.id = pt.page_id
   group by t.name, t.facet;

create or replace view wiki.v_unreferenced_files as
  select f.* from wiki.files f
   where not exists (select 1 from wiki.source_bodies sb where sb.file_sha256 = f.sha256)
     and not exists (select 1 from wiki.run_files rf where rf.file_sha256 = f.sha256);

create or replace view wiki.v_sources_missing_body as
  select s.id, s.kind, s.external_id, s.url from wiki.sources s
   where not exists (select 1 from wiki.source_bodies sb where sb.source_id = s.id and sb.is_current);

-- ===== optional, leave commented (pgvector tier is out of scope) =====
-- create extension if not exists vector with schema extensions;
-- alter table wiki.revisions add column embedding extensions.vector(1024), add column embedding_model text;
-- create index revisions_embedding_idx on wiki.revisions using hnsw (embedding extensions.vector_cosine_ops);
