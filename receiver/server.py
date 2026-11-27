#!/usr/bin/env python3
"""
BraveCast Receiver & DIAL Server for Glen's MacBook (2)
Multi-threaded Python 3 HTTP Server with Dedicated Brave Kiosk Window, DIAL, and Remote Control
"""

import os
import re
import sys
import json
import time
import queue
import socket
import struct
import threading
import uuid
import platform
import subprocess
import platform_support as ps
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, urljoin, quote, urlencode, urlunparse
import urllib.request
try:
    from autoplay import YouTubeAutopilot, yt_oembed, yt_search
    import doctor
    import updater
except ImportError:
    from receiver.autoplay import YouTubeAutopilot, yt_oembed, yt_search
    from receiver import doctor
    from receiver import updater

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    except Exception:
        pass

PORT = int(os.environ.get("BRAVECAST_PORT", 8099))
HOST = "0.0.0.0"
DIR_PATH = os.path.dirname(os.path.abspath(__file__))
_MAC_HEX = f"{uuid.getnode():012x}"
MAC_ADDR = ":".join(_MAC_HEX[i:i + 2] for i in range(0, 12, 2))
DEVICE_UUID = os.environ.get("BRAVECAST_UUID") or f"{_MAC_HEX[:8]}-{_MAC_HEX[8:]}-4000-8000-000000000000"
FRIENDLY_NAME = os.environ.get("BRAVECAST_NAME") or f"Living Room TV ({ps.default_device_label()})"
SMART_TV_UA = "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/5.0 Native TV /0.0.1 TV Safari/538.1"
KIOSK_DIR = ps.kiosk_profile_dir()
CDP_PORT = int(os.environ.get("BRAVECAST_CDP_PORT", 9223))  # localhost-only DevTools port used to steer the kiosk (autoplay)

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # UDP connect sends nothing; it just selects the outbound interface
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip

LOCAL_IP = get_local_ip()

# Global State
class ReceiverState:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = {
            "state": "idle",
            "stream": None,
            "currentTime": 0,
            "duration": 0,
            "volume": 1.0,
            "muted": False,
            "lastUpdate": time.time(),
            "youtubeRunning": False,
            "loginMode": False
        }
        self.sse_queues = []
        self.history = []
        self.jam_queue = []
        self.user_last_played_seq = {}
        self._seq_counter = 0
        self._priority_counter = 0

    def _next_seq_locked(self):
        self._seq_counter += 1
        return self._seq_counter

    def record_user_played(self, added_by):
        user_key = (added_by or "Guest").strip().lower() or "guest"
        with self.lock:
            self.user_last_played_seq[user_key] = self._next_seq_locked()

    def _sort_jam_locked(self):
        if len(self.jam_queue) <= 1:
            return
        pinned = [it for it in self.jam_queue if it.get("priority", 0) > 0]
        pinned.sort(key=lambda it: (-it.get("priority", 0), it.get("seq", 0)))

        standard = [it for it in self.jam_queue if it.get("priority", 0) == 0]
        by_user = {}
        user_first_seq = {}
        for it in standard:
            uk = (it.get("addedBy") or "Guest").strip().lower() or "guest"
            by_user.setdefault(uk, []).append(it)
            if uk not in user_first_seq or it["seq"] < user_first_seq[uk]:
                user_first_seq[uk] = it["seq"]

        round_map = {}
        for uk, items in by_user.items():
            items.sort(key=lambda x: (-x.get("votes", 0), x.get("seq", 0)))
            for r_idx, it in enumerate(items):
                round_map[it["id"]] = (
                    r_idx,
                    self.user_last_played_seq.get(uk, 0),
                    user_first_seq[uk],
                )

        standard.sort(
            key=lambda it: (
                -it.get("votes", 0),
                round_map[it["id"]][0],
                round_map[it["id"]][1],
                round_map[it["id"]][2],
                it.get("seq", 0),
            )
        )
        self.jam_queue = pinned + standard

    def has_jam_items(self):
        with self.lock:
            return len(self.jam_queue) > 0

    def get_jam_queue(self):
        with self.lock:
            return [dict(it) for it in self.jam_queue]

    def add_to_jam(self, item, play_next=False, voter_id=None):
        with self.lock:
            # Deduplicate if the exact videoId or URL is already waiting in the queue
            vid = item.get("videoId")
            url = item.get("url")
            for idx, existing in enumerate(self.jam_queue):
                if (vid and existing.get("videoId") == vid) or (url and existing.get("url") == url):
                    voter = voter_id or item.get("addedBy") or "anon"
                    voters = list(existing.get("voters") or [])
                    if voter not in voters:
                        voters.append(voter)
                        existing["voters"] = voters
                        existing["votes"] = len(voters)
                    if play_next:
                        self._priority_counter += 1
                        existing["priority"] = self._priority_counter
                    self._sort_jam_locked()
                    pos = next((i + 1 for i, x in enumerate(self.jam_queue) if x["id"] == existing["id"]), idx + 1)
                    return dict(existing), pos

            new_item = dict(item)
            new_item["id"] = uuid.uuid4().hex[:8]
            new_item["seq"] = self._next_seq_locked()
            new_item["addedAt"] = int(time.time())
            new_item["addedBy"] = (item.get("addedBy") or "Guest").strip() or "Guest"
            new_item["voters"] = []
            new_item["votes"] = 0
            if play_next:
                self._priority_counter += 1
                new_item["priority"] = self._priority_counter
            else:
                new_item["priority"] = 0

            self.jam_queue.append(new_item)
            self._sort_jam_locked()
            pos = next((i + 1 for i, x in enumerate(self.jam_queue) if x["id"] == new_item["id"]), len(self.jam_queue))
            return dict(new_item), pos

    def pop_next_jam(self):
        with self.lock:
            if not self.jam_queue:
                return None
            item = self.jam_queue.pop(0)
            uk = (item.get("addedBy") or "Guest").strip().lower() or "guest"
            self.user_last_played_seq[uk] = self._next_seq_locked()
            self._sort_jam_locked()
            return dict(item)

    def jam_action(self, action, item_id=None, client_id=None):
        with self.lock:
            if action == "clear":
                self.jam_queue = []
                return None
            target = next((it for it in self.jam_queue if it.get("id") == item_id), None)
            if not target:
                return None
            if action == "remove":
                self.jam_queue = [it for it in self.jam_queue if it.get("id") != item_id]
                self._sort_jam_locked()
                return dict(target)
            if action == "play_now":
                self.jam_queue = [it for it in self.jam_queue if it.get("id") != item_id]
                uk = (target.get("addedBy") or "Guest").strip().lower() or "guest"
                self.user_last_played_seq[uk] = self._next_seq_locked()
                self._sort_jam_locked()
                return dict(target)
            if action == "play_next":
                self._priority_counter += 1
                target["priority"] = self._priority_counter
                self._sort_jam_locked()
                return dict(target)
            if action == "vote":
                voter = (client_id or "anon").strip() or "anon"
                voters = list(target.get("voters") or [])
                if voter in voters:
                    voters.remove(voter)
                else:
                    voters.append(voter)
                target["voters"] = voters
                target["votes"] = len(voters)
                self._sort_jam_locked()
                return dict(target)
            return None

    def update_status(self, data):
        with self.lock:
            for k in ("state", "currentTime", "duration", "volume", "muted", "youtubeRunning", "loginMode"):
                if k in data:
                    self.status[k] = data[k]
            if "stream" in data:
                new_stream = data["stream"]
                old_stream = self.status.get("stream")
                if isinstance(new_stream, dict) and isinstance(old_stream, dict):
                    # Preserve Jam metadata (addedBy, channel, thumbnail) when autopilot updates progress on the same item
                    if old_stream.get("url") == new_stream.get("url"):
                        for meta_k in ("addedBy", "channel", "thumbnail", "videoId"):
                            if meta_k in old_stream and meta_k not in new_stream:
                                new_stream[meta_k] = old_stream[meta_k]
                self.status["stream"] = new_stream
            self.status["lastUpdate"] = time.time()

    def get_status(self):
        with self.lock:
            return dict(self.status)

    def add_history(self, stream):
        with self.lock:
            if not self.history or self.history[0].get("url") != stream.get("url"):
                self.history.insert(0, stream)
                if len(self.history) > 20:
                    self.history.pop()

    def get_history(self):
        with self.lock:
            return list(self.history)

    def register_sse(self):
        q = queue.Queue(maxsize=100)
        with self.lock:
            self.sse_queues.append(q)
        return q

    def unregister_sse(self, q):
        with self.lock:
            if q in self.sse_queues:
                self.sse_queues.remove(q)

    def broadcast(self, event_type, payload):
        msg = f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
        with self.lock:
            dead_queues = []
            for q in self.sse_queues:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead_queues.append(q)
            for dq in dead_queues:
                if dq in self.sse_queues:
                    self.sse_queues.remove(dq)

