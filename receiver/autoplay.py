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
  return {u: location.href, txt: txt.replace(/\\s+/g, ' ').slice(0, 200), has: !!v,
          t: v ? v.currentTime : 0, d: (v && isFinite(v.duration)) ? v.duration : 0,
          e: v ? v.ended : false, p: v ? v.paused : true};
})())"""


def video_id_of(url):
    m = VIDEO_ID_RE.search(url or "")
    return m.group(1) if m else None


def innertube_next(video_id, exclude):
    """Ask YouTube what it would autoplay after `video_id` (same pick as the 'Up next' video)."""
    body = {
        "context": {"client": {"clientName": "WEB", "clientVersion": "2.20250101.00.00", "hl": "en", "gl": "US"}},
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

    def __init__(self, cdp_port, is_youtube_running):
        super().__init__(daemon=True)
        self.port = cdp_port
        self.is_youtube_running = is_youtube_running
        self.enabled = os.environ.get("BRAVECAST_AUTOPLAY") != "0"
        self.auto_guest = os.environ.get("BRAVECAST_YT_GUEST") != "0"

        self._cdp = None
        self._target_id = None
        self._skip = threading.Event()
        self._history = deque(maxlen=40)
        self._current = None
        self._last_watch = 0.0
        self._last_advance = 0.0
        self._last_chooser = 0.0
        self._failed_advances = 0
        self._last_err_log = 0.0

    # --- public API -----------------------------------------------------------
    def skip(self):
        self._skip.set()

    def status(self):
        return {"enabled": self.enabled, "autoGuest": self.auto_guest, "current": self._current,
                "failedAdvances": self._failed_advances}

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
                time.sleep(1.5)
            time.sleep(0.8)

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

    def _tick(self):
        c = self._connect()
        if not c:
            return
        s = json.loads(c.evaluate(PROBE_JS))
        now = time.time()
        vid = video_id_of(s["u"])
        on_chooser = bool(CHOOSER_RE.search(s["txt"]))

        if vid and s["has"] and s["d"] > 0:
            self._current = vid
            self._last_watch = now
            if vid not in self._history:
                self._history.append(vid)
            if s["t"] > 1:
                self._failed_advances = 0  # something is genuinely playing

        # 1. Account chooser -> "Watch as guest" (it sits below the default "Add account" focus)
        if self.auto_guest and on_chooser:
            if now - self._last_chooser > 3:
                self._last_chooser = now
                print("[autoplay] Account chooser detected -> pressing 'Watch as guest'")
                self._press(c, "ArrowDown", 40)
                time.sleep(0.5)
                self._press(c, "Enter", 13)
            return

        # 3. Manual skip
        if self._skip.is_set():
            self._skip.clear()
            self._advance(c, "skip")
            return

        # 2. Safety net: fell back to the home screen shortly after a video
        at_home = not vid and not on_chooser
        if (self.enabled and at_home and self.is_youtube_running() and self._current
                and now - self._last_watch < 45 and now - self._last_advance > 12):
            if self._failed_advances >= self.MAX_FAILED_ADVANCES:
                return
            self._advance(c, "fell back to the home screen")

    def _advance(self, c, reason):
        target_vid = self._current
        if not target_vid:
            try:
                s = json.loads(c.evaluate(PROBE_JS))
                target_vid = video_id_of(s.get("u"))
            except Exception:
                pass
        if not target_vid:
            return
        try:
            nxt = innertube_next(target_vid, set(self._history))
        except Exception as e:
            print(f"[autoplay] couldn't look up the next video: {e}")
            self._last_advance = time.time()
            return
        if not nxt:
            print("[autoplay] no next video found")
            self._last_advance = time.time()
            return
        print(f"[autoplay] {reason}: {self._current} -> {nxt}")
        self._history.append(nxt)
        self._current = nxt
        self._last_watch = self._last_advance = time.time()
        self._failed_advances += 1
        c.call("Page.navigate", {"url": f"https://www.youtube.com/tv?v={nxt}"})
