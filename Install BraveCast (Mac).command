#!/usr/bin/env bash
# Double-click this file on macOS to launch the graphical BraveCast installer.
DIR="$(cd "$(dirname "$0")" && pwd)"
exec bash "$DIR/macos/install.sh" --gui
