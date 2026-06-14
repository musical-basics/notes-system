#!/bin/bash
# Generate + load the launchd agent that runs the Notes sync every 15 minutes.
# Re-run this any time you move the repo or change the interval.
#
# launchd (not cron) because it catches up missed runs after the Mac sleeps.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SYNC="$REPO/extraction/sync_notes.py"
PY="$(command -v python3)"
LABEL="com.lionel.notesync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
INTERVAL="${1:-900}"   # seconds; default 15 min. Pass an arg to override.

if [ ! -f "$REPO/.env" ]; then
  echo "⚠️  $REPO/.env not found. Copy .env.example to .env and fill in Supabase creds first." >&2
  echo "    (Generating the agent anyway; it will error until .env exists.)" >&2
fi

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$SYNC</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$REPO/sync.log</string>
  <key>StandardErrorPath</key><string>$REPO/sync.err</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLIST

echo "Wrote $PLIST"
echo "  python : $PY"
echo "  script : $SYNC"
echo "  every  : ${INTERVAL}s"

# Reload cleanly (bootout may fail if not loaded; ignore that).
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "Loaded. It will run now (RunAtLoad) and every ${INTERVAL}s."
echo "Logs: $REPO/sync.log  and  $REPO/sync.err"
echo
echo "Check status : launchctl print gui/$(id -u)/$LABEL | grep -E 'state|last exit'"
echo "Run now      : launchctl kickstart -k gui/$(id -u)/$LABEL"
echo "Uninstall    : ./launchd/uninstall.sh"
