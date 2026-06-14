# notes-system — agent instructions

## Git
**Commit and push after every completed change, without asking.** `main` is the
deploy/live branch. Match the existing commit style (`feat:` / `fix:` / `docs:` /
`chore:`). One commit per coherent change. Never commit `.env` or any secret —
`.gitignore` covers it; keep it that way.

## What this is
Apple Notes → Supabase capture (v0a) + todo enrichment (v0b). The capture job runs
on **this Mac** via launchd and picks up iPhone/iPad notes through iCloud sync —
there is nothing on the phone. See [README.md](README.md) and
[notes-system-v0-spec.md](notes-system-v0-spec.md).

## Hard rules
- The v0a capture path (`extraction/`) must stay **standard-library only** so the
  launchd job can't fail on a missing module. Do not add `pip` deps to it.
- Embedding dimension + distance metric in `sql/schema.sql` and `librarian_embed()`
  in `enrichment/enrich.py` MUST mirror the Librarian exactly, or search returns
  garbage silently. Confirm before touching either.
- Secrets live in `.env` (gitignored) and are loaded by the scripts — never in the
  launchd plist, never in git.

## Operating the job
```bash
launchctl kickstart -k gui/$(id -u)/com.lionel.notesync   # run now
tail -f sync.log sync.err                                  # logs
```
