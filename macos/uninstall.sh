#!/usr/bin/env bash
# BraveCast - macOS Uninstaller
set -euo pipefail

LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
INSTALL_DIR="$HOME/Library/Application Support/BraveCast"

for plist in com.glen.bravecast.plist com.glen.librespot.plist; do
  if [ -f "$LAUNCH_AGENTS/$plist" ]; then
    launchctl unload "$LAUNCH_AGENTS/$plist" 2>/dev/null || true
    rm -f "$LAUNCH_AGENTS/$plist"
    echo "Removed $plist"
  fi
done

if [ -d "$INSTALL_DIR" ]; then
  rm -rf "$INSTALL_DIR"
  echo "Removed $INSTALL_DIR"
fi

echo "✅ BraveCast has been uninstalled."
