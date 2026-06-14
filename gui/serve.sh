#!/bin/bash
# Serve the dashboard on http://localhost:8787 (avoids file:// fetch quirks).
# Opening index.html directly mostly works, but serving over http is more reliable
# for browser fetch + loading config.local.js.
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8787}"
echo "Notes dashboard -> http://localhost:$PORT"
echo "Ctrl-C to stop."
# Open the browser (macOS) after a short delay, then serve.
( sleep 1; command -v open >/dev/null && open "http://localhost:$PORT" ) &
exec python3 -m http.server "$PORT"
