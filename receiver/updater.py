#!/usr/bin/env python3
"""
BraveCast Over-The-Air (OTA) Auto-Updater
Pure-stdlib updater that checks `github.com/haa-gg/BraveCast` (no `git` binary required),
validates syntax of downloaded Python files, atomically updates `receiver/`, and triggers a
graceful daemon restart when the TV is idle.

Disable automatic background updates with BRAVECAST_AUTO_UPDATE=0.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

DIR_PATH = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(DIR_PATH, ".version")
REPO = os.environ.get("BRAVECAST_REPO", "haa-gg/BraveCast")
BRANCH = os.environ.get("BRAVECAST_BRANCH", "main")

# Files inside `receiver/` that are safe to update over-the-air
UPDATABLE_EXTENSIONS = (".py", ".html", ".js", ".css", ".sh", ".ps1")
SKIP_FILES = {"com.glen.bravecast.plist", "com.glen.librespot.plist"}


def _git_head_sha():
    """If running inside a git clone, read HEAD SHA once to seed `.version`."""
    repo_root = os.path.dirname(DIR_PATH)
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        return None
    try:
        res = subprocess.run(
            ["git", "-C", repo_root, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
            if len(sha) == 40:
                return sha
    except Exception:
        pass
    return None


def _is_git_dirty():
    """Return True if running inside a developer's git working tree with uncommitted changes."""
    repo_root = os.path.dirname(DIR_PATH)
    if not os.path.isdir(os.path.join(repo_root, ".git")):
        return False
    try:
        res = subprocess.run(
            ["git", "-C", repo_root, "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if res.returncode == 0 and res.stdout.strip():
            return True
    except Exception:
        pass
    return False


def get_local_version():
    """Return dict with local `sha`, `shortSha`, and `updatedAt`."""
    try:
        if os.path.exists(VERSION_FILE):
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            sha = (data.get("sha") or "").strip()
            if sha:
                return {
                    "sha": sha,
                    "shortSha": sha[:7],
                    "message": data.get("message", ""),
                    "updatedAt": data.get("updatedAt", 0),
                }
    except Exception:
        pass

    git_sha = _git_head_sha()
    if git_sha:
        info = {
            "sha": git_sha,
            "shortSha": git_sha[:7],
            "message": "git checkout",
            "updatedAt": int(time.time()),
        }
        try:
            with open(VERSION_FILE, "w", encoding="utf-8") as f:
                json.dump(info, f)
        except Exception:
            pass
        return info

    return {"sha": "unknown", "shortSha": "zip-install", "message": "", "updatedAt": 0}


def fetch_remote_commit():
    """Query GitHub API for the latest commit on REPO/BRANCH."""
    url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "BraveCast-OTA-Updater/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    sha = (data.get("sha") or "").strip()
    commit_msg = ((data.get("commit") or {}).get("message") or "").splitlines()[0].strip()
    return {
        "sha": sha,
        "shortSha": sha[:7] if sha else "",
        "message": commit_msg,
    }


def check_for_update():
    """Compare local `.version` against GitHub `main` without modifying files."""
    local = get_local_version()
    try:
        remote = fetch_remote_commit()
    except Exception as e:
        return {
            "ok": False,
            "updateAvailable": False,
            "local": local,
            "error": str(e),
            "checkedAt": int(time.time()),
        }

    remote_sha = remote.get("sha") or ""
    local_sha = local.get("sha") or ""
    update_available = bool(remote_sha and remote_sha != local_sha)
    return {
        "ok": True,
        "updateAvailable": update_available,
        "local": local,
        "remote": remote,
        "checkedAt": int(time.time()),
    }


def apply_update(force=False):
    """
    Download the latest tarball from GitHub, syntax-check all `.py` files, and
    atomically update `receiver/` files in place.
    """
    status = check_for_update()
    if not status.get("ok"):
        return {"updated": False, "error": status.get("error", "GitHub check failed"), "status": status}

    if not status.get("updateAvailable") and not force:
        return {
            "updated": False,
            "message": f"Already up to date ({status['local']['shortSha']})",
            "status": status,
        }

    if not force and _is_git_dirty():
        return {
            "updated": False,
            "skippedDirtyGit": True,
            "message": "Skipped auto-update because git working tree has uncommitted edits",
            "status": status,
        }

    remote = status["remote"]
    tar_url = f"https://codeload.github.com/{REPO}/tar.gz/refs/heads/{BRANCH}"
    tmp_dir = tempfile.mkdtemp(prefix="bravecast_ota_")
    try:
        req = urllib.request.Request(tar_url, headers={"User-Agent": "BraveCast-OTA-Updater/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            raw_tar = r.read()

        with tarfile.open(fileobj=io.BytesIO(raw_tar), mode="r:gz") as tf:
            for member in tf.getmembers():
                # Guard against path traversal in tar members
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    continue
                tf.extract(member, path=tmp_dir)

        # Find the extracted `receiver/` directory
        extracted_receiver = None
        for entry in os.listdir(tmp_dir):
            cand = os.path.join(tmp_dir, entry, "receiver")
            if os.path.isdir(cand):
                extracted_receiver = cand
                break

        if not extracted_receiver or not os.path.exists(os.path.join(extracted_receiver, "server.py")):
            return {"updated": False, "error": "Archive missing receiver/server.py"}

        # Syntax-check every Python file in the update before touching the live folder
        for fname in os.listdir(extracted_receiver):
            if fname.endswith(".py"):
                fpath = os.path.join(extracted_receiver, fname)
                with open(fpath, "r", encoding="utf-8") as f:
                    src = f.read()
                compile(src, fname, "exec")

        updated_files = []
        for fname in os.listdir(extracted_receiver):
            if fname in SKIP_FILES or not fname.endswith(UPDATABLE_EXTENSIONS):
                continue
            src_path = os.path.join(extracted_receiver, fname)
            dst_path = os.path.join(DIR_PATH, fname)
            if os.path.isfile(src_path):
                tmp_dst = dst_path + ".ota.tmp"
                shutil.copy2(src_path, tmp_dst)
                os.replace(tmp_dst, dst_path)
                updated_files.append(fname)

        version_info = {
            "sha": remote["sha"],
            "shortSha": remote["shortSha"],
            "message": remote["message"],
            "updatedAt": int(time.time()),
        }
        with open(VERSION_FILE, "w", encoding="utf-8") as f:
            json.dump(version_info, f)

        print(f"[ota] Updated BraveCast to {remote['shortSha']} ({remote['message']}) [{len(updated_files)} files]")
        return {
            "updated": True,
            "sha": remote["sha"],
            "shortSha": remote["shortSha"],
            "message": remote["message"],
            "files": updated_files,
        }
    except Exception as e:
        return {"updated": False, "error": str(e)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    if "--apply" in sys.argv:
        res = apply_update(force="--force" in sys.argv)
        print(json.dumps(res, indent=2))
        sys.exit(0 if res.get("updated") or not res.get("error") else 1)
    else:
        res = check_for_update()
        print(json.dumps(res, indent=2))
        sys.exit(0 if res.get("ok") else 1)
