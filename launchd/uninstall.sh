#!/bin/bash
# Stop and remove the Notes sync launchd agent.
set -euo pipefail
LABEL="com.lionel.notesync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed agent $LABEL and $PLIST"
