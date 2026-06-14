# Notes System v0 Spec

Capture stays in Apple Notes, untouched. Organization happens later and by machine. Classification serves two outputs and nothing else: ask-your-notes search, and a cross-business todo board.

Built as two increments so you ship something usable fast:

- **v0a (searchable):** extraction + embed into the Librarian. "Ask my notes" works. Ship this, live on it for a week.
- **v0b (todos):** add the enrichment pass that extracts action items with business attribution.

Hosts: extraction runs on the **Mac Studio** (the data lives in macOS, and Apple Notes has no server API). Storage is your existing **Supabase**. Enrichment runs as an **OpenClaw** task. Query goes through the **Librarian**.

---

## The one make-or-break detail

Note embeddings must live in the **same model space** and use the **same distance metric** as the Librarian, or cross-corpus search silently returns garbage. Before anything else, confirm two things about your Librarian:

1. Which embedding model it uses (sets the vector dimension).
2. Which distance operator it queries with (cosine / L2 / inner product).

Mirror both in the schema below. The enrichment pass should call the Librarian's exact embedder, not a parallel one.

---

## 1. Supabase schema (v0a)

Two tables, one trigger, one view. Run once against your existing project.

```sql
create extension if not exists vector;     -- pgvector, already present if the Librarian uses it
create extension if not exists pgcrypto;   -- gen_random_uuid()

-- Set vector(N) to match your Librarian's model:
--   text-embedding-3-small -> 1536
--   text-embedding-3-large -> 3072
--   voyage-3 / other        -> check your model
create table if not exists notes (
  id                 uuid primary key default gen_random_uuid(),
  source_id          text unique not null,          -- Apple Notes x-coredata id, stable per note
  title              text,
  body               text,
  folder             text,                           -- captured as free signal, never the organizing key
  source_created_at  timestamptz,
  source_modified_at timestamptz,
  content_hash       text not null,                  -- sha256(title + body), used for dirty detection
  embedding          vector(1536),                   -- <-- set to match the Librarian
  summary            text,                           -- one line, agent-generated (v0b)
  enrichment_status  text not null default 'pending',-- pending | done | error
  enriched_at        timestamptz
);

create index if not exists notes_modified_idx  on notes (source_modified_at desc);
create index if not exists notes_status_idx    on notes (enrichment_status);
-- distance op MUST match the Librarian (cosine shown):
create index if not exists notes_embedding_idx on notes using hnsw (embedding vector_cosine_ops);

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
    delete from todos where note_id = old.id;   -- see "known v0 sharp edges" #1
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
```

Note the deliberate cut: there is **no note-level business tagging**. Business lives only on todos. Search handles cross-business discovery; tagging notes is a "later, if a real retrieval miss demands it" feature.

---

## 2. Extraction (Mac Studio, v0a)

Apple Notes has no API. The supported paths are AppleScript and reading the local database. v0 uses JXA (JavaScript for Automation), because it returns real JSON and can push the date filter into the app. Incremental by modification date, so each run touches only a handful of notes.

**`notes_dump.js`** (returns JSON for notes modified after a cutoff):

```javascript
#!/usr/bin/env osascript -l JavaScript
// Usage: osascript -l JavaScript notes_dump.js "2026-06-01T00:00:00Z"
function run(argv) {
  const cutoff = new Date(argv[0] || "1970-01-01T00:00:00Z");
  const Notes = Application("Notes");
  const out = [];

  // Try to push the date filter into the app (fast). Fall back to full scan.
  let candidates;
  try {
    candidates = Notes.notes.whose({ modificationDate: { ">": cutoff } });
  } catch (e) {
    candidates = Notes.notes;
  }

  const n = candidates.length;
  for (let i = 0; i < n; i++) {
    const note = candidates[i];
    let modified;
    try { modified = note.modificationDate(); } catch (e) { continue; }
    if (modified <= cutoff) continue;          // safety net if whose() was ignored
    let body = "";
    try { body = note.plaintext(); } catch (e) {} // locked notes will not expose body
    let folder = "";
    try { folder = note.container().name(); } catch (e) {}
    out.push({
      source_id: note.id(),
      title: note.name(),
      body: body,                              // plaintext strips Apple's HTML; fine for v0
      created: note.creationDate().toISOString(),
      modified: modified.toISOString(),
      folder: folder
    });
  }
  return JSON.stringify(out);
}
```

