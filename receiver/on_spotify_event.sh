#!/usr/bin/env bash
# Wake TV/display when Spotify starts playing
if [ "$PLAYER_EVENT" = "start" ] || [ "$PLAYER_EVENT" = "play" ]; then
    /usr/bin/caffeinate -u -t 4
    /usr/bin/curl -s -X POST http://localhost:8099/api/receiver/status \
        -H "Content-Type: application/json" \
        -d "{\"state\": \"playing\", \"stream\": {\"title\": \"Spotify: Playing Music\", \"url\": \"spotify\"}}" >/dev/null 2>&1 || true
elif [ "$PLAYER_EVENT" = "stop" ]; then
    /usr/bin/curl -s -X POST http://localhost:8099/api/receiver/status \
        -H "Content-Type: application/json" \
        -d "{\"state\": \"idle\", \"stream\": null}" >/dev/null 2>&1 || true
fi
