# Windows equivalent of on_spotify_event.sh (librespot --onevent hook)
# Wakes the display on playback and mirrors state to the BraveCast receiver.
$base = "http://localhost:8099"
$evt = $env:PLAYER_EVENT

if ($evt -eq "start" -or $evt -eq "play") {
    try { Invoke-RestMethod -Method Post -Uri "$base/api/wake" | Out-Null } catch {}
    $body = '{"state": "playing", "stream": {"title": "Spotify: Playing Music", "url": "spotify"}}'
    try { Invoke-RestMethod -Method Post -Uri "$base/api/receiver/status" -ContentType "application/json" -Body $body | Out-Null } catch {}
}
elseif ($evt -eq "stop") {
    $body = '{"state": "idle", "stream": null}'
    try { Invoke-RestMethod -Method Post -Uri "$base/api/receiver/status" -ContentType "application/json" -Body $body | Out-Null } catch {}
}
