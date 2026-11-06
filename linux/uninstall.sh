#!/usr/bin/env bash
# Removes the BraveCast systemd user service. Firewall rules / packages are left alone.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"
systemctl --user disable --now bravecast.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/bravecast.service"
systemctl --user daemon-reload
echo "BraveCast removed. (To also undo auto-start at boot: sudo loginctl disable-linger $USER)"
