#!/usr/bin/env python3
"""v0b enrichment pass — REFERENCE IMPLEMENTATION.

Polls Supabase for notes with enrichment_status='pending', then for each:
  1. Embeds the body with the SAME embedder the Librarian queries with.
  2. Calls Claude Haiku 4.5 to extract a one-line summary + genuine todos.
  3. Writes embedding + summary back, inserts todos, marks the note done.

The spec runs this as a scheduled OpenClaw task (every ~2 min). This file is the
portable logic so it can also run from launchd/cron if you prefer. It is gated on
two things you must wire to YOUR stack before enabling:

  - librarian_embed(): call the Librarian's exact embedding model. The dimension
    and metric MUST match sql/schema.sql or cross-corpus search returns garbage.
  - ANTHROPIC_API_KEY in .env for the extraction call.

Until librarian_embed() is implemented this script refuses to run, by design —
a wrong embedder silently corrupts search, which is worse than not running.
"""
import os
import sys
import json
import urllib.request
import urllib.error
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROMPT_PATH = os.path.join(HERE, "prompt.txt")
PENDING_LIMIT = 20
EXTRACT_MODEL = "claude-haiku-4-5"   # constant, simple task -> small fast model


def load_dotenv():
    path = os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_dotenv()
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]
REST = f"{SUPABASE_URL}/rest/v1"
HDRS = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"}


def _req(method, path, body=None, prefer=None):
    headers = dict(HDRS)
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{REST}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# WIRE THIS to the Librarian's exact embedder before enabling enrichment.
def librarian_embed(text: str):
    raise NotImplementedError(
        "librarian_embed() must call the Librarian's exact embedding model. "
        "A mismatched embedder silently corrupts cross-corpus search. "
        "See README 'The one make-or-break detail'."
    )


def to_pgvector(vec) -> str:
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def extract(title: str, body: str) -> dict:
    """Claude Haiku 4.5 extraction. Returns {"summary": str, "todos": [...]}."""
    import anthropic  # pip install anthropic
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = (open(PROMPT_PATH, encoding="utf-8").read()
              .replace("{today}", date.today().isoformat())
              .replace("{title}", title or "")
              .replace("{body}", body or ""))
    msg = client.messages.create(
        model=EXTRACT_MODEL, max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json").strip()
    return json.loads(text)


def run_enrichment():
    pending = _req("GET",
                   "/notes?select=id,title,body,source_modified_at"
                   f"&enrichment_status=eq.pending&limit={PENDING_LIMIT}")
    for note in pending or []:
        try:
            if len((note["body"] or "").strip()) < 8:          # skip near-empty notes
                _req("PATCH", f"/notes?id=eq.{note['id']}",
                     {"enrichment_status": "done", "enriched_at": "now()"},
                     prefer="return=minimal")
                continue
            emb = librarian_embed(note["body"])
            result = extract(note["title"], note["body"])
            _req("PATCH", f"/notes?id=eq.{note['id']}", {
                "embedding": to_pgvector(emb),
                "summary": result["summary"],
                "enrichment_status": "done",
                "enriched_at": "now()",
            }, prefer="return=minimal")
            for t in result.get("todos", []):
                _req("POST", "/todos", {
                    "note_id": note["id"],
                    "text": t["text"],
                    "business": t.get("business", "unsorted"),
                    "due_date": t.get("due_date"),
                    "source_modified_at": note.get("source_modified_at"),
                }, prefer="return=minimal")
        except Exception as e:
            _req("PATCH", f"/notes?id=eq.{note['id']}",
                 {"enrichment_status": "error"}, prefer="return=minimal")
            print(f"error on note {note['id']}: {e}", file=sys.stderr)


if __name__ == "__main__":
    run_enrichment()
