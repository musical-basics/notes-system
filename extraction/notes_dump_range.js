#!/usr/bin/env osascript -l JavaScript
// Apple Notes -> JSON for a bounded index slice [start, start+count).
// Used only by the one-time backfill (backfill.py). Bounding by index guarantees
// each Apple Events call processes a fixed number of notes, so it can't blow the
// ~2-minute AppleEvent timeout the way a full/dense scan does.
// Usage: osascript -l JavaScript notes_dump_range.js <start> <count>
function run(argv) {
  const start = parseInt(argv[0], 10) || 0;
  const count = parseInt(argv[1], 10) || 200;
  const Notes = Application("Notes");
  const all = Notes.notes;
  const total = all.length;
  const end = Math.min(start + count, total);
  const out = [];
  for (let i = start; i < end; i++) {
    const note = all[i];
    try {
      let body = "";
      try { body = note.plaintext(); } catch (e) {}
      let folder = "";
      try { folder = note.container().name(); } catch (e) {}
      let created, modified;
      try { modified = note.modificationDate().toISOString(); } catch (e) { continue; }
      try { created = note.creationDate().toISOString(); } catch (e) { created = modified; }
      out.push({
        source_id: note.id(),
        title: note.name(),
        body: body,
        created: created,
        modified: modified,
        folder: folder
      });
    } catch (e) { /* skip unreadable note */ }
  }
  return JSON.stringify({ total: total, start: start, end: end, notes: out });
}
