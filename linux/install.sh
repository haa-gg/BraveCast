#!/usr/bin/env bash
# BraveCast - Linux / Raspberry Pi installer
#
#   bash linux/install.sh                    # core install
#   bash linux/install.sh --install-brave    # also install Brave via Brave's official apt script
#   bash linux/install.sh --autologin        # (Raspberry Pi OS) boot straight to the desktop
#
# Run as your normal user (it uses sudo where needed), NOT as root.
set -euo pipefail

INSTALL_BRAVE=0
AUTOLOGIN=0
for arg in "$@"; do
  case "$arg" in
    --install-brave) INSTALL_BRAVE=1 ;;
    --autologin) AUTOLOGIN=1 ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done

if [ "$(id -u)" -eq 0 ]; then
  echo "Please run as your normal user, not root. The receiver must run in YOUR desktop session."
  exit 1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$REPO/receiver/server.py"
[ -f "$SERVER" ] || { echo "Cannot find $SERVER"; exit 1; }

IS_PI=0
if [ -r /proc/device-tree/model ] && grep -aq "Raspberry Pi" /proc/device-tree/model; then
  IS_PI=1
  echo "Detected: $(tr -d '\0' < /proc/device-tree/model)"
fi
ARCH="$(uname -m)"

# --- Packages -----------------------------------------------------------------
echo "==> Installing dependencies"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq
  # Installed one by one so a package missing on your distro doesn't abort the rest.
  #   python3, curl   : the receiver itself
  #   cec-utils       : HDMI-CEC (wake TV + switch its input) - Raspberry Pi has real CEC hardware
  #   x11-xserver-utils / xdotool / wlr-randr : wake the screen and focus the window (X11 / Wayland)
  for pkg in python3 curl cec-utils x11-xserver-utils xdotool wlr-randr; do
    sudo apt-get install -y -qq "$pkg" >/dev/null 2>&1 && echo "   ok   $pkg" || echo "   skip $pkg (not available)"
  done
else
  echo "   (not a Debian-based system: install python3, curl and optionally cec-utils / xdotool / wlr-randr yourself)"
fi
command -v python3 >/dev/null || { echo "python3 is required."; exit 1; }

# --- Brave --------------------------------------------------------------------
if ! command -v brave-browser >/dev/null 2>&1 && ! command -v brave >/dev/null 2>&1; then
  if [ "$INSTALL_BRAVE" -eq 1 ]; then
    echo "==> Installing Brave (official script from brave.com)"
    curl -fsS https://dl.brave.com/install.sh | sh
  else
    echo "WARNING: Brave is not installed. Re-run with --install-brave, or see https://brave.com/linux/"
    case "$ARCH" in
      armv7l|armv6l) echo "         32-bit Raspberry Pi OS has no Brave build. Install the 64-bit OS, or BraveCast will fall back to Chromium (no ad blocking)." ;;
    esac
  fi
fi

# --- Keep the screen on / boot to desktop (Raspberry Pi OS) ---------------------
if command -v raspi-config >/dev/null 2>&1; then
  echo "==> Disabling screen blanking"
  sudo raspi-config nonint do_blanking 1 || echo "   (could not change blanking)"
  if [ "$AUTOLOGIN" -eq 1 ]; then
    echo "==> Enabling desktop auto-login"
    sudo raspi-config nonint do_boot_behaviour B4 || echo "   (could not change boot behaviour)"
  else
    echo "   TIP: the Pi must auto-login to the desktop to show video. Re-run with --autologin to set that."
  fi
fi

# --- Firewall (only if ufw is active) -----------------------------------------
if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "==> Opening firewall (ufw): 8099/tcp, 1900/udp"
  sudo ufw allow 8099/tcp >/dev/null
  sudo ufw allow 1900/udp >/dev/null
fi

# --- systemd user service -----------------------------------------------------
echo "==> Installing systemd user service"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=$XDG_RUNTIME_DIR/bus}"

mkdir -p "$HOME/.config/systemd/user"
cat > "$HOME/.config/systemd/user/bravecast.service" << UNIT
[Unit]
Description=BraveCast receiver (DIAL + Brave kiosk)
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=$(command -v python3) $SERVER
WorkingDirectory=$REPO/receiver
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
UNIT

# Linger = start at boot without anyone logging in; the receiver finds the desktop session when a cast arrives
sudo loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now bravecast.service

sleep 3
if curl -fsS http://localhost:8099/api/ping >/dev/null 2>&1; then
  IP="$(curl -fsS http://localhost:8099/api/ping | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["name"], d["localIp"])')"
  echo
  echo "BraveCast is running: ${IP%% *}... -> http://${IP##* }:8099/"
  echo "Logs: journalctl --user -u bravecast -f"
else
  echo "Receiver didn't answer on :8099. Check: journalctl --user -u bravecast -n 50"
fi