state_mgr = ReceiverState()

YT_URL_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*?\bv=|tv\?.*?\bv=|shorts/|embed/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)
DIRECT_MEDIA_RE = re.compile(
    r"\.(m3u8|mp4|webm|mkv|mpd|m4v|mov)(\?|$)|/(hls|m3u8|manifest\.mpd|master\.txt|playlist\.txt|index\.txt)\b",
    re.I,
)
M3U8_URI_ATTR_RE = re.compile(r'URI="([^"]+)"')


def rewrite_m3u8_manifest(manifest_text, base_url, referer=""):
    """Rewrite relative and absolute URLs inside an HLS .m3u8 playlist to route through /api/proxy."""
    ref_param = f"&referer={quote(referer, safe='')}" if referer else ""

    def _wrap(target_url):
        resolved = urljoin(base_url, target_url.strip())
        return f"/api/proxy?url={quote(resolved, safe='')}{ref_param}"

    out_lines = []
    for raw_line in manifest_text.splitlines():
        line = raw_line.strip()
        if not line:
            out_lines.append(raw_line)
            continue
        if line.startswith("#"):
            if 'URI="' in line:
                line = M3U8_URI_ATTR_RE.sub(lambda m: f'URI="{_wrap(m.group(1))}"', line)
            out_lines.append(line)
        else:
            out_lines.append(_wrap(line))
    return "\n".join(out_lines)


def wake_tv_and_screen():
    ps.wake_display()


