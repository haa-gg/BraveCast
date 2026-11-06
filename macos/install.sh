#!/usr/bin/env bash
# BraveCast - macOS Installer (Supports both GUI Double-Click and Terminal CLI)
#
#   GUI mode (Finder double-click):  bash macos/install.sh --gui
#   Terminal mode:                   bash macos/install.sh [--name "Bedroom TV"] [--install-brave] [--install-spotify]

set -euo pipefail

GUI_MODE=0
INSTALL_BRAVE=0
INSTALL_SPOTIFY=0
TV_NAME="${BRAVECAST_NAME:-}"

while [ $# -gt 0 ]; do
  case "$1" in
    --gui) GUI_MODE=1; shift ;;
    --install-brave) INSTALL_BRAVE=1; shift ;;
    --install-spotify) INSTALL_SPOTIFY=1; shift ;;
    --name) TV_NAME="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_RECEIVER="$REPO/receiver"
INSTALL_DIR="$HOME/Library/Application Support/BraveCast/receiver"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs"

gui_alert() {
  local title="$1"
  local msg="$2"
  osascript -e "display dialog $(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))') with title $(printf '%s' "$title" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))') buttons {\"OK\"} default button \"OK\" with icon note" >/dev/null 2>&1 || true
}

gui_confirm() {
  local title="$1"
  local msg="$2"
  local ok_label="${3:-Install}"
  osascript -e "display dialog $(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))') with title $(printf '%s' "$title" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))') buttons {\"Cancel\", \"$ok_label\"} default button \"$ok_label\" with icon note" >/dev/null 2>&1
}

gui_notify() {
  local title="$1"
  local msg="$2"
  osascript -e "display notification $(printf '%s' "$msg" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))') with title $(printf '%s' "$title" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" >/dev/null 2>&1 || true
}

# 1. Ask for TV Name in GUI mode
if [ "$GUI_MODE" -eq 1 ] && [ -z "$TV_NAME" ]; then
  TV_NAME="$(osascript -e '
    try
      set d to display dialog "Welcome to BraveCast! 🦁📺\n\nThis will set up your Mac as an ad-free YouTube & media receiver with BraveCast Jam.\n\nWhat would you like to name this TV?" default answer "Living Room TV (Mac)" with title "Install BraveCast" buttons {"Cancel", "Install 🚀"} default button "Install 🚀"
      return text returned of d
    on error
      return "__CANCELLED__"
    end try
  ' 2>/dev/null || echo "__CANCELLED__")"

  if [ "$TV_NAME" = "__CANCELLED__" ]; then
    exit 0
  fi
fi

TV_NAME="${TV_NAME:-Living Room TV (Mac)}"

# 2. Check / Install Brave Browser
BRAVE_APP="/Applications/Brave Browser.app"
if [ ! -d "$BRAVE_APP" ] && [ ! -d "$HOME/Applications/Brave Browser.app" ]; then
  if [ "$GUI_MODE" -eq 1 ]; then
    if gui_confirm "Brave Browser Required" "Brave Browser was not found in /Applications.\n\nBraveCast uses Brave Shields to block 100% of YouTube pre-roll and mid-roll ads.\n\nDownload and install Brave automatically now?" "Download & Install"; then
      INSTALL_BRAVE=1
    fi
  fi

  if [ "$INSTALL_BRAVE" -eq 1 ]; then
    echo "==> Downloading and installing Brave Browser..."
    gui_notify "BraveCast Installer" "Downloading Brave Browser (this takes ~30 seconds)..."
    TMP_DMG="/tmp/Brave-Browser-Installer.dmg"
    curl -fL "https://laptop-updates.brave.com/latest/osx" -o "$TMP_DMG"
    MOUNT_DIR="$(hdiutil attach "$TMP_DMG" -nobrowse -readonly | awk -F'\t' 'END{print $NF}')"
    if [ -n "$MOUNT_DIR" ] && [ -d "$MOUNT_DIR/Brave Browser.app" ]; then
      cp -R "$MOUNT_DIR/Brave Browser.app" /Applications/ 2>/dev/null || cp -R "$MOUNT_DIR/Brave Browser.app" "$HOME/Applications/"
      hdiutil detach "$MOUNT_DIR" -quiet || true
    fi
    rm -f "$TMP_DMG"
  else
    echo "⚠️ Warning: Brave Browser not found in /Applications. Install it from https://brave.com/download/"
  fi
