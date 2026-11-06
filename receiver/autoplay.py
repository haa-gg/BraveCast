"""
YouTube "doomscroll" autopilot.

Watches the kiosk's YouTube TV page over the DevTools port and keeps it playing:

  1. Account chooser  - a fresh YouTube TV load in a signed-out profile stops at a
     "Who's watching? / Watch as guest" screen and the video never starts (this is what
     looked like "it just goes to the home screen"). We press "Watch as guest" for you.
  2. Autoplay safety net - YouTube TV normally counts down "NEXT IN 5..." and plays the next
     video by itself. If the page instead falls back to the home screen right after a video,
     we ask YouTube what it would have autoplayed next and launch that.
  3. Skip - `skip()` jumps to the next video on demand.

Disable pieces with BRAVECAST_AUTOPLAY=0 (safety net) or BRAVECAST_YT_GUEST=0 (chooser).
"""

import json
import os
import re
import threading
import time
import urllib.request
from collections import deque

try:
    from cdp import CDPClient, list_targets
except ImportError:
    from receiver.cdp import CDPClient, list_targets

VIDEO_ID_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")
CHOOSER_RE = re.compile(r"watch as guest", re.I)

PROBE_JS = """JSON.stringify((function(){
  var v = document.querySelector('video');
  var txt = document.body ? (document.body.innerText || '') : '';
  var vid = null;
  var title = '';
  try {
    var p = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
    if (p && typeof p.getVideoData === 'function') {
      var vd = p.getVideoData();
      if (vd && vd.video_id) vid = vd.video_id;
      if (vd && vd.title) title = vd.title;
    }
  } catch (e) {}
  var hash = location.hash || '';
  if (!vid) {
    var hm = hash.match(/[?&]v=([A-Za-z0-9_-]{11})/);
    if (hm) vid = hm[1];
  }
  if (!vid) {
    try {
      var entries = performance.getEntriesByType('resource');
      for (var i = entries.length - 1; i >= 0; i--) {
        var name = entries[i].name || '';
        if (name.indexOf('/api/stats/playback') !== -1 || name.indexOf('/api/stats/watchtime') !== -1) {
          var dm = name.match(/[?&]docid=([A-Za-z0-9_-]{11})/);
          if (dm) { vid = dm[1]; break; }
        }
      }
    } catch (e) {}
  }
  var onHomeHash = (hash === '#/' || hash.indexOf('#/browse') === 0 || hash.indexOf('#/surface') === 0);
  var searchVid = null;
  var sm = (location.search || '').match(/[?&]v=([A-Za-z0-9_-]{11})/);
  if (sm) searchVid = sm[1];
  if (!vid && !onHomeHash && searchVid) {
    vid = searchVid;
  }
  var vw = 0;
  try { vw = v ? v.getBoundingClientRect().width : 0; } catch (e) {}
  return {
    u: location.href,
    hash: hash,
    vid: vid,
    searchVid: searchVid,
    title: title,
    onHomeHash: onHomeHash,
    txt: txt.replace(/\\s+/g, ' ').slice(0, 300),
    has: !!v,
    vw: vw,
    t: v ? v.currentTime : 0,
    d: (v && isFinite(v.duration)) ? v.duration : 0,
    e: v ? v.ended : false,
    p: v ? v.paused : true
  };
})())"""


def video_id_of(url):
    m = VIDEO_ID_RE.search(url or "")
    return m.group(1) if m else None


def yt_oembed(video_id):
    """Fetch video title, channel, and thumbnail via YouTube's public oEmbed endpoint (no API key)."""
    if not video_id:
        return {}
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read())
        return {
            "title": data.get("title") or f"YouTube ({video_id})",
            "channel": data.get("author_name") or "",
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        }
    except Exception:
        return {
            "title": f"YouTube ({video_id})",
            "channel": "",
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        }


_CACHED_CLIENT_VER = None
_CACHED_CLIENT_VER_TS = 0.0


