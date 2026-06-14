#!/bin/bash
# Serve the dashboard + Supabase proxy on http://localhost:8787.
# proxy.py keeps the Supabase secret key server-side (browsers can't use secret keys).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${1:-8787}"
( sleep 1; command -v open >/dev/null && open "http://localhost:$PORT" ) &
exec python3 proxy.py "$PORT"
