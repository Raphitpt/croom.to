#!/bin/bash
#
# Croom installer for macOS (Mac mini as a meeting room computer).
#
# Run as the user account the room logs into automatically, not as root:
#   ./packaging/macos/install.sh [--no-chrome] [--power-settings]
#
# Installs Croom in its own virtualenv and starts it at login with a
# LaunchAgent (a LaunchDaemon cannot open the meeting browser: it has no
# access to the graphical session).

set -euo pipefail

LABEL="to.croom.agent"
SUPPORT_DIR="$HOME/Library/Application Support/Croom"
VENV_DIR="$SUPPORT_DIR/venv"
CONFIG_DIR="$HOME/.config/croom"
CONFIG_FILE="$CONFIG_DIR/config.yaml"
LOG_DIR="$HOME/Library/Logs/Croom"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Install from this checkout, or from CROOM_SOURCE (any pip requirement)
CROOM_SOURCE="${CROOM_SOURCE:-$REPO_DIR}"

INSTALL_CHROME=1
POWER_SETTINGS=0
for arg in "$@"; do
    case "$arg" in
        --no-chrome) INSTALL_CHROME=0 ;;
        --power-settings) POWER_SETTINGS=1 ;;
        -h|--help) sed -n '3,12p' "$0"; exit 0 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

log() { echo "==> $*"; }
fail() { echo "Error: $*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS; use installer/install.sh on Linux"
[ "$(id -u)" -ne 0 ] || fail "run as the room's user account, not root"
command -v brew >/dev/null || fail "Homebrew is required: https://brew.sh"

log "Installing dependencies with Homebrew"
brew install python@3.12 android-platform-tools
if [ "$INSTALL_CHROME" -eq 1 ] && [ ! -d "/Applications/Google Chrome.app" ]; then
    brew install --cask google-chrome
fi
PYTHON="$(brew --prefix python@3.12)/bin/python3.12"

log "Installing Croom in $VENV_DIR"
mkdir -p "$SUPPORT_DIR" "$LOG_DIR" "$CONFIG_DIR"
"$PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
if [ -d "$CROOM_SOURCE" ]; then
    "$VENV_DIR/bin/pip" install --quiet "$CROOM_SOURCE[calendar]"
else
    "$VENV_DIR/bin/pip" install --quiet "$CROOM_SOURCE"
fi
if [ "$INSTALL_CHROME" -eq 0 ]; then
    # No Google Chrome: meetings use Playwright's Chromium
    "$VENV_DIR/bin/playwright" install chromium
fi

if [ -f "$CONFIG_FILE" ]; then
    log "Keeping existing configuration $CONFIG_FILE"
else
    log "Writing configuration $CONFIG_FILE"
    cp "$SCRIPT_DIR/config.example.yaml" "$CONFIG_FILE"
    if [ "$INSTALL_CHROME" -eq 0 ]; then
        sed -i '' 's/^  browser_channel: chrome/  browser_channel: ""/' "$CONFIG_FILE"
    fi
fi

log "Installing LaunchAgent $PLIST"
mkdir -p "$(dirname "$PLIST")"
sed -e "s|@LABEL@|$LABEL|g" \
    -e "s|@CROOM@|$VENV_DIR/bin/croom|g" \
    -e "s|@CONFIG@|$CONFIG_FILE|g" \
    -e "s|@LOG_DIR@|$LOG_DIR|g" \
    -e "s|@BREW_PREFIX@|$(brew --prefix)|g" \
    "$SCRIPT_DIR/$LABEL.plist.in" > "$PLIST"
plutil -lint "$PLIST" >/dev/null

if [ "$POWER_SETTINGS" -eq 1 ]; then
    log "Room power settings (asks for your password)"
    # Never sleep, keep the HDMI signal on, restart after a power cut
    sudo pmset -a sleep 0 displaysleep 0 disksleep 0 autorestart 1 womp 1
fi

log "Starting Croom"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

cat <<EOF

Croom is installed and starts at login. Next steps:

  1. Edit $CONFIG_FILE (room name, Fire TV address, calendar credentials)
     then restart: launchctl kickstart -k gui/$(id -u)/$LABEL
  2. Sign the room's Google account in once:
       launchctl bootout gui/$(id -u)/$LABEL
       "$VENV_DIR/bin/croom-login"
       launchctl bootstrap gui/$(id -u) "$PLIST"
  3. Allow camera and microphone the first time macOS asks.
  4. System Settings > Users & Groups > Automatic login: this account
     (not available while FileVault is on).

Logs: $LOG_DIR
EOF
