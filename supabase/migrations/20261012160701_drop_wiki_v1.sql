-- Drop the v1 probe-only wiki tables (created only by earlier live tests).
--
-- Verified before dropping (on the real Supabase DB): public.wiki_pages held 11
-- rows and public.wiki_log 13 rows, all from live-test probe threads
-- (live-pg-wiki-* / live-verify-*). No real content is removed. The v2 relational
-- schema (wiki.*) replaces these entirely; both are never kept.
drop table if exists public.wiki_log;
drop table if exists public.wiki_links;
drop table if exists public.wiki_pages;
