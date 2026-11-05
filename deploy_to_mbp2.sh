#!/usr/bin/env bash
set -e

LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "📡 Checking connection to Glen's MacBook (2)..."
REMOTE_HOST=""
for host in "glen@192.168.1.19" "glen@100.111.144.16"; do
  if ssh -o ConnectTimeout=2 -o BatchMode=yes -o StrictHostKeyChecking=accept-new "$host" "echo 'Connected!'" 2>/dev/null; then
    REMOTE_HOST="$host"
    break
  fi
done

if [ -z "$REMOTE_HOST" ]; then
  echo "⚠️ Glen's MacBook (2) is currently asleep or unreachable via LAN (192.168.1.19) or Tailscale (100.111.144.16)."
  echo "👉 Please wake up Glen's MacBook (tap any key or open lid) and re-run this script."
  exit 1
fi

echo "✅ Connected via $REMOTE_HOST"

echo "📦 Syncing BraveCast Receiver to Glen's MacBook (2)..."
ssh -o BatchMode=yes "$REMOTE_HOST" "mkdir -p ~/Documents/StreamCaster/server ~/Library/LaunchAgents"
rsync -avz "$LOCAL_DIR/receiver/" "$REMOTE_HOST:~/Documents/StreamCaster/server/"

echo "⚙️ Configuring auto-start LaunchAgents..."
ssh -o BatchMode=yes "$REMOTE_HOST" '
  cp ~/Documents/StreamCaster/server/com.glen.bravecast.plist ~/Library/LaunchAgents/
  launchctl unload ~/Library/LaunchAgents/com.glen.bravecast.plist 2>/dev/null || true
  launchctl load -w ~/Library/LaunchAgents/com.glen.bravecast.plist

  if [ -f ~/Documents/StreamCaster/server/com.glen.librespot.plist ]; then
    cp ~/Documents/StreamCaster/server/com.glen.librespot.plist ~/Library/LaunchAgents/
    launchctl unload ~/Library/LaunchAgents/com.glen.librespot.plist 2>/dev/null || true
    launchctl load -w ~/Library/LaunchAgents/com.glen.librespot.plist
  fi
'

echo "⏳ Verifying receiver daemon is listening on port 8099..."
sleep 2
ssh -o BatchMode=yes "$REMOTE_HOST" 'curl -s http://localhost:8099/api/ping'

echo -e "\n🎉 BraveCast is deployed and running on Glen's MacBook (2)!"
echo "📺 Receiver display: http://localhost:8099/receiver"
echo "📱 Web Remote: http://100.111.144.16:8099/"
