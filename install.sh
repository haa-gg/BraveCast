#!/usr/bin/env bash
# Universal BraveCast Installer (macOS & Linux / Raspberry Pi)
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"

case "$OS" in
  Darwin)
    exec bash "$DIR/macos/install.sh" "$@"
    ;;
  Linux)
    exec bash "$DIR/linux/install.sh" "$@"
    ;;
  *)
    echo "On Windows, double-click 'Install BraveCast (Windows).bat' or run: powershell -ExecutionPolicy Bypass -File windows\\install.ps1"
    exit 1
    ;;
esac
