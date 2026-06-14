#!/usr/bin/env python3
"""One-time full backfill of the entire Apple Notes library -> Supabase.

Why this exists: a single JXA pass over a large library (thousands of notes) blows
the ~2-minute AppleEvent timeout (-1712). This pages through the collection by INDEX
in fixed-size chunks, so each osascript call processes a bounded number of notes and
always finishes. Idempotent (upsert on source_id) and resumable: pass --start to
continue from a given index after an interruption.

Steady-state incremental sync is handled separately by sync_notes.py + launchd; run
this once to seed history, then never again.

Notes.notes is ordered modification-descending, so index 0 is the most recently
modified note. That means `--limit 100` backfills exactly the 100 most recent notes.

Usage:
  python3 extraction/backfill.py [--chunk 150] [--start 0] [--limit N]
"""
import os
import sys
import json
import time
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
RANGE_JS = os.path.join(HERE, "notes_dump_range.js")

# Reuse the verified upsert + env loading from sync_notes.py
import importlib.util
_spec = importlib.util.spec_from_file_location("syncmod", os.path.join(HERE, "sync_notes.py"))
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def dump_range(start: int, count: int) -> dict:
    res = subprocess.run(
        ["osascript", "-l", "JavaScript", RANGE_JS, str(start), str(count)],
        capture_output=True, text=True, timeout=600,
    )
    if res.returncode != 0:
        raise RuntimeError(f"JXA range [{start},{start+count}) failed: {res.stderr.strip()}")
    return json.loads(res.stdout or "{}")


def main():
    chunk = 150
    start = 0
    limit = None   # max notes to process (from --start); None = whole library
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--chunk":
            chunk = int(args[i + 1])
        elif a == "--start":
            start = int(args[i + 1])
        elif a == "--limit":
            limit = int(args[i + 1])

    stop_at = (start + limit) if limit is not None else None
    total = None
    done = 0
    t0 = time.time()
    while True:
        if stop_at is not None:
            chunk = min(chunk, stop_at - start)
            if chunk <= 0:
                break
        try:
            res = dump_range(start, chunk)
        except RuntimeError as e:
            # A chunk failed (rare). Halve and retry once; if it still fails, report and stop.
            print(f"  chunk [{start},{start+chunk}) failed: {e}\n  retrying at half size...", flush=True)
            half = max(25, chunk // 2)
            res = dump_range(start, half)
            chunk = half

        if total is None:
            total = res.get("total", 0)
            print(f"library has {total} notes; backfilling in chunks of {chunk} from index {start}", flush=True)

        notes = res.get("notes", [])
        end = res.get("end", start + chunk)
        n = sync.upsert(notes)
        done += n
        elapsed = time.time() - t0
        print(f"  [{start}->{end}/{total}] upserted {n} (running {done}) — {elapsed:.0f}s", flush=True)

        if end >= total or (stop_at is not None and end >= stop_at):
            break
        start = end

    print(f"BACKFILL DONE — {done} notes upserted in {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
