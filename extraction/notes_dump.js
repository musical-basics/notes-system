#!/usr/bin/env osascript -l JavaScript
// Apple Notes -> JSON. Returns notes modified strictly after a cutoff.
// Usage: osascript -l JavaScript notes_dump.js "2026-06-01T00:00:00Z"
//
// iCloud syncs notes created on iPhone/iPad into the Mac's Notes app, so
// scraping here on the Mac captures every device's notes.
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
    try { body = note.plaintext(); } catch (e) {} // locked notes won't expose body
    let folder = "";
    try { folder = note.container().name(); } catch (e) {}
    let created;
    try { created = note.creationDate().toISOString(); } catch (e) { created = modified.toISOString(); }
    out.push({
      source_id: note.id(),
      title: note.name(),
      body: body,                              // plaintext strips Apple's HTML; fine for v0
      created: created,
      modified: modified.toISOString(),
      folder: folder
    });
  }
  return JSON.stringify(out);
}