fi

# 3. Copy receiver into ~/Library/Application Support/BraveCast/receiver
echo "==> Installing BraveCast Receiver to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$LAUNCH_AGENTS" "$LOG_DIR"
cp -R "$SRC_RECEIVER/"* "$INSTALL_DIR/"
chmod +x "$INSTALL_DIR/server.py" "$INSTALL_DIR/doctor.py" "$INSTALL_DIR/on_spotify_event.sh" 2>/dev/null || true

# 4. Generate macOS LaunchAgent plist dynamically for this user
PYTHON_BIN="$(command -v python3 || echo /usr/bin/python3)"
PLIST_PATH="$LAUNCH_AGENTS/com.glen.bravecast.plist"

cat > "$PLIST_PATH" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.glen.bravecast</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>$INSTALL_DIR/server.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>BRAVECAST_NAME</key>
        <string>$TV_NAME</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/bravecast.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/bravecast_err.log</string>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load -w "$PLIST_PATH"

# 5. Optional Spotify Connect (librespot) if available or requested
if [ "$INSTALL_SPOTIFY" -eq 1 ] && ! command -v librespot >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "==> Installing librespot via Homebrew..."
    brew install librespot || true
  fi
fi

LIBRESPOT_BIN="$(command -v librespot || true)"
if [ -n "$LIBRESPOT_BIN" ]; then
  echo "==> Configuring Spotify Connect ($LIBRESPOT_BIN)"
  SPOT_PLIST="$LAUNCH_AGENTS/com.glen.librespot.plist"
  cat > "$SPOT_PLIST" << SPLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.glen.librespot</string>
    <key>ProgramArguments</key>
    <array>
        <string>$LIBRESPOT_BIN</string>
        <string>--name</string>
        <string>$TV_NAME</string>
        <string>--backend</string>
        <string>rodio</string>
        <string>--enable-volume-normalisation</string>
        <string>--initial-volume</string>
        <string>100</string>
        <string>--onevent</string>
        <string>$INSTALL_DIR/on_spotify_event.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/librespot.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/librespot_err.log</string>
</dict>
</plist>
SPLIST
  launchctl unload "$SPOT_PLIST" 2>/dev/null || true
  launchctl load -w "$SPOT_PLIST"
fi

# 6. Verify Receiver is Live
echo "==> Waiting for BraveCast daemon on port 8099..."
sleep 2
if curl -fsS http://localhost:8099/api/ping >/dev/null 2>&1; then
  LOCAL_IP="$(curl -fsS http://localhost:8099/api/ping | "$PYTHON_BIN" -c 'import sys,json; print(json.load(sys.stdin).get("localIp","localhost"))')"
  REMOTE_URL="http://$LOCAL_IP:8099/"
  echo "🎉 BraveCast is running!"
  echo "📺 Cast Target Name : $TV_NAME"
  echo "📱 Web Jam Remote   : $REMOTE_URL"

  if [ "$GUI_MODE" -eq 1 ]; then
    open "$REMOTE_URL" >/dev/null 2>&1 || true
    gui_alert "BraveCast Installed! 🎉" "✅ BraveCast is now running in the background and will start automatically whenever your Mac turns on.\n\n📺 Cast Name: $TV_NAME\n📱 Phone Jam Remote: $REMOTE_URL\n\nOpen YouTube on your phone and tap the Cast icon, or open the Web Remote link above to start a Jam!"
  fi
else
  echo "⚠️ Receiver did not respond on port 8099. Check $LOG_DIR/bravecast_err.log"
  if [ "$GUI_MODE" -eq 1 ]; then
    gui_alert "BraveCast Setup Warning" "The service was installed, but port 8099 did not respond yet. Check ~/Library/Logs/bravecast_err.log for details."
  fi
fi