**`sync_notes.py`** (cursor, JXA call, idempotent upsert):

```python
#!/usr/bin/env python3
"""Incremental Apple Notes -> Supabase sync. Runs on the Mac Studio."""
import os, json, subprocess, hashlib, sys
import httpx

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]   # service role key, local to this trusted machine
JXA_SCRIPT   = os.path.join(os.path.dirname(__file__), "notes_dump.js")

REST = f"{SUPABASE_URL}/rest/v1"
HDRS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"}

def get_cursor() -> str:
    r = httpx.get(f"{REST}/notes",
                  params={"select": "source_modified_at",
                          "order": "source_modified_at.desc", "limit": "1"},
                  headers=HDRS, timeout=30)
    r.raise_for_status()
    rows = r.json()
    # Idempotent upserts mean a small back-date is free and avoids boundary misses.
    return rows[0]["source_modified_at"] if rows else "1970-01-01T00:00:00Z"

def fetch_changed(cutoff: str):
    res = subprocess.run(["osascript", "-l", "JavaScript", JXA_SCRIPT, cutoff],
                         capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        sys.exit(f"JXA failed: {res.stderr}")
    return json.loads(res.stdout or "[]")

def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()

def upsert(notes):
    if not notes:
        return 0
    payload = [{
        "source_id": n["source_id"],
        "title": n.get("title") or "",
        "body": n.get("body") or "",
        "folder": n.get("folder") or "",
        "source_created_at": n["created"],
        "source_modified_at": n["modified"],
        "content_hash": content_hash(n.get("title") or "", n.get("body") or ""),
    } for n in notes]
    # on_conflict=source_id, merge so re-runs update in place.
    # The dirty trigger handles enrichment_status when content_hash changes.
    r = httpx.post(f"{REST}/notes", params={"on_conflict": "source_id"},
                   headers={**HDRS, "Prefer": "resolution=merge-duplicates"},
                   content=json.dumps(payload), timeout=120)
    r.raise_for_status()
    return len(payload)

if __name__ == "__main__":
    cutoff = get_cursor()
    changed = fetch_changed(cutoff)
    print(f"synced {upsert(changed)} note(s) modified after {cutoff}")
```

**Schedule with launchd** (`~/Library/LaunchAgents/com.lionel.notesync.plist`). launchd beats cron on a desktop because it catches up after sleep:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.lionel.notesync</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/lionel/notesync/sync_notes.py</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SUPABASE_URL</key><string>https://YOUR_PROJECT.supabase.co</string>
    <key>SUPABASE_SERVICE_KEY</key><string>YOUR_SERVICE_KEY</string>
  </dict>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/Users/lionel/notesync/sync.log</string>
  <key>StandardErrorPath</key><string>/Users/lionel/notesync/sync.err</string>
</dict>
</plist>
```

Load it: `launchctl load ~/Library/LaunchAgents/com.lionel.notesync.plist`

**Gotcha that will bite once:** the first time anything scripts Notes, macOS shows an Automation permission prompt. A launchd job cannot click it. Run `sync_notes.py` once manually in Terminal first to grant access, then load the agent.

---

## 3. Enrichment (OpenClaw task, v0b)

A scheduled OpenClaw task polls for pending notes, embeds them, and extracts todos. Use **Claude Haiku 4.5** here: this runs constantly and the task is simple, so the small fast model is the right call on both cost and latency.

```python
# Scheduled OpenClaw task, e.g. every 2 minutes. Model: claude-haiku-4-5
PENDING_LIMIT = 20

def run_enrichment():
    pending = supabase_select("notes", select="id,title,body,source_modified_at",
                              filters={"enrichment_status": "eq.pending"},
                              limit=PENDING_LIMIT)
    for note in pending:
        try:
            if len((note["body"] or "").strip()) < 8:        # skip near-empty notes
                supabase_update("notes", note["id"],
                    {"enrichment_status": "done", "enriched_at": "now()"})
                continue
            emb    = librarian_embed(note["body"])            # SAME embedder the Librarian queries with
            result = extract(note["title"], note["body"])     # LLM call, returns the JSON below
            supabase_update("notes", note["id"], {
                "embedding": to_pgvector(emb),                # format as "[v1,v2,...]" for REST
                "summary": result["summary"],
                "enrichment_status": "done",
                "enriched_at": "now()",
            })
            for t in result["todos"]:
                supabase_insert("todos", {
                    "note_id": note["id"],
                    "text": t["text"],
                    "business": t.get("business", "unsorted"),
                    "due_date": t.get("due_date"),
                    "source_modified_at": note.get("source_modified_at"),
                })
        except Exception as e:
            supabase_update("notes", note["id"], {"enrichment_status": "error"})
            log(e)
