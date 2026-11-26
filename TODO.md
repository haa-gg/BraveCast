# BraveCast To-Do List

## Streamable Apps & Sites

- [x] **1. `movy.sx` Support**
  - **How `movy.sx` works**: Next.js SPA serving `/movie/{tmdbId}?play=true` and `/tv/{tmdbId}[/{season}/{episode}]?play=true`. Resolves streams via two-step obfuscated `api.wecollege.net/seed` + `/{server}/sources` (`mvm1`-encrypted JSON), plays **fMP4 HLS** (`index-*.m3u8` + `init-*.mp4` + `seg-*.m4s`, no DRM) via `hls.js` + `MediaSource` (`blob:` URL) directly inside `#vp-shell video` (no video `<iframe>`), and uses `db.wecollege.net/3/search/multi` for catalog search.
  - **Implemented in BraveCast**:
    - Added **Movy.sx** to Streamable Apps & Search Source toggle in the Web Remote (`receiver/remote.html`, `/api/movy`, `/api/movy/search`).
    - Added `normalize_movy_url()` in `receiver/server.py` to auto-append `?play=true` to `movy.sx` `/movie/...` and `/tv/...` URLs for instant unmuted playback in Brave Kiosk.
    - Updated Web Cinema autopilot (`receiver/autoplay.py`) to target `#vp-shell video`, keep `#vp-shell` above subtitles, ignore background `youtube-nocookie.com` trailer iframes, and support `fullscreen` + TV `next` episode controls.
    - Updated StreamCaster (`extension/background.js`, `extension/popup.js`) to filter out `movy.sx` `/videos/intro.mp4` and fMP4 `init-*.mp4` segments so only real `.m3u8` streams appear.

- [ ] **2. Amazon Video (`amazon.com` / Prime Video) Support**
  - Investigate how Amazon Prime Video playback works in Brave Kiosk (Widevine DRM support, persistent signed-in profile vs dedicated kiosk profile, playback controls via CDP, and Web Remote / StreamCaster integration).
  - Add Amazon Video to the list of streamable apps in BraveCast if feasible.