def ensure_widevine_opted_in(profile_dir):
    """Ensure Brave Kiosk's Local State opts into Widevine DRM so Prime Video plays without a hidden prompt."""
    if not profile_dir:
        return
    try:
        os.makedirs(profile_dir, exist_ok=True)
        local_state_path = os.path.join(profile_dir, "Local State")
        data = {}
        if os.path.exists(local_state_path):
            with open(local_state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        brave_cfg = data.get("brave")
        if not isinstance(brave_cfg, dict):
            brave_cfg = {}
            data["brave"] = brave_cfg
        if brave_cfg.get("widevine_opted_in") is True:
            return
        brave_cfg["widevine_opted_in"] = True
        tmp_path = local_state_path + ".bc.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_path, local_state_path)
    except Exception as e:
        print(f"[widevine] could not update Local State: {e}")


def launch_brave_kiosk(url, is_youtube=True, kiosk=True):
    """Launch Brave in dedicated fullscreen kiosk mode (or normal windowed mode when kiosk=False for sign-in)."""
    ps.ensure_display_env()
    wake_tv_and_screen()
    try:
        # 1. Terminate any previous Kiosk instance so flags like --kiosk apply fresh
        ps.kill_kiosk(KIOSK_DIR)
        time.sleep(0.3)
        if not is_youtube:
            ensure_widevine_opted_in(KIOSK_DIR)

        brave_bin = ps.find_browser()
        cmd = [
            brave_bin,
            f"--user-data-dir={KIOSK_DIR}",
            *(["--kiosk", "--start-fullscreen"] if kiosk else []),
            "--start-maximized",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=Translate",
            "--overscroll-history-navigation=0",
            *ps.extra_browser_flags(),
            f"--remote-debugging-port={CDP_PORT}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            f"--app={url}" if kiosk else url
        ]
        if is_youtube:
            cmd.append(f"--user-agent={SMART_TV_UA}")

        subprocess.Popen(cmd)

        def activate_window():
            time.sleep(0.6)
            ps.activate_kiosk(KIOSK_DIR)

        threading.Thread(target=activate_window, daemon=True).start()
    except Exception as e:
        print(f"Error launching Brave Kiosk: {e}")


MOVY_WATCH_PATH_RE = re.compile(r"^/(movie/\d+|tv/\d+(?:/\d+/\d+)?)/?$")


def normalize_movy_url(url):
    """Ensure Movy.sx movie/TV watch URLs include ?play=true so Brave Kiosk auto-launches the player."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host != "movy.sx" and not host.endswith(".movy.sx"):
            return url
        if not MOVY_WATCH_PATH_RE.match(parsed.path or ""):
            return url
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if "play" not in qs:
            qs["play"] = ["true"]
        new_query = urlencode(qs, doseq=True)
        netloc = "www.movy.sx" if host == "movy.sx" else parsed.netloc
        scheme = "https" if parsed.scheme in ("http", "https", "") else parsed.scheme
        return urlunparse((scheme, netloc, parsed.path.rstrip("/"), parsed.params, new_query, ""))
    except Exception:
        return url


def movy_search(query, limit=8):
    """Search Movy.sx catalog via its public TMDB proxy (db.wecollege.net)."""
    if not query or not query.strip():
        return []
    url = f"https://db.wecollege.net/3/search/multi?language=en&page=1&query={quote(query.strip())}"
    req = urllib.request.Request(
        url,
        headers={
            "Origin": "https://www.movy.sx",
            "Referer": "https://www.movy.sx/",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read())

    results = []
    for item in data.get("results") or []:
        mtype = item.get("media_type")
        if mtype not in ("movie", "tv"):
            continue
        tmdb_id = item.get("id")
        if not tmdb_id:
            continue
        title = (item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        date_str = item.get("release_date") or item.get("first_air_date") or ""
        year = date_str[:4] if len(date_str) >= 4 else ""
        vote = item.get("vote_average")
        rating = f"{float(vote):.1f}" if isinstance(vote, (int, float)) and vote > 0 else ""
        poster = item.get("poster_path")
        thumb = (
            f"https://wsrv.nl/?url=https://image.tmdb.org/t/p/w342{poster}&output=webp&q=50"
            if poster else "https://www.movy.sx/no-poster.png"
        )
        results.append({
            "id": tmdb_id,
            "mediaType": mtype,
            "title": title,
            "year": year,
            "rating": rating,
            "thumbnail": thumb,
            "url": f"https://www.movy.sx/{mtype}/{tmdb_id}?play=true",
            "source": "webpage",
        })
        if len(results) >= limit:
            break
    return results


AMAZON_HOST_RE = re.compile(r"^(?:[\w-]+\.)?(?:amazon\.[a-z.]+|primevideo\.com)$", re.I)
AMAZON_DETAIL_PATH_RE = re.compile(r"^(?:/[a-z]{2}(?:-[a-z]{2})?)?(?:/gp/video)?/detail/([A-Z0-9]{10,})(?:/.*)?$", re.I)
AMAZON_SEARCH_LINK_RE = re.compile(r'href="(/gp/video/detail/([A-Z0-9]{10,})[^"]*)"[^>]*?(?:aria-label="([^"]+)"|title="([^"]+)")?', re.I)


def normalize_amazon_url(url):
    """Ensure Amazon / Prime Video detail URLs include ?autoplay=1 so Brave Kiosk opens the player directly."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if not AMAZON_HOST_RE.match(host):
            return url
        if not AMAZON_DETAIL_PATH_RE.match(parsed.path or ""):
            return url
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if "autoplay" not in qs:
            qs["autoplay"] = ["1"]
        new_query = urlencode(qs, doseq=True)
        scheme = "https" if parsed.scheme in ("http", "https", "") else parsed.scheme
        return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, new_query, ""))
    except Exception:
        return url


