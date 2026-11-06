#!/usr/bin/env python3
"""
BraveCast Doctor & Automated Maintenance Suite.

Verifies every third-party integration surface that can drift as Brave/Chromium
and YouTube evolve:
  1. Brave Browser executable & version
  2. Chrome DevTools Protocol (CDP) WebSocket handshake & PROBE_JS evaluation
  3. YouTube InnerTube (`next`, `search`) & oEmbed metadata APIs
  4. YouTube TV Leanback (`youtube.com/tv`) Smart-TV User-Agent acceptance

Run manually:
  python3 receiver/doctor.py
  python3 receiver/doctor.py --upgrade-browser

Or query over HTTP on a running receiver:
  curl http://localhost:8099/api/doctor
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

try:
    import platform_support as ps
    from cdp import CDPClient, list_targets
    from autoplay import PROBE_JS, innertube_next, yt_oembed, yt_search
except ImportError:
    from receiver import platform_support as ps
    from receiver.cdp import CDPClient, list_targets
    from receiver.autoplay import PROBE_JS, innertube_next, yt_oembed, yt_search

# Ranked Smart TV User-Agents. If YouTube retires an older Tizen version, resolve_smart_tv_ua()
# automatically falls forward to a newer Tizen / WebOS User-Agent.
SMART_TV_UA_CANDIDATES = [
    "Mozilla/5.0 (SMART-TV; Linux; Tizen 8.0) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/7.0 Chrome/108.0.5359.128 TV Safari/537.36",
    "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.5) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/5.0 Chrome/85.0.4183.93 TV Safari/537.36",
    "Mozilla/5.0 (Web0S; Linux/SmartTV) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.5359.211 Safari/537.36 WebAppManager",
    "Mozilla/5.0 (SMART-TV; Linux; Tizen 5.0) AppleWebKit/538.1 (KHTML, like Gecko) Version/5.0 Native TV /0.0.1 TV Safari/538.1",
]


def check_ua_against_youtube_tv(ua, timeout=8):
    req = urllib.request.Request(
        "https://www.youtube.com/tv",
        headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        final_url = r.geturl()
        body = r.read(150000).decode("utf-8", errors="ignore").lower()
    is_tv = "/tv" in final_url and ("leanback" in body or "ytlr" in body or "youtube.com/tv" in body or "kabuki" in body)
    unsupported = "browser is no longer supported" in body or "update your browser" in body
    return is_tv and not unsupported


def resolve_smart_tv_ua():
    override = os.environ.get("BRAVECAST_TV_UA")
    if override:
        return override
    for ua in SMART_TV_UA_CANDIDATES:
        try:
            if check_ua_against_youtube_tv(ua, timeout=5):
                return ua
        except Exception:
            continue
    return SMART_TV_UA_CANDIDATES[0]


def check_brave_binary():
    path = ps.find_browser()
    exists = os.path.exists(path) or bool(shutil.which(path))
    version = None
    if exists and not ps.IS_WINDOWS:
        try:
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
            version = (out.stdout or out.stderr or "").strip()
        except Exception as e:
            version = f"unknown ({e})"
    return {"ok": exists, "path": path, "version": version}


def check_cdp_probe():
    """Spawn a temporary headless browser on an ephemeral port and test CDP + PROBE_JS."""
    path = ps.find_browser()
    if not (os.path.exists(path) or shutil.which(path)):
        return {"ok": False, "error": f"Browser not found at {path}"}

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    tmp_profile = tempfile.mkdtemp(prefix="bravecast_doctor_")
    proc = None
    try:
        cmd = [
            path,
            f"--user-data-dir={tmp_profile}",
            "--headless=new",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-gpu",
            f"--remote-debugging-port={port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            "about:blank",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 10.0
        targets = []
        while time.time() < deadline:
            try:
                targets = [t for t in list_targets(port, timeout=1.0) if t.get("type") == "page"]
                if targets:
                    break
            except Exception:
                pass
            time.sleep(0.25)

        if not targets:
            return {"ok": False, "error": "Timed out waiting for headless CDP target"}

        c = CDPClient(targets[0]["webSocketDebuggerUrl"], timeout=5.0)
        try:
            raw = c.evaluate(PROBE_JS, timeout=5.0)
            parsed = json.loads(raw)
        finally:
            c.close()

        required_keys = {"u", "vid", "has", "t", "d", "e", "p", "onHomeHash"}
        missing = sorted(required_keys - set(parsed.keys()))
        return {"ok": len(missing) == 0, "missingKeys": missing, "sample": parsed.get("u")}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        if proc:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        shutil.rmtree(tmp_profile, ignore_errors=True)


def check_youtube_endpoints():
    report = {"oembed": False, "next": False, "search": False}
    errors = []
    try:
        meta = yt_oembed("dQw4w9WgXcQ")
        report["oembed"] = bool(meta.get("title") and "YouTube (" not in meta["title"])
    except Exception as e:
        errors.append(f"oembed: {e}")

    try:
        nxt = innertube_next("dQw4w9WgXcQ", {"dQw4w9WgXcQ"})
        report["next"] = bool(nxt and len(nxt) == 11 and nxt != "dQw4w9WgXcQ")
        report["nextSample"] = nxt
    except Exception as e:
        errors.append(f"next: {e}")

    try:
        results = yt_search("daft punk one more time", limit=2)
        report["search"] = len(results) > 0 and bool(results[0].get("videoId"))
    except Exception as e:
        errors.append(f"search: {e}")

    report["ok"] = report["oembed"] and report["next"] and report["search"]
    if errors:
        report["errors"] = errors
    return report


def check_youtube_tv_ua():
    working = []
    for ua in SMART_TV_UA_CANDIDATES:
        try:
            if check_ua_against_youtube_tv(ua, timeout=6):
                working.append(ua)
                break
        except Exception:
            continue
    return {
        "ok": len(working) > 0,
        "selectedUa": working[0] if working else None,
    }


def upgrade_browser():
    if ps.IS_MAC and shutil.which("brew"):
        res = subprocess.run(["brew", "upgrade", "--cask", "brave-browser"], capture_output=True, text=True)
        return {"ran": "brew upgrade --cask brave-browser", "code": res.returncode, "output": (res.stdout + res.stderr)[-400:]}
    if ps.IS_LINUX and shutil.which("apt-get"):
        res = subprocess.run(["sudo", "-n", "apt-get", "install", "-y", "--only-upgrade", "brave-browser"], capture_output=True, text=True)
        return {"ran": "apt-get --only-upgrade brave-browser", "code": res.returncode, "output": (res.stdout + res.stderr)[-400:]}
    return {"ran": None, "note": "Brave uses built-in background auto-update on this OS"}


def run_all_checks(include_headless_cdp=True, do_upgrade=False):
    results = {
        "timestamp": int(time.time()),
        "browser": check_brave_binary(),
        "youtubeApis": check_youtube_endpoints(),
        "youtubeTvUa": check_youtube_tv_ua(),
    }
    if include_headless_cdp:
        results["cdp"] = check_cdp_probe()
    if do_upgrade:
        results["upgrade"] = upgrade_browser()

    results["ok"] = all(
        results[k].get("ok", False)
        for k in ("browser", "youtubeApis", "youtubeTvUa", *(["cdp"] if include_headless_cdp else []))
    )
    return results


def main():
    parser = argparse.ArgumentParser(description="BraveCast Diagnostic & Self-Maintenance Suite")
    parser.add_argument("--no-cdp", action="store_true", help="Skip spawning headless Brave for CDP test")
    parser.add_argument("--upgrade-browser", action="store_true", help="Attempt OS package upgrade for Brave")
    args = parser.parse_args()

    report = run_all_checks(include_headless_cdp=not args.no_cdp, do_upgrade=args.upgrade_browser)
    print(json.dumps(report, indent=2))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()

