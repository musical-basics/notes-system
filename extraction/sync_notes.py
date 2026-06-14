#!/usr/bin/env python3
"""Incremental Apple Notes -> Supabase sync. Runs on the Mac (this machine).

Zero third-party dependencies: uses only the Python standard library so the
launchd job never breaks on a missing module or a wrong interpreter's site-packages.

Flow:
  1. Read the high-water cursor (max source_modified_at) back from Supabase.
  2. Ask Apple Notes (via JXA) for everything modified after that cursor.
  3. Idempotent upsert on source_id. The DB's dirty trigger handles re-enrichment.
"""
import os
import sys
import json
import hashlib
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
JXA_SCRIPT = os.path.join(HERE, "notes_dump.js")


def load_dotenv():
    """Minimal .env loader: KEY=VALUE lines, repo root or extraction/ dir.
    Does not override variables already present in the environment (launchd may set them)."""
    for path in (os.path.join(HERE, ".env"), os.path.join(os.path.dirname(HERE), ".env")):
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key, val = key.strip(), val.strip().strip('"').strip("'")
                os.environ.setdefault(key, val)


load_dotenv()

try:
    SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
    SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]  # service role key, local to this trusted machine
except KeyError as e:
    sys.exit(f"Missing required env var {e}. Copy .env.example to .env and fill it in.")

BACKDATE_MIN = int(os.environ.get("CURSOR_BACKDATE_MINUTES", "5"))
REST = f"{SUPABASE_URL}/rest/v1"
HDRS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}


def _request(method, url, headers, body=None):
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Supabase {method} {url} -> {e.code}: {e.read().decode('utf-8', 'replace')}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Supabase {method} {url} unreachable: {e.reason}")


def get_cursor() -> str:
    url = f"{REST}/notes?select=source_modified_at&order=source_modified_at.desc&limit=1"
    _, raw = _request("GET", url, HDRS)
    rows = json.loads(raw or "[]")
    if not rows or not rows[0].get("source_modified_at"):
        return "1970-01-01T00:00:00Z"
    # Back-date a few minutes for boundary safety; idempotent upserts make this free.
    ts = rows[0]["source_modified_at"]
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) - timedelta(minutes=BACKDATE_MIN)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return ts


def fetch_changed(cutoff: str):
    res = subprocess.run(
        ["osascript", "-l", "JavaScript", JXA_SCRIPT, cutoff],
        capture_output=True, text=True, timeout=1800,
    )
    if res.returncode != 0:
        sys.exit(f"JXA failed: {res.stderr.strip()}")
    return json.loads(res.stdout or "[]")


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()


UPSERT_CHUNK = 200   # keep each POST small so a full backfill can't time out on one giant body


def upsert(notes) -> int:
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
    url = f"{REST}/notes?on_conflict=source_id"
    headers = {**HDRS, "Prefer": "resolution=merge-duplicates,return=minimal"}
    for i in range(0, len(payload), UPSERT_CHUNK):
        _request("POST", url, headers, json.dumps(payload[i:i + UPSERT_CHUNK]))
    return len(payload)


def main():
    cutoff = get_cursor()
    changed = fetch_changed(cutoff)
    count = upsert(changed)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] synced {count} note(s) modified after {cutoff}", flush=True)


if __name__ == "__main__":
    main()