def amazon_search(query, limit=8):
    """
    Search Amazon Prime Video catalog when HTML results are available,
    falling back to a direct Prime Video TV search link if bot-gated.
    """
    q = (query or "").strip()
    if not q:
        return []
    search_url = f"https://www.amazon.com/gp/video/search?ie=UTF8&phrase={quote(q)}"
    results = []
    try:
        req = urllib.request.Request(
            search_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            html = r.read(350000).decode("utf-8", errors="ignore")
        seen = set()
        for m in AMAZON_SEARCH_LINK_RE.finditer(html):
            asin = m.group(2)
            if asin in seen:
                continue
            title = (m.group(3) or m.group(4) or "").strip()
            if not title or len(title) < 2:
                continue
            seen.add(asin)
            results.append({
                "id": asin,
                "title": title,
                "subtitle": "Prime Video • Autoplay",
                "thumbnail": "",
                "url": f"https://www.amazon.com/gp/video/detail/{asin}?autoplay=1",
                "source": "webpage",
            })
            if len(results) >= limit:
                break
    except Exception:
        pass

    if not results:
        results.append({
            "id": "",
            "title": f"Search Prime Video on TV: {q}",
            "subtitle": "Opens Prime Video search results in Brave Kiosk",
            "thumbnail": "",
            "url": search_url,
            "source": "webpage",
        })
    return results


def build_stream_item(data):
    url = (data.get("url") or "").strip()
    vid = (data.get("videoId") or "").strip()
    if not vid and url:
        yt_match = YT_URL_RE.search(url)
        if yt_match:
            vid = yt_match.group(1)

    added_by = (data.get("addedBy") or "Guest").strip() or "Guest"
    if vid:
        yt_url = f"https://www.youtube.com/tv?v={vid}"
        title = (data.get("title") or "").strip()
        channel = (data.get("channel") or "").strip()
        thumbnail = (data.get("thumbnail") or "").strip()
        if not title or title.startswith("YouTube ("):
            meta = yt_oembed(vid)
            title = meta.get("title") or title or f"YouTube ({vid})"
            channel = channel or meta.get("channel") or ""
            thumbnail = thumbnail or meta.get("thumbnail") or ""
        if not thumbnail:
            thumbnail = f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg"
        return {
            "url": yt_url,
            "videoId": vid,
            "title": title,
            "channel": channel,
            "thumbnail": thumbnail,
            "pageUrl": data.get("pageUrl") or url or yt_url,
            "source": "youtube",
            "addedBy": added_by,
            "timestamp": int(time.time()),
        }

    referer = (data.get("referer") or data.get("pageUrl") or "").strip()
    stream_type = (data.get("streamType") or "").strip()
    explicit_source = (data.get("source") or "").strip().lower()

    is_direct_stream = (
        explicit_source == "stream"
        or bool(stream_type)
        or bool(DIRECT_MEDIA_RE.search(url))
    )
    source = "stream" if (is_direct_stream and explicit_source != "webpage") else "webpage"
    if source == "webpage":
        url = normalize_amazon_url(normalize_movy_url(url))

    title = (data.get("title") or "").strip()
    if not title:
        parsed_u = urlparse(url)
        host_label = (parsed_u.netloc or "").replace("www.", "")
        tail = parsed_u.path.rstrip("/").split("/")[-1] if parsed_u.path else ""
        if source == "webpage" and host_label:
            title = f"{host_label} • {tail}" if tail else host_label
        else:
            title = tail or "Video Stream"

    proxy_url = ""
    if source == "stream" and url:
        ref_q = f"&referer={quote(referer, safe='')}" if referer else ""
        proxy_url = f"/api/proxy?url={quote(url, safe='')}{ref_q}"

    return {
        "url": url,
        "proxyUrl": proxy_url,
        "referer": referer,
        "streamType": stream_type,
        "videoId": None,
        "title": title,
        "channel": (data.get("channel") or "").strip(),
        "thumbnail": (data.get("thumbnail") or "").strip(),
        "pageUrl": data.get("pageUrl", ""),
        "source": source,
        "addedBy": added_by,
        "timestamp": int(time.time()),
    }


def play_stream_item(stream_info):
    """Play a YouTube, Web Cinema page (e.g. ramoflix.com), or HLS/MP4 stream immediately."""
    state_mgr.record_user_played(stream_info.get("addedBy"))
    state_mgr.add_history(stream_info)

    if stream_info.get("source") == "youtube" or stream_info.get("videoId"):
        vid = stream_info["videoId"]
        yt_url = f"https://www.youtube.com/tv?v={vid}"
        was_login_mode = state_mgr.get_status().get("loginMode", False)
        state_mgr.update_status({
            "state": "playing",
            "youtubeRunning": True,
            "loginMode": False,
            "stream": stream_info,
            "currentTime": 0,
        })
        if autopilot.page_alive() and not was_login_mode:
            wake_tv_and_screen()
            ps.activate_kiosk(KIOSK_DIR)
            autopilot.notify_video(vid)
            autopilot.play_video(vid)
        else:
            autopilot.notify_video(vid)
            launch_brave_kiosk(yt_url, is_youtube=True)
    elif stream_info.get("source") == "webpage":
        # Open the streaming site directly in Brave Kiosk (Shields block ads; CDP maximizes the video iframe)
        state_mgr.update_status({
            "state": "playing",
            "youtubeRunning": False,
            "loginMode": False,
            "stream": stream_info,
            "currentTime": 0,
        })
        launch_brave_kiosk(stream_info["url"], is_youtube=False)
    else:
        was_login_mode = state_mgr.get_status().get("loginMode", False)
        state_mgr.update_status({
            "state": "playing",
            "youtubeRunning": False,
            "loginMode": False,
            "stream": stream_info,
            "currentTime": 0,
        })
        has_active_receiver = (
            not was_login_mode
            and len(state_mgr.sse_queues) > 0
            and not autopilot.page_alive()
            and not autopilot.web_cinema_alive()
        )
        if not has_active_receiver:
            launch_brave_kiosk(f"http://localhost:{PORT}/receiver", is_youtube=False)
        else:
            wake_tv_and_screen()
            ps.activate_kiosk(KIOSK_DIR)
        state_mgr.broadcast("LOAD", stream_info)

    state_mgr.broadcast("JAM_UPDATE", state_mgr.get_jam_queue())


def is_anything_playing():
    st = state_mgr.get_status()
    if st.get("loginMode"):
        return False
    if autopilot.is_actively_playing() or autopilot.web_cinema_alive():
        return True
    if (
        st.get("state") == "playing"
        and not st.get("youtubeRunning")
        and len(state_mgr.sse_queues) > 0
        and (time.time() - st.get("lastUpdate", 0) < 15.0)
    ):
        return True
    return False


def handle_autopilot_jam_pop():
    item = state_mgr.pop_next_jam()
    if not item:
        return None
    state_mgr.add_history(item)
    state_mgr.broadcast("JAM_UPDATE", state_mgr.get_jam_queue())
    if item.get("videoId"):
        state_mgr.update_status({
            "state": "playing",
            "youtubeRunning": True,
            "stream": item,
            "currentTime": 0,
        })
        return item
    play_stream_item(item)
    return {"handled_externally": True}


autopilot = YouTubeAutopilot(
    CDP_PORT,
    is_youtube_running=lambda: state_mgr.get_status().get("youtubeRunning", False),
    on_player_state=state_mgr.update_status,
    pop_jam_next=handle_autopilot_jam_pop,
    has_jam_items=state_mgr.has_jam_items,
    is_login_mode=lambda: state_mgr.get_status().get("loginMode", False),
)


def start_caffeinate():
    keep_awake = ps.KeepAwake()
    keep_awake.start()
    return keep_awake


# --- DIAL SSDP SERVER ---
class DialSsdpServer(threading.Thread):
    def __init__(self, local_ip, port):
        super().__init__(daemon=True)
        self.local_ip = local_ip
        self.port = port
        self.mcast_group = "239.255.255.250"
        self.mcast_port = 1900

    def run(self):
        print(f"📡 DIAL SSDP Responder listening on {self.mcast_group}:{self.mcast_port}...")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except AttributeError:
                pass

            sock.bind(("", self.mcast_port))
            try:
                mreq = struct.pack("4s4s", socket.inet_aton(self.mcast_group), socket.inet_aton(self.local_ip))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                mreq = struct.pack("4s4s", socket.inet_aton(self.mcast_group), socket.inet_aton("0.0.0.0"))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except Exception as e:
            print(f"⚠️ Could not bind SSDP port 1900: {e}")
            return

        while True:
            try:
                data, addr = sock.recvfrom(2048)
                msg = data.decode("utf-8", errors="ignore")
                if "M-SEARCH" in msg and ("urn:dial-multiscreen-org:service:dial:1" in msg or "ssdp:all" in msg):
                    location = f"http://{self.local_ip}:{self.port}/dd.xml"
                    response = (
                        "HTTP/1.1 200 OK\r\n"
                        f"LOCATION: {location}\r\n"
                        "CACHE-CONTROL: max-age=1800\r\n"
                        "EXT:\r\n"
                        "BOOTID.UPNP.ORG: 1\r\n"
                        f"SERVER: {platform.system()}/{platform.release()} UPnP/1.1 BraveCast/2.0\r\n"
                        "ST: urn:dial-multiscreen-org:service:dial:1\r\n"
                        f"USN: uuid:{DEVICE_UUID}::urn:dial-multiscreen-org:service:dial:1\r\n"
                        f"WAKEUP: MAC={MAC_ADDR};Timeout=10\r\n"
                        "\r\n"
                    )
                    sock.sendto(response.encode("utf-8"), addr)
            except Exception as e:
                time.sleep(1)


# --- HTTP HANDLER ---
class BraveCastHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def read_body(self):
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            return ""
        return self.rfile.read(content_len).decode("utf-8", errors="ignore")

    def read_json_body(self):
        body = self.read_body()
        try:
            return json.loads(body)
        except Exception:
            return {}

    def send_json(self, data, status_code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_xml(self, xml_str, status_code=200, extra_headers=None):
        body = xml_str.encode("utf-8")
        self.send_response(status_code)
        self.send_cors_headers()
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, filepath, content_type):
        if not os.path.exists(filepath):
            self.send_response(404)
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(b"File not found")
            return

        with open(filepath, "rb") as f:
            content = f.read()

        self.send_response(200)
        self.send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/dd.xml":
            app_url = f"http://{LOCAL_IP}:{PORT}/apps/"
            xml = f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:tvdevice:1</deviceType>
    <friendlyName>{FRIENDLY_NAME}</friendlyName>
    <manufacturer>Google</manufacturer>
    <modelName>BraveCast</modelName>
    <UDN>uuid:{DEVICE_UUID}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:dial-multiscreen-org:service:dial:1</serviceType>
        <serviceId>urn:dial-multiscreen-org:serviceId:dial</serviceId>
        <controlURL>/apps</controlURL>
        <eventSubURL></eventSubURL>
        <SCPDURL></SCPDURL>
      </service>
    </serviceList>
  </device>
</root>"""
            self.send_xml(xml, extra_headers={"Application-URL": app_url})
            return

        if path in ("/apps/YouTube", "/apps/YouTube/"):
            st = state_mgr.get_status()
            yt_running = st.get("youtubeRunning", False)
            state_val = "running" if yt_running else "stopped"
            link_tag = '<link rel="run" href="run"/>' if yt_running else ""

            xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<service xmlns="urn:dial-multiscreen-org:schemas:dial" dialVer="2.1">
  <name>YouTube</name>
  <options allowStop="true"/>
  <state>{state_val}</state>
  {link_tag}
</service>"""
            self.send_xml(xml)
            return

        if path == "/api/ping":
            self.send_json({
                "status": "ok",
                "device": socket.gethostname(),
                "name": FRIENDLY_NAME,
                "version": "2.0",
                "localIp": LOCAL_IP,
                "dial": "active",
                "spotify": "active",
                "receiverUrl": f"http://localhost:{PORT}/receiver"
            })
            return

        if path == "/api/status":
            st = state_mgr.get_status()
            st["history"] = state_mgr.get_history()
            st["jamQueue"] = state_mgr.get_jam_queue()
            st["autoplay"] = autopilot.status()
            st["codeVersion"] = updater.get_local_version()
            st["localIp"] = LOCAL_IP
            st["port"] = PORT
            self.send_json(st)
            return

        if path == "/api/update":
            self.send_json(updater.check_for_update())
            return

        if path == "/api/doctor":
            params = parse_qs(parsed.query)
            run_cdp = (params.get("cdp") or ["0"])[0] == "1"
            self.send_json(doctor.run_all_checks(include_headless_cdp=run_cdp))
            return

        if path == "/api/proxy":
            params = parse_qs(parsed.query)
            target_url = (params.get("url") or [""])[0].strip()
            referer = (params.get("referer") or [""])[0].strip()
            if not target_url.startswith(("http://", "https://")):
                self.send_json({"error": "Invalid proxy target URL"}, 400)
                return

            parsed_target = urlparse(target_url)
            parsed_ref = urlparse(referer) if referer else parsed_target
            origin = f"{parsed_ref.scheme}://{parsed_ref.netloc}" if parsed_ref.netloc else ""
            req_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                "Accept": "*/*",
            }
            if referer:
                req_headers["Referer"] = referer
            elif origin:
                req_headers["Referer"] = origin + "/"
            if origin:
                req_headers["Origin"] = origin
            if "Range" in self.headers:
                req_headers["Range"] = self.headers["Range"]

            try:
                req = urllib.request.Request(target_url, headers=req_headers)
                with urllib.request.urlopen(req, timeout=15) as up:
                    status_code = getattr(up, "status", 200)
                    final_url = up.geturl() or target_url
                    ct = (up.headers.get("Content-Type") or "application/octet-stream").lower()
                    is_m3u8_hint = (
                        "mpegurl" in ct
                        or ".m3u8" in final_url.lower()
                        or final_url.lower().endswith(("/master.txt", "/playlist.txt", "/index.txt"))
                    )
                    first_chunk = up.read(65536)
                    if is_m3u8_hint or first_chunk.lstrip().startswith(b"#EXTM3U"):
                        raw_bytes = first_chunk + up.read()
                        text = raw_bytes.decode("utf-8", errors="replace")
                        rewritten = rewrite_m3u8_manifest(text, final_url, referer=referer).encode("utf-8")
                        self.send_response(200)
                        self.send_cors_headers()
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl; charset=utf-8")
                        self.send_header("Content-Length", str(len(rewritten)))
                        self.end_headers()
                        self.wfile.write(rewritten)
                        return

                    self.send_response(status_code)
                    self.send_cors_headers()
                    self.send_header("Content-Type", up.headers.get("Content-Type") or "application/octet-stream")
                    for h_name in ("Content-Length", "Content-Range", "Accept-Ranges"):
                        if up.headers.get(h_name):
                            self.send_header(h_name, up.headers.get(h_name))
                    self.end_headers()
                    if first_chunk:
                        self.wfile.write(first_chunk)
                    while True:
                        buf = up.read(65536)
                        if not buf:
                            break
                        self.wfile.write(buf)
            except (ConnectionResetError, BrokenPipeError):
                pass
            except Exception as e:
                self.send_json({"error": f"Proxy fetch failed: {e}"}, 502)
            return

        if path == "/api/youtube/search":
            params = parse_qs(parsed.query)
            q = (params.get("q") or [""])[0].strip()
            if not q:
                self.send_json({"results": []})
                return
            try:
                results = yt_search(q, limit=6)
                self.send_json({"results": results})
            except Exception as e:
                self.send_json({"error": str(e), "results": []}, 500)
            return

        if path == "/api/movy/search":
            params = parse_qs(parsed.query)
            q = (params.get("q") or [""])[0].strip()
            if not q:
                self.send_json({"results": []})
                return
            try:
                results = movy_search(q, limit=8)
                self.send_json({"results": results})
            except Exception as e:
                self.send_json({"error": str(e), "results": []}, 500)
            return

        if path == "/api/amazon/search":
            params = parse_qs(parsed.query)
            q = (params.get("q") or [""])[0].strip()
            if not q:
                self.send_json({"results": []})
                return
            try:
                results = amazon_search(q, limit=8)
                self.send_json({"results": results})
            except Exception as e:
                self.send_json({"error": str(e), "results": []}, 500)
            return

        if path == "/api/events":
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            q = state_mgr.register_sse()
            init_msg = f"event: INIT\ndata: {json.dumps(state_mgr.get_status())}\n\n"
            try:
                self.wfile.write(init_msg.encode("utf-8"))
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15.0)
                        self.wfile.write(msg.encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
            except (ConnectionResetError, BrokenPipeError):
                pass
            finally:
                state_mgr.unregister_sse(q)
            return

        if path in ("/", "/remote"):
            self.serve_file(os.path.join(DIR_PATH, "remote.html"), "text/html; charset=utf-8")
            return

        if path == "/receiver":
            self.serve_file(os.path.join(DIR_PATH, "receiver.html"), "text/html; charset=utf-8")
            return

        if path == "/hls.min.js":
            self.serve_file(os.path.join(DIR_PATH, "hls.min.js"), "application/javascript")
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/apps/YouTube", "/apps/YouTube/"):
            raw_body = self.read_body()
            yt_url = "https://www.youtube.com/tv"
            params = parse_qs(raw_body)
            if not params:
                params = parse_qs(parsed.query)

            query_parts = []
            if "pairingCode" in params:
                query_parts.append(f"pairingCode={params['pairingCode'][0]}")
            if "v" in params:
                query_parts.append(f"v={params['v'][0]}")
            if "t" in params:
                query_parts.append(f"t={params['t'][0]}")

            if query_parts:
                yt_url = f"https://www.youtube.com/tv?{'&'.join(query_parts)}"

            dial_vid = params["v"][0] if "v" in params else None
            if dial_vid:
                item = build_stream_item({"videoId": dial_vid, "addedBy": "Phone (Cast)"})
                if is_anything_playing():
                    queued_item, pos = state_mgr.add_to_jam(item)
                    state_mgr.broadcast("JAM_UPDATE", state_mgr.get_jam_queue())
                    print(f"[jam] DIAL video queued at #{pos}: {queued_item.get('title')}")
                else:
                    play_stream_item(item)
            else:
                # Pairing request without a direct videoId: don't kill an actively playing session
                if not is_anything_playing():
                    launch_brave_kiosk(yt_url, is_youtube=True)
                    state_mgr.update_status({
                        "state": "playing",
                        "youtubeRunning": True,
                        "stream": {"url": yt_url, "title": "YouTube on TV (Ad-Free)", "source": "dial"}
                    })

            self.send_response(201)
            self.send_cors_headers()
            self.send_header("Location", f"http://{LOCAL_IP}:{PORT}/apps/YouTube/run")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path == "/api/youtube":
            url = "https://www.youtube.com/tv"
            was_login = state_mgr.get_status().get("loginMode", False)
            if not autopilot.page_alive() or was_login:
                launch_brave_kiosk(url, is_youtube=True)
            else:
                wake_tv_and_screen()
                ps.activate_kiosk(KIOSK_DIR)
            state_mgr.update_status({
                "state": "playing",
                "youtubeRunning": True,
                "loginMode": False,
                "stream": {"url": url, "title": "YouTube on TV (Ad-Free)", "source": "youtube"}
            })
            self.send_json({"success": True, "message": "YouTube TV opened in Brave Kiosk"})
            return

        if path == "/api/movy":
            data = self.read_json_body()
            raw_url = (data.get("url") or "https://www.movy.sx/").strip()
            url = normalize_movy_url(raw_url) or "https://www.movy.sx/"
            launch_brave_kiosk(url, is_youtube=False)
            state_mgr.update_status({
                "state": "playing",
                "youtubeRunning": False,
                "loginMode": False,
                "stream": {"url": url, "title": "Movy.sx", "source": "webpage"}
            })
            self.send_json({"success": True, "message": "Movy.sx opened in Brave Kiosk"})
            return

        if path == "/api/amazon":
            data = self.read_json_body()
            raw_url = (data.get("url") or "https://www.amazon.com/gp/video/storefront").strip()
            url = normalize_amazon_url(raw_url) or "https://www.amazon.com/gp/video/storefront"
            launch_brave_kiosk(url, is_youtube=False)
            state_mgr.update_status({
                "state": "playing",
                "youtubeRunning": False,
                "loginMode": False,
                "stream": {"url": url, "title": "Amazon Prime Video", "source": "webpage"}
            })
            self.send_json({"success": True, "message": "Prime Video opened in Brave Kiosk"})
            return

        if path in ("/api/amazon/login", "/api/kiosk/login"):
            data = self.read_json_body()
            raw_url = (data.get("url") or "https://www.amazon.com/gp/video/storefront").strip()
            url = raw_url if raw_url.startswith(("http://", "https://")) else "https://www.amazon.com/gp/video/storefront"
            launch_brave_kiosk(url, is_youtube=False, kiosk=False)
            state_mgr.update_status({
                "state": "playing",
                "youtubeRunning": False,
                "loginMode": True,
                "stream": {"url": url, "title": "Prime Video (Windowed Sign-In)", "source": "webpage"}
            })
            self.send_json({"success": True, "loginMode": True, "message": "Windowed Brave sign-in opened on TV"})
            return

        if path == "/api/kiosk/exit-login":
            launch_brave_kiosk(f"http://localhost:{PORT}/receiver", is_youtube=False, kiosk=True)
            state_mgr.update_status({
                "state": "idle",
                "youtubeRunning": False,
                "loginMode": False,
                "stream": None
            })
            self.send_json({"success": True, "loginMode": False, "message": "Returned to BraveCast Kiosk"})
            return

        if path in ("/api/cast", "/api/jam/add"):
            data = self.read_json_body()
            url = (data.get("url") or "").strip()
            vid = (data.get("videoId") or "").strip()
            if not url and not vid:
                self.send_json({"error": "No url or videoId provided"}, 400)
                return

            mode = (data.get("mode") or ("queue" if path == "/api/jam/add" else "auto")).strip().lower()
            client_id = (data.get("clientId") or "").strip()
            stream_info = build_stream_item(data)

            should_queue = False
            if mode in ("queue", "next"):
                # Even in "queue"/"next" mode, if nothing is playing and the queue is empty, start playing right away
                if is_anything_playing() or state_mgr.has_jam_items():
                    should_queue = True
            elif mode == "auto":
                if is_anything_playing() or state_mgr.has_jam_items():
                    should_queue = True

            if should_queue:
                queued_item, pos = state_mgr.add_to_jam(
                    stream_info,
                    play_next=(mode == "next"),
                    voter_id=client_id or stream_info.get("addedBy"),
                )
                queue_snapshot = state_mgr.get_jam_queue()
                state_mgr.broadcast("JAM_UPDATE", queue_snapshot)
                self.send_json({
                    "success": True,
                    "queued": True,
                    "position": pos,
                    "message": f"Added '{queued_item['title']}' to Jam (#{pos})",
                    "stream": queued_item,
                    "jamQueue": queue_snapshot,
                })
                return

            play_stream_item(stream_info)
            self.send_json({
                "success": True,
                "queued": False,
                "message": f"Playing '{stream_info['title']}' on {FRIENDLY_NAME}",
                "stream": stream_info,
                "jamQueue": state_mgr.get_jam_queue(),
            })
            return

        if path == "/api/jam/action":
            data = self.read_json_body()
            action = (data.get("action") or "").strip()
            item_id = (data.get("id") or "").strip()
            client_id = (data.get("clientId") or "").strip()
            if not action:
                self.send_json({"error": "No action provided"}, 400)
                return

            target = state_mgr.jam_action(action, item_id=item_id, client_id=client_id)
            if action == "play_now" and target:
                play_stream_item(target)
            else:
                state_mgr.broadcast("JAM_UPDATE", state_mgr.get_jam_queue())
            self.send_json({
                "success": True,
                "action": action,
                "target": target,
                "jamQueue": state_mgr.get_jam_queue(),
            })
            return

        if path == "/api/control":
            data = self.read_json_body()
            action = data.get("action")
            value = data.get("value")

            if not action:
                self.send_json({"error": "No action provided"}, 400)
                return

            if action in ("play", "toggle"):
                ps.wake_display()

            if action in ("skip", "next"):
                if autopilot.page_alive():
                    autopilot.skip()
                elif state_mgr.has_jam_items():
                    nxt = state_mgr.pop_next_jam()
                    if nxt:
                        play_stream_item(nxt)
                elif autopilot.web_cinema_alive():
                    autopilot.control("next", None)
                else:
                    state_mgr.broadcast("CONTROL", {"action": "next", "value": None})
                self.send_json({"success": True, "action": action, "jamQueue": state_mgr.get_jam_queue()})
                return

            if action == "toggle_autoplay":
                autopilot.enabled = not autopilot.enabled
                self.send_json({"success": True, "action": action, "autoplay": autopilot.status()})
                return

            if action == "stop":
                if autopilot.page_alive() or autopilot.web_cinema_alive() or state_mgr.get_status().get("loginMode"):
                    launch_brave_kiosk(f"http://localhost:{PORT}/receiver", is_youtube=False)
                state_mgr.update_status({"state": "idle", "youtubeRunning": False, "loginMode": False, "stream": None})

            if action in ("toggle", "play", "pause", "seek", "mute", "volume", "fullscreen"):
                autopilot.control(action, value)

            state_mgr.broadcast("CONTROL", {"action": action, "value": value})
            self.send_json({"success": True, "action": action, "value": value})
            return

        if path == "/api/wake":
            ps.wake_display()
            self.send_json({"success": True})
            return

        if path == "/api/update":
            data = self.read_json_body()
            force = bool(data.get("force", True))
            res = updater.apply_update(force=force)
            self.send_json(res)
            if res.get("updated"):
                _schedule_restart(1.0)
            return

        if path == "/api/receiver/status":
            data = self.read_json_body()
            if data.get("state") == "ended" and state_mgr.has_jam_items():
                nxt = state_mgr.pop_next_jam()
                if nxt:
                    play_stream_item(nxt)
                    self.send_json({"success": True, "advanced": True})
                    return
                data["state"] = "idle"
            state_mgr.update_status(data)
            self.send_json({"success": True})
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(b"Endpoint not found")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/apps/YouTube/run", "/apps/YouTube/run/"):
            state_mgr.update_status({"youtubeRunning": False, "state": "idle"})
            self.send_response(200)
            self.send_cors_headers()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()
        self.wfile.write(b"Not found")

    def log_message(self, format, *args):
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {args[0]} {args[1]}\n")


_HTTP_SERVER = None


def _schedule_restart(delay=1.0):
    """Gracefully replace the running daemon process with the newly updated code."""
    def _do_restart():
        time.sleep(delay)
        print("🔄 [ota] Restarting BraveCast daemon with updated code...")
        try:
            if _HTTP_SERVER:
                _HTTP_SERVER.socket.close()
        except Exception:
            pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    threading.Thread(target=_do_restart, daemon=True).start()


def _maintenance_loop():
    """
    Background self-maintenance:
      1. Keep SMART_TV_UA and InnerTube client version fresh when idle.
      2. Check GitHub `main` for OTA code updates and self-apply + restart when idle.
    """
    global SMART_TV_UA
    time.sleep(20)  # Let startup settle before first check
    while True:
        sleep_sec = 6 * 3600
        try:
            if not is_anything_playing():
                SMART_TV_UA = doctor.resolve_smart_tv_ua()
                if os.environ.get("BRAVECAST_AUTO_UPDATE") != "0":
                    res = updater.apply_update(force=False)
                    if res.get("updated"):
                        _schedule_restart(1.0)
                        return
            else:
                # Playback is active right now; check again in 30 minutes when TV may be idle
                sleep_sec = 1800
        except Exception as e:
            print(f"[maintenance] check warning: {e}")
        time.sleep(sleep_sec)


def main():
    global _HTTP_SERVER
    print(f"🚀 Starting BraveCast Receiver & DIAL Server on port {PORT}...")
    print(f"🏠 Local IP: {LOCAL_IP}")
    caffeinate_proc = start_caffeinate()

    threading.Thread(target=_maintenance_loop, daemon=True).start()
    autopilot.start()

    ssdp_server = DialSsdpServer(LOCAL_IP, PORT)
    ssdp_server.start()

    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((HOST, PORT), BraveCastHandler)
    server.daemon_threads = True
    _HTTP_SERVER = server

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.server_close()
        if caffeinate_proc:
            caffeinate_proc.stop()


if __name__ == "__main__":
    main()