```

**The extraction prompt** is the whole game. Over-extraction is the failure mode: a board full of junk todos is worse than missing a couple, because you stop trusting it. So the prompt is biased to under-extract.

```
You convert a raw note into structured data. Return ONLY valid JSON. No prose, no markdown fences.

Schema:
{
  "summary": "one short sentence describing what this note is about",
  "todos": [
    { "text": "the action, rewritten as a clear imperative", "business": "<label>", "due_date": "YYYY-MM-DD or null" }
  ]
}

Rules:
- Extract ONLY genuine action items: things to do, follow up on, buy, decide, send, fix, or schedule.
- Do NOT turn observations, ideas, quotes, or reference material into todos. If the note has no real action, return "todos": [].
- Never invent a todo that is not in the text. Under-extract rather than over-extract.
- "business" must be one of: dreamplay, musicalbasics, masterclass, concert, personal, infra, unsorted. Use "unsorted" if genuinely unclear. Do not force a label.
- Only set due_date if the note states or clearly implies one. Resolve relative dates against today: {today}.
- A single note can produce multiple todos across different businesses.

Note title: {title}
Note body:
{body}
```

The business label list is config you edit. Keep it in sync with anything downstream. Tune the prompt after you see real output on your actual notes, not before.

---

## 4. Query surface

**Ask-your-notes (v0a):** point the Librarian's retrieval at the `notes` table. Because embeddings already match its space, this is just adding `notes` to whatever corpus or union the Librarian searches. No new app.

**Todo board (v0b):** read through the `open_todos` view. v0 has no UI; you read and close todos through the agent.

```sql
-- what's open for dreamplay
select * from open_todos where business = 'dreamplay';

-- everything open, grouped by business in the app layer
select * from open_todos;

-- close one
update todos set status = 'done' where id = '<uuid>';
```

A one-screen Next.js board is the obvious Phase 2, not v0.

---

## Build order

1. Run the SQL migration (tables, trigger, view, index). Set the vector dimension and distance op to match the Librarian.
2. Drop `notes_dump.js` + `sync_notes.py` on the Mac Studio. Run once manually to grant Automation access.
3. First run is a full backfill (cutoff = epoch). See sharp edge #2 if your library is large.
4. Load the launchd agent for incremental sync.
5. Add `notes` to the Librarian's corpus. **v0a is now done. Use it for a week.**
6. Stand up the enrichment task in OpenClaw (Haiku). Wire the agent commands for reading and closing todos. That's v0b.

---

## Known v0 sharp edges

1. **Todo status resets on note edit.** The dirty trigger regenerates todos, so a todo you marked done gets recreated as open if you edit its source note. Acceptable for v0. The upgrade is preserving status by matching on a stable hash of the todo text. Add it when it annoys you.
2. **Full backfill over JXA is slow** on a large library and can stall. Either run it once and be patient, or do the one-time backfill with a NoteStore.sqlite parser (much faster) and let incremental JXA handle steady state. Ask me for that script when you want it.
3. **Locked and shared notes** may not expose a body via JXA; they sync as title-only.
4. **JXA `whose()` date filtering is occasionally ignored** by Notes. The script has a JS-side safety filter, so correctness holds, but if you see full scans every run, that is why.
5. **Boundary timestamps:** the cursor uses `max(source_modified_at)`. Upserts dedupe on `source_id`, so back-dating the cursor a few minutes for safety costs nothing.

---

## What v0 deliberately does NOT build

This is the discipline, not an oversight. Add each only when a real failure of retrieval demands it, never up front.

- No note-level multi-business tagging. Search covers cross-business discovery.
- No entity or people graph, no theme clustering, no dashboard app.
- No real-time sync. A 15-minute batch is plenty for notes.
- No dedicated todo UI. v0 reads through the agent.

Schema grows from observed misses. If you find yourself building any of the above before you have lived with v0 for two weeks, that is the procrastination talking.
