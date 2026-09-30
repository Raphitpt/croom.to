#!/bin/bash
#
# Remove Croom from macOS. Keeps the configuration and the signed-in
# browser profile unless --purge is given.

set -euo pipefail

LABEL="to.croom.agent"
SUPPORT_DIR="$HOME/Library/Application Support/Croom"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$SUPPORT_DIR/venv"

if [ "${1:-}" = "--purge" ]; then
    rm -rf "$SUPPORT_DIR" "$HOME/.config/croom" "$HOME/Library/Logs/Croom"
    echo "Croom removed, including configuration, browser profile and logs."
else
    echo "Croom removed. Kept: ~/.config/croom, $SUPPORT_DIR/browser (use --purge to delete)."
fi
