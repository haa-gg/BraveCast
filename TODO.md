# BraveCast To-Do List

## Streamable Apps & Sites

- [x] **1. `movy.sx` Support**
  - **How `movy.sx` works**: Next.js SPA serving `/movie/{tmdbId}?play=true` and `/tv/{tmdbId}[/{season}/{episode}]?play=true`. Resolves streams via two-step obfuscated `api.wecollege.net/seed` + `/{server}/sources` (`mvm1`-encrypted JSON), plays **fMP4 HLS** (`index-*.m3u8` + `init-*.mp4` + `seg-*.m4s`, no DRM) via `hls.js` + `MediaSource` (`blob:` URL) directly inside `#vp-shell video` (no video `<iframe>`), and uses `db.wecollege.net/3/search/multi` for catalog search.
  - **Implemented in BraveCast**:
    - Added **Movy.sx** to Streamable Apps & Search Source toggle in the Web Remote (`receiver/remote.html`, `/api/movy`, `/api/movy/search`).
    - Added `normalize_movy_url()` in `receiver/server.py` to auto-append `?play=true` to `movy.sx` `/movie/...` and `/tv/...` URLs for instant unmuted playback in Brave Kiosk.
    - Updated Web Cinema autopilot (`receiver/autoplay.py`) to target `#vp-shell video`, keep `#vp-shell` above subtitles, ignore background `youtube-nocookie.com` trailer iframes, and support `fullscreen` + TV `next` episode controls.
    - Updated StreamCaster (`extension/background.js`, `extension/popup.js`) to filter out `movy.sx` `/videos/intro.mp4` and fMP4 `init-*.mp4` segments so only real `.m3u8` streams appear.

- [x] **2. Amazon Video (`amazon.com` / Prime Video) Support**
  - **How Amazon Video works**: Renders directly in the main `amazon.com` DOM (no `<iframe>`) inside `div#dv-web-player.dv-player-fullscreen` (`ATVWebPlayerSDK`). Streams **Widevine DRM-encrypted MPEG-DASH** (`_corrected.mpd` + separated `*_video_*.mp4` / `*_audio_*.mp4` chunks via `GetVodPlaybackResources` / `GetWidevineLicense`) into a `blob:` MSE `<video>`. Free-with-ads titles (60s pre-roll ad) and `?autoplay=trailer` play signed-out; Prime-only titles require signing in once inside the persistent `Brave-Kiosk` profile. Detail pages `/gp/video/detail/<ID>?autoplay=1` auto-launch playback.
  - **Implemented in BraveCast**:
    - Added `ensure_widevine_opted_in()` in `receiver/server.py` to set `brave.widevine_opted_in = true` in `Brave-Kiosk/Local State` before launching Web Cinema so Widevine DRM works without a hidden kiosk prompt.
    - Added `normalize_amazon_url()` in `receiver/server.py` to append `?autoplay=1` to `amazon.*` / `primevideo.com` detail URLs, plus `amazon_search()`, `GET /api/amazon/search`, and `POST /api/amazon`.
    - Added **Prime Video** (`📦 Prime Video`) to Streamable Apps and the Search Source toggle in `receiver/remote.html`.
    - Updated Web Cinema autopilot (`receiver/autoplay.py`) to target `#dv-web-player video`, preserve `ATVWebPlayerSDK` overlay/subtitles, wire `#atvwebplayersdk-fullscreen-toggle-button` / `#atvwebplayersdk-next-episode-button`, and guard Jam Queue auto-advance against short `< 180s` pre-roll ads on Amazon.
    - Updated StreamCaster (`extension/background.js`, `extension/popup.js`) to skip Widevine-encrypted Amazon DASH `.mpd` / `_video_*.mp4` / `_audio_*.mp4` chunks and prompt 1-click **Cast Prime Video Page to TV** instead.
