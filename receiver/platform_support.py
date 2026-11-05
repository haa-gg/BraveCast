"""
Cross-platform helpers for BraveCast (macOS + Windows).

Everything OS-specific lives here so server.py stays platform-neutral:
  - locating the Brave binary and the dedicated kiosk profile directory
  - waking the display (which wakes the TV over HDMI)
  - keeping the machine awake
  - killing / focusing the kiosk window
"""

import os
import shutil
import subprocess
import sys
import threading

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

# Don't flash a console window for helper commands on Windows
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


def _run(cmd, **kwargs):
    kwargs.setdefault("check", False)
    if IS_WINDOWS:
        kwargs.setdefault("creationflags", _NO_WINDOW)
    return subprocess.run(cmd, **kwargs)


def _ps(script):
    """Run a PowerShell snippet (Windows only)."""
    return _run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# --- Paths -------------------------------------------------------------------

def kiosk_profile_dir():
    """Dedicated Brave profile so kiosk flags never collide with the user's normal browser."""
    override = os.environ.get("BRAVECAST_PROFILE_DIR")
    if override:
        return override
    if IS_WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
        return os.path.join(base, "BraveSoftware", "Brave-Kiosk")
    if IS_MAC:
        return os.path.expanduser("~/Library/Application Support/BraveSoftware/Brave-Kiosk")
    return os.path.expanduser("~/.config/BraveSoftware/Brave-Kiosk")


def find_browser():
    """Return the path to the Brave executable (override with BRAVECAST_BROWSER)."""
    override = os.environ.get("BRAVECAST_BROWSER")
    if override:
        return override

    candidates = []
    if IS_MAC:
        candidates.append("/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
    elif IS_WINDOWS:
        for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                candidates.append(
                    os.path.join(base, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")
                )
    else:
        for name in ("brave-browser", "brave"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0] if candidates else "brave"


# --- Display / power ---------------------------------------------------------

def wake_display():
    """
    Wake the display and keep the video signal active.
    The HDMI clock coming back is what makes a TV with Quick Start / auto input detect
    switch to this machine's port.
    """
    try:
        if IS_MAC:
            _run(["caffeinate", "-u", "-t", "4"])
        elif IS_WINDOWS:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
            kernel32.SetThreadExecutionState.restype = ctypes.c_uint
            ES_SYSTEM_REQUIRED = 0x00000001
            ES_DISPLAY_REQUIRED = 0x00000002
            kernel32.SetThreadExecutionState(ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)

            # A synthetic 1px wiggle is what actually brings a blanked display back
            MOUSEEVENTF_MOVE = 0x0001
            user32.mouse_event(MOUSEEVENTF_MOVE, 1, 0, 0, 0)
            user32.mouse_event(MOUSEEVENTF_MOVE, -1, 0, 0, 0)
    except Exception as e:
        print(f"Error waking display: {e}")


class KeepAwake:
    """Prevent system and display sleep for as long as the receiver is running."""

    def __init__(self):
        self._proc = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        try:
            if IS_MAC:
                self._proc = subprocess.Popen(["caffeinate", "-d", "-i"])
            elif IS_WINDOWS:
                self._thread = threading.Thread(target=self._windows_loop, daemon=True)
                self._thread.start()
        except Exception as e:
            print(f"Could not start keep-awake: {e}")

    def _windows_loop(self):
        import ctypes

        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ES_DISPLAY_REQUIRED = 0x00000002
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        kernel32.SetThreadExecutionState.restype = ctypes.c_uint
        # The execution state is tied to this thread, so the thread must stay alive
        kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
        self._stop.wait()
        kernel32.SetThreadExecutionState(ES_CONTINUOUS)

    def stop(self):
        if self._proc:
            self._proc.terminate()
        self._stop.set()


# --- Kiosk window management -------------------------------------------------

def kill_kiosk(profile_dir):
    """Terminate only the dedicated kiosk Brave instance, never the user's normal browser."""
    if IS_WINDOWS:
        marker = os.path.basename(profile_dir.rstrip("\\/")).replace("'", "''")
        _ps(
            "Get-CimInstance Win32_Process -Filter \"Name='brave.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
        )
    else:
        _run(["pkill", "-9", "-f", profile_dir])


def activate_kiosk(profile_dir=None):
    """Bring the kiosk window to the front."""
    if IS_MAC:
        _run(["osascript", "-e", 'tell application "Brave Browser" to activate'])
    elif IS_WINDOWS:
        marker = os.path.basename((profile_dir or kiosk_profile_dir()).rstrip("\\/")).replace("'", "''")
        _ps(
            "$p = Get-CimInstance Win32_Process -Filter \"Name='brave.exe'\" | "
            f"Where-Object {{ $_.CommandLine -like '*{marker}*' -and $_.CommandLine -notlike '*--type=*' }} | "
            "Select-Object -First 1; "
            "if ($p) { (New-Object -ComObject WScript.Shell).AppActivate([int]$p.ProcessId) | Out-Null }"
        )