def innertube_client_version(force_scrape=False):
    """Return a current YouTube InnerTube WEB clientVersion so requests don't expire over time."""
    global _CACHED_CLIENT_VER, _CACHED_CLIENT_VER_TS
    now = time.time()
    if _CACHED_CLIENT_VER and not force_scrape and (now - _CACHED_CLIENT_VER_TS < 86400):
        return _CACHED_CLIENT_VER
    if force_scrape:
        try:
            req = urllib.request.Request(
                "https://www.youtube.com/",
                headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                html = r.read(250000).decode("utf-8", errors="ignore")
            m = re.search(r'"INNERTUBE_CLIENT_VERSION"\s*:\s*"([^"]+)"', html)
            if m:
                _CACHED_CLIENT_VER = m.group(1)
                _CACHED_CLIENT_VER_TS = now
                return _CACHED_CLIENT_VER
        except Exception:
            pass
    _CACHED_CLIENT_VER = f"2.{time.strftime('%Y%m%d')}.00.00"
    _CACHED_CLIENT_VER_TS = now
    return _CACHED_CLIENT_VER


def yt_search(query, limit=6):
    """Search YouTube via InnerTube (no API key required) for the Web Remote Jam search bar."""
    if not query or not query.strip():
        return []
    body = {
        "context": {
            "client": {"clientName": "WEB", "clientVersion": innertube_client_version(), "hl": "en", "gl": "US"}
        },
        "query": query.strip(),
    }
    req = urllib.request.Request(
        "https://www.youtube.com/youtubei/v1/search?prettyPrint=false",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://www.youtube.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())

    results = []
    seen = set()

    def walk(node):
        if len(results) >= limit:
            return
        if isinstance(node, dict):
            vr = node.get("videoRenderer")
            if isinstance(vr, dict) and vr.get("videoId"):
                vid = vr["videoId"]
                if vid not in seen:
                    seen.add(vid)
                    title_runs = vr.get("title", {}).get("runs") or []
                    title = "".join(r.get("text", "") for r in title_runs) or vr.get("title", {}).get("simpleText", "")
                    owner_runs = vr.get("ownerText", {}).get("runs") or vr.get("longBylineText", {}).get("runs") or []
                    channel = "".join(r.get("text", "") for r in owner_runs)
                    dur = vr.get("lengthText", {}).get("simpleText", "")
                    results.append({
                        "videoId": vid,
                        "title": title or f"YouTube ({vid})",
                        "channel": channel,
                        "duration": dur,
                        "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                        "url": f"https://www.youtube.com/tv?v={vid}",
                    })
                return
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return results


def innertube_next(video_id, exclude):
    """Ask YouTube what it would autoplay after `video_id` (same pick as the 'Up next' video)."""
    exclude = set(exclude or ()) | {video_id}
    body = {
        "context": {"client": {"clientName": "WEB", "clientVersion": innertube_client_version(), "hl": "en", "gl": "US"}},
        "videoId": video_id,
    }
    req = urllib.request.Request(
        "https://www.youtube.com/youtubei/v1/next?prettyPrint=false",
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Origin": "https://www.youtube.com",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        },
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read())

    try:
        picked = data["contents"]["twoColumnWatchNextResults"]["autoplay"]["autoplay"]["sets"][0][
            "autoplayVideo"]["watchEndpoint"]["videoId"]
        if picked and picked not in exclude:
            return picked
    except (KeyError, IndexError, TypeError):
        pass

    # Layout changed or the autoplay pick was already played: take any other suggested video
    for m in re.finditer(r'"(?:videoId|contentId)":\s*"([A-Za-z0-9_-]{11})"', json.dumps(data)):
        if m.group(1) not in exclude:
            return m.group(1)
    return None


class YouTubeAutopilot(threading.Thread):
    MAX_FAILED_ADVANCES = 5   # give up if this many "next" launches in a row never start playing

    def __init__(self, cdp_port, is_youtube_running, on_player_state=None, pop_jam_next=None, has_jam_items=None):
        super().__init__(daemon=True)
        self.port = cdp_port
        self.is_youtube_running = is_youtube_running
        self.on_player_state = on_player_state
        self.pop_jam_next = pop_jam_next
        self.has_jam_items = has_jam_items or (lambda: False)
        self.enabled = os.environ.get("BRAVECAST_AUTOPLAY") != "0"
        self.auto_guest = os.environ.get("BRAVECAST_YT_GUEST") != "0"

        self._cdp = None
        self._target_id = None
        self._skip = threading.Event()
        self._wake = threading.Event()
        self._next_video_override = None
        self._cmd_queue = deque(maxlen=10)
        self._history = deque(maxlen=40)
        self._current = None
        self._pending_vid = None
        self._played_current = False
        self._max_t = 0.0
        self._ended_since = None
        self._idle_since = None
        self._last_watch = 0.0
        self._last_advance = 0.0
        self._last_chooser = 0.0
        self._chooser_dismissed_at = 0.0
        self._last_unpause = 0.0
        self._failed_advances = 0
        self._last_err_log = 0.0

    # --- public API -----------------------------------------------------------
    def skip(self):
        self._skip.set()
        self._wake.set()

    def play_video(self, video_id):
        """Immediately navigate the existing YouTube TV tab to `video_id` without killing Brave."""
        if not video_id:
            return
        self._next_video_override = video_id
        self._skip.set()
        self._wake.set()

    def is_actively_playing(self):
        """True if YouTube TV is open in the kiosk and currently playing or loading a video."""
        now = time.time()
        if not self.page_alive():
            return False
        if self._played_current and (now - self._last_watch < 15.0):
            return True
        if self._pending_vid and (now - self._last_advance < 15.0):
            return True
        return False

    def notify_video(self, video_id):
        """Record a video ID launched externally (e.g. via DIAL cast)."""
        if video_id:
            self._current = video_id
            self._pending_vid = video_id
            self._played_current = False
            self._max_t = 0.0
            self._ended_since = None
            self._idle_since = None
            self._last_advance = time.time()
            if video_id not in self._history:
                self._history.append(video_id)

    def control(self, action, value=None):
        """Queue a playback control command (toggle, play, pause, seek, mute, volume) for YouTube TV."""
        self._cmd_queue.append((action, value))
        self._wake.set()

    def status(self):
        return {
            "enabled": self.enabled,
            "autoGuest": self.auto_guest,
            "current": self._current,
            "playedCurrent": self._played_current,
            "failedAdvances": self._failed_advances,
        }

    def page_alive(self):
        """True if a YouTube TV page is currently open in the kiosk."""
        try:
            return any(self._is_youtube_target(t) for t in list_targets(self.port))
        except Exception:
            return False

    # --- loop -----------------------------------------------------------------
    def run(self):
        while True:
            try:
                self._tick()
            except Exception as e:
                self._drop()
                if time.time() - self._last_err_log > 60 and "Connection refused" not in str(e):
                    print(f"[autoplay] {type(e).__name__}: {e}")
                    self._last_err_log = time.time()
                self._wake.wait(1.5)
                self._wake.clear()
                continue
            self._wake.wait(0.8)
            self._wake.clear()

    @staticmethod
    def _is_youtube_target(t):
        return t.get("type") == "page" and "youtube.com/tv" in t.get("url", "")

    def _drop(self):
        if self._cdp:
            self._cdp.close()
        self._cdp, self._target_id = None, None

    def _connect(self):
        targets = [t for t in list_targets(self.port) if self._is_youtube_target(t)]
        if not targets:
            self._drop()
            return None
        target = targets[0]
        if self._cdp and self._target_id == target["id"]:
            return self._cdp
        self._drop()
        self._cdp = CDPClient(target["webSocketDebuggerUrl"], timeout=8)
        self._target_id = target["id"]
        return self._cdp

    def _press(self, c, key, vk):
        for typ in ("keyDown", "keyUp"):
            c.call("Input.dispatchKeyEvent", {"type": typ, "key": key, "code": key,
                                              "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk})
            time.sleep(0.08)

    def _run_queued_controls(self, c):
        while self._cmd_queue:
            action, value = self._cmd_queue.popleft()
            try:
                if action == "toggle":
                    c.evaluate("(function(){ var v = document.querySelector('video'); if (v) { v.paused ? v.play() : v.pause(); } })()")
                elif action == "play":
                    c.evaluate("(function(){ var v = document.querySelector('video'); if (v) v.play(); })()")
                elif action == "pause":
                    c.evaluate("(function(){ var v = document.querySelector('video'); if (v) v.pause(); })()")
                elif action == "seek":
                    if value == "back":
                        c.evaluate("(function(){ var v = document.querySelector('video'); if (v) v.currentTime = Math.max(0, v.currentTime - 10); })()")
                    elif value == "fwd":
                        c.evaluate("(function(){ var v = document.querySelector('video'); if (v) v.currentTime = Math.min(v.duration || v.currentTime + 30, v.currentTime + 30); })()")
                    elif isinstance(value, (int, float)):
                        c.evaluate(f"(function(){{ var v = document.querySelector('video'); if (v) v.currentTime = {float(value)}; }})()")
                elif action == "mute":
                    c.evaluate("(function(){ var v = document.querySelector('video'); if (v) v.muted = !v.muted; })()")
                elif action == "volume" and isinstance(value, (int, float)):
                    vol = max(0.0, min(1.0, float(value)))
                    c.evaluate(f"(function(){{ var v = document.querySelector('video'); if (v) {{ v.volume = {vol}; v.muted = ({vol} === 0); }} }})()")
            except Exception as e:
                print(f"[autoplay] control '{action}' failed: {e}")

    def _tick(self):
        c = self._connect()
        if not c:
            return
        self._run_queued_controls(c)
        s = json.loads(c.evaluate(PROBE_JS))
        now = time.time()
        on_home_hash = bool(s.get("onHomeHash"))
        search_vid = s.get("searchVid")
        vid = s.get("vid") or (search_vid if not on_home_hash else None)
        on_chooser = bool(CHOOSER_RE.search(s.get("txt", "")))

        d = float(s.get("d") or 0.0)
        t = float(s.get("t") or 0.0)
        ended = bool(s.get("e"))
        paused = bool(s.get("p"))
        vw = float(s.get("vw") or 0.0)
        has_video = bool(s.get("has") and d > 0 and not on_home_hash and (vw > 50 or not paused))

        if search_vid and not self._pending_vid and not has_video:
            self._pending_vid = search_vid
        if vid and self._current is None:
            self._current = vid

        is_old_finished = ended or (paused and d > 5.0 and (d - t) <= 3.0)
        if vid and has_video and not (vid != self._current and is_old_finished and now - self._last_advance < 8.0):
            if vid != self._current:
                self._current = vid
                self._played_current = False
                self._max_t = 0.0
                self._ended_since = None
                self._idle_since = None
            if t >= 0.5:
                self._played_current = True
                self._pending_vid = None
                self._chooser_dismissed_at = 0.0
                if t > self._max_t:
                    self._max_t = t
            if t > 1.5 and not paused:
                self._failed_advances = 0  # something is genuinely playing
            if vid not in self._history:
                self._history.append(vid)

        reached_near_end = has_video and self._played_current and (d <= 5.0 or (d - max(t, self._max_t)) <= 3.0)
        at_end = has_video and (ended or (paused and reached_near_end))

        if has_video and not at_end:
            self._ended_since = None
            self._idle_since = None
            if not paused:
                self._last_watch = now
            elif t < 1.5 and not on_chooser and (now - self._last_advance < 25 or self._pending_vid) and (now - self._last_unpause > 3.0):
                self._last_unpause = now
                c.evaluate("(function(){ var v = document.querySelector('video'); if (v && v.paused) v.play(); })()")

        if self.on_player_state and has_video:
            title = (s.get("title") or "").strip()
            display_title = f"YouTube: {title}" if title and not title.lower().startswith("youtube") else (title or "YouTube on TV (Ad-Free)")
            self.on_player_state({
                "state": "paused" if paused else "playing",
                "currentTime": t,
                "duration": d,
                "youtubeRunning": True,
                "stream": {
                    "url": f"https://www.youtube.com/tv?v={self._current or vid}",
                    "title": display_title,
                    "source": "youtube",
                },
            })

        # 1. Account chooser -> "Watch as guest" (it sits below the default "Add account" focus)
        if self.auto_guest and on_chooser:
            if search_vid:
                self._pending_vid = search_vid
            if now - self._last_chooser > 3:
                self._last_chooser = now
                self._chooser_dismissed_at = now
                print("[autoplay] Account chooser detected -> pressing 'Watch as guest'")
                self._press(c, "ArrowDown", 40)
                time.sleep(0.5)
                self._press(c, "Enter", 13)
            return

        # 1b. If clicking "Watch as guest" landed on the home screen instead of starting the requested video, launch it
        if (self._chooser_dismissed_at and self._pending_vid and not on_chooser and not has_video
                and 4.0 < now - self._chooser_dismissed_at < 25.0):
            vid_to_open = self._pending_vid
            self._chooser_dismissed_at = 0.0
            self._last_advance = now
            print(f"[autoplay] Resuming requested video after guest chooser: {vid_to_open}")
            c.call("Page.navigate", {"url": f"https://www.youtube.com/tv?v={vid_to_open}"})
            return

        # 3. Manual skip or direct play_video override
        if self._skip.is_set():
            self._skip.clear()
            self._advance(c, "skip", s)
            return

        # 2. Jam Queue + Autoplay safety net: either the video ended, or the page dropped back to home
        has_jam = bool(self.has_jam_items())
        if (not self.enabled and not has_jam) or not self._current or now - self._last_advance <= (4 if has_jam else 10):
            return
        if self._failed_advances >= self.MAX_FAILED_ADVANCES and not has_jam:
            return

        if at_end:
            if self._ended_since is None:
                self._ended_since = now
            # If a song is waiting in the Jam Queue, advance after 0.6s (before YouTube TV's 5s random countdown fires!)
            wait_sec = 0.6 if has_jam else 6.0
            if now - self._ended_since >= wait_sec:
                self._advance(c, "jam queue next" if has_jam else "video ended", s)
            return

        if not has_video and not on_chooser and self._played_current and (now - self._last_watch < 60):
            if self._idle_since is None:
                self._idle_since = now
            wait_sec = 1.2 if has_jam else 2.5
            if now - self._idle_since >= wait_sec:
                self._advance(c, "jam queue next" if has_jam else "fell back to the home screen", s)

    def _advance(self, c, reason, probe_state=None):
        nxt = None
        if self._next_video_override:
            nxt = self._next_video_override
            self._next_video_override = None
            self._failed_advances = 0
            reason = "play_video"
        elif self.pop_jam_next:
            try:
                jam_item = self.pop_jam_next()
            except Exception as e:
                print(f"[autoplay] jam pop failed: {e}")
                jam_item = None
            if jam_item:
                if jam_item.get("handled_externally"):
                    self._last_advance = time.time()
                    return
                nxt = jam_item.get("videoId")
                if nxt:
                    self._failed_advances = 0
                    reason = f"jam ({jam_item.get('addedBy', 'Guest')})"

        target_vid = self._current or self._pending_vid
        if not nxt:
            if not self.enabled and reason != "skip":
                return
            if not target_vid and probe_state:
                target_vid = probe_state.get("vid") or probe_state.get("searchVid") or video_id_of(probe_state.get("u"))
            if not target_vid:
                try:
                    s = json.loads(c.evaluate(PROBE_JS))
                    target_vid = s.get("vid") or s.get("searchVid") or video_id_of(s.get("u"))
                except Exception:
                    pass
            if not target_vid:
                return
            try:
                nxt = innertube_next(target_vid, set(self._history) | {target_vid})
            except Exception as e:
                print(f"[autoplay] couldn't look up the next video: {e}")
                self._last_advance = time.time()
                return
            if not nxt:
                print("[autoplay] no next video found")
                self._last_advance = time.time()
                return
        print(f"[autoplay] {reason}: {target_vid} -> {nxt}")
        if nxt not in self._history:
            self._history.append(nxt)
        self._current = nxt
        self._pending_vid = nxt
        self._played_current = False
        self._max_t = 0.0
        self._ended_since = None
        self._idle_since = None
        self._last_watch = self._last_advance = time.time()
        self._failed_advances += 1
        c.call("Page.navigate", {"url": f"https://www.youtube.com/tv?v={nxt}"})
