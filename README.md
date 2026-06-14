# Notes System

Capture stays in Apple Notes, untouched. Organization happens later, by machine.
This repo ships the machinery: extract notes from the Mac, store them in Supabase,
embed them into the Librarian for "ask my notes" search (v0a), then enrich them into
a cross-business todo board (v0b).

Full design rationale: [notes-system-v0-spec.md](notes-system-v0-spec.md).

## How capture works (Mac + iPhone)

iCloud syncs every note from iPhone/iPad into the **Mac's** Notes app. So a single
scheduled job on the Mac — reading Notes via JXA — captures notes from **all** your
devices. There is nothing to install on the phone.

```
iPhone/iPad notes ──iCloud──▶ Mac Notes.app ──JXA──▶ sync_notes.py ──REST──▶ Supabase
                                                  (launchd, every 15 min)
```

## Layout

| Path | What |
|------|------|
| [sql/schema.sql](sql/schema.sql) | Supabase tables, trigger, view, indexes. Run once. |
| [extraction/notes_dump.js](extraction/notes_dump.js) | JXA: Apple Notes → JSON, incremental by mod date. |
| [extraction/sync_notes.py](extraction/sync_notes.py) | Cursor + idempotent upsert to Supabase. Stdlib only. |
| [launchd/install.sh](launchd/install.sh) | Generates + loads the every-15-min launchd agent. |
| [launchd/uninstall.sh](launchd/uninstall.sh) | Stops + removes the agent. |
| [enrichment/enrich.py](enrichment/enrich.py) | v0b: embed + extract todos (Haiku 4.5). Reference impl. |
| [enrichment/prompt.txt](enrichment/prompt.txt) | The extraction prompt (the whole game). |

## Setup

### 0. The one make-or-break detail (before SQL)

Note embeddings must live in the **same model space** and use the **same distance
metric** as the Librarian, or cross-corpus search silently returns garbage. Confirm:

1. Which embedding model the Librarian uses → sets `vector(N)` in the schema.
2. Which distance operator it queries with (cosine / L2 / inner product) → sets the index op.

Mirror both in [sql/schema.sql](sql/schema.sql) (lines marked `<-- MATCH LIBRARIAN`).
This only blocks v0b (embeddings); v0a capture works regardless.

**Configured for `text-embedding-3-large` (3072 dims, cosine).** Because pgvector's
HNSW index caps at 2000 dims, the schema stores full-precision `vector(3072)` but
indexes a `halfvec` cast — the standard pattern for >2000-dim embeddings (needs
pgvector ≥ 0.7.0). Retrieval must compare the cast to hit the index:
`order by embedding::halfvec(3072) <=> $q::halfvec(3072)`.

### 1. Credentials

```bash
cp .env.example .env
# fill in SUPABASE_URL and SUPABASE_SERVICE_KEY
```
`.env` is gitignored. The service-role key stays on this trusted machine; it never
goes into git or the launchd plist.

### 2. Database

Run [sql/schema.sql](sql/schema.sql) once against the Supabase project (SQL editor or psql).

### 3. Grant Automation permission (one time)

The first time anything scripts Notes, macOS shows an Automation prompt. A launchd
job can't click it, so run the sync **once in Terminal** to grant access:

```bash
python3 extraction/sync_notes.py
```

### 4. Load the scheduled job

```bash
./launchd/install.sh          # every 15 min (default)
./launchd/install.sh 300      # or pass seconds to override
```

First run is a full backfill (cursor = epoch). On a large library JXA can be slow —
be patient, or ask for the NoteStore.sqlite fast-backfill (spec sharp edge #2).

### 5. Add `notes` to the Librarian's corpus

Point the Librarian's retrieval at the `notes` table. **v0a done — live on it for a week.**

### 6. v0b enrichment

Wire `librarian_embed()` in [enrichment/enrich.py](enrichment/enrich.py) to the
Librarian's exact embedder, set `ANTHROPIC_API_KEY`, then schedule it as an OpenClaw
task (every ~2 min) or via launchd. Read/close todos through the `open_todos` view.

## Operating the job

```bash
# run now
launchctl kickstart -k gui/$(id -u)/com.lionel.notesync
# status + last exit code
launchctl print gui/$(id -u)/com.lionel.notesync | grep -E 'state|last exit'
# logs
tail -f sync.log sync.err
```

## v0b: reading the todo board

```sql
select * from open_todos;                          -- everything open
select * from open_todos where business = 'dreamplay';
update todos set status = 'done' where id = '<uuid>';
```
