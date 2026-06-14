-- Notes System v0 schema. Run once against the existing Supabase project.
--
-- ⚠️ MAKE-OR-BREAK: the vector(N) dimension and the index distance op below
-- MUST match the Librarian's embedding model and query metric, or cross-corpus
-- search silently returns garbage. Confirm both before running, then edit lines
-- marked  <-- MATCH LIBRARIAN  to match.

create extension if not exists vector;     -- pgvector, already present if the Librarian uses it
create extension if not exists pgcrypto;   -- gen_random_uuid()

-- Set vector(N) to match your Librarian's model:
--   text-embedding-3-small -> 1536
--   text-embedding-3-large -> 3072
--   voyage-3 / other        -> check your model
create table if not exists notes (
  id                 uuid primary key default gen_random_uuid(),
  source_id          text unique not null,           -- Apple Notes x-coredata id, stable per note
  title              text,
  body               text,
  folder             text,                            -- captured as free signal, never the organizing key
  source_created_at  timestamptz,
  source_modified_at timestamptz,
  content_hash       text not null,                   -- sha256(title + body), used for dirty detection
  embedding          vector(3072),                    -- <-- MATCH LIBRARIAN: text-embedding-3-large
  summary            text,                            -- one line, agent-generated (v0b)
  enrichment_status  text not null default 'pending', -- pending | done | error
  enriched_at        timestamptz
);

create index if not exists notes_modified_idx  on notes (source_modified_at desc);
create index if not exists notes_status_idx    on notes (enrichment_status);
-- Distance op MUST match the Librarian (cosine shown).
-- NOTE: pgvector's HNSW index caps at 2000 dims, but text-embedding-3-large is 3072.
-- So we store full-precision vector(3072) (exact match to the Librarian's vectors) and
-- build the HNSW index on a half-precision cast — the documented pgvector pattern for >2000 dims.
-- Requires pgvector >= 0.7.0 (halfvec). Supabase has shipped this since mid-2024.
-- To USE this index at query time, compare the cast, e.g.:
--   order by embedding::halfvec(3072) <=> $query::halfvec(3072)
create index if not exists notes_embedding_idx
  on notes using hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);  -- <-- MATCH LIBRARIAN (metric)

create table if not exists todos (
  id                 uuid primary key default gen_random_uuid(),
  note_id            uuid not null references notes(id) on delete cascade,
  text               text not null,
  business           text not null default 'unsorted', -- dreamplay | musicalbasics | masterclass | concert | personal | infra | unsorted
  due_date           date,
  status             text not null default 'open',     -- open | done | dismissed
  source_modified_at timestamptz,
  created_at         timestamptz not null default now()
);

create index if not exists todos_status_business_idx on todos (status, business);

-- When a note's content changes, mark it dirty so enrichment regenerates it.
-- New rows arrive as 'pending' by default, so this only fires on real edits.
create or replace function mark_note_dirty() returns trigger as $$
begin
  if new.content_hash is distinct from old.content_hash then
    new.enrichment_status := 'pending';
    new.summary := null;
    new.embedding := null;
    new.enriched_at := null;
    delete from todos where note_id = old.id;   -- see "known v0 sharp edges" #1 in the spec
  end if;
  return new;
end;
$$ language plpgsql;

drop trigger if exists notes_dirty_trigger on notes;
create trigger notes_dirty_trigger
  before update on notes
  for each row execute function mark_note_dirty();

-- v0b read surface for the todo board:
create or replace view open_todos as
  select t.id, t.text, t.business, t.due_date, t.source_modified_at,
         n.title as note_title, n.source_id
  from todos t
  join notes n on n.id = t.note_id
  where t.status = 'open'
  order by t.due_date asc nulls last, t.source_modified_at desc;
