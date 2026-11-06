"""
Cross-platform helpers for BraveCast (macOS, Windows, Linux / Raspberry Pi).

Everything OS-specific lives here so server.py stays platform-neutral:
  - locating the Brave binary and the dedicated kiosk profile directory
  - waking the display (which wakes the TV over HDMI), plus HDMI-CEC where available
  - keeping the machine awake
  - killing / focusing the kiosk window
"""

import os
import shutil
import signal
import subprocess
import sys
import threading
import time

IS_WINDOWS = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

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


# --- Identity ----------------------------------------------------------------

def is_raspberry_pi():
    try:
        with open("/proc/device-tree/model", "rb") as f:
            return b"Raspberry Pi" in f.read()
    except OSError:
        return False


def default_device_label():
    """Suffix for the default cast-target name, e.g. 'Living Room TV (Pi)'."""
    if IS_MAC:
        return "Mac"
    if IS_WINDOWS:
        return "PC"
    return "Pi" if is_raspberry_pi() else "Linux"


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


_warned_chromium = False


def find_browser():
    """Return the path to the Brave executable (override with BRAVECAST_BROWSER)."""
    global _warned_chromium
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
        for name in ("brave-browser", "brave", "brave-browser-stable"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        candidates.append("/opt/brave.com/brave/brave-browser")

    for path in candidates:
        if os.path.exists(path):
            return path

    if IS_LINUX:
        # 32-bit Raspberry Pi OS has no Brave build. Chromium works, but without Brave Shields ad blocking.
        for name in ("chromium-browser", "chromium"):
            found = shutil.which(name)
            if found:
                if not _warned_chromium:
                    print("⚠️ Brave not found - falling back to Chromium (no Brave Shields ad blocking).")
                    _warned_chromium = True
                return found

    return candidates[0] if candidates else "brave"


def extra_browser_flags():
    """Platform-specific Brave flags appended to the kiosk command line."""
    if IS_LINUX:
        return [
            "--ozone-platform-hint=auto",   # native Wayland on Raspberry Pi OS Bookworm, X11 otherwise
            "--password-store=basic",       # never pop up a keyring unlock prompt on a headless box
        ]
    return []


# --- Linux session environment -----------------------------------------------

def ensure_display_env():
    """
    When started from systemd at boot, the process has no DISPLAY / WAYLAND_DISPLAY.
    Find the desktop session's sockets lazily (they only exist after auto-login finishes).
    """
    if not IS_LINUX:
        return
    runtime = os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    if not os.environ.get("WAYLAND_DISPLAY"):
        for name in ("wayland-0", "wayland-1"):
            if os.path.exists(os.path.join(runtime, name)):
                os.environ["WAYLAND_DISPLAY"] = name
                break
    if not os.environ.get("DISPLAY") and os.path.exists("/tmp/.X11-unix/X0"):
        os.environ["DISPLAY"] = ":0"


# --- HDMI-CEC (Raspberry Pi / any Linux box with a CEC adapter) ---------------

_cec_lock = threading.Lock()
_cec_last = 0.0


def cec_wake_tv():
    """
    Power the TV on and claim the active source, which makes the TV switch to our HDMI input.
    Needs `cec-client` (apt install cec-utils). Macs and most PCs have no CEC hardware, so this is a no-op there.
    Disable with BRAVECAST_CEC=0.
    """
    global _cec_last
    if os.environ.get("BRAVECAST_CEC") == "0":
        return
    cec = shutil.which("cec-client")
    if not cec:
        return
    with _cec_lock:
        if time.time() - _cec_last < 60:  # wake_display() is called on every play/pause; don't spam the bus
            return
        _cec_last = time.time()
        try:
            _run([cec, "-s", "-d", "1"], input=b"on 0\nas\n",
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
        except Exception as e:
            print(f"CEC wake failed: {e}")


# --- Display / power ---------------------------------------------------------

def wake_display():
    """
    Wake the display and keep the video signal active.
    The HDMI clock coming back is what makes a TV with Quick Start / auto input detect
    switch to this machine's port. On Linux/Pi we also ask the TV directly over HDMI-CEC.
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
        elif IS_LINUX:
            ensure_display_env()
            # CEC can take several seconds, so never block the cast request on it
            threading.Thread(target=cec_wake_tv, daemon=True).start()

            if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wlr-randr"):
                out = _run(["wlr-randr"], capture_output=True, text=True).stdout or ""
                for line in out.splitlines():
                    if line and not line[0].isspace():  # output names are the unindented lines
                        _run(["wlr-randr", "--output", line.split()[0], "--on"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.environ.get("DISPLAY") and shutil.which("xset"):
                _run(["xset", "dpms", "force", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _run(["xset", "s", "reset"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            elif IS_LINUX and shutil.which("systemd-inhibit"):
                self._proc = self._linux_inhibit()
        except Exception as e:
            print(f"Could not start keep-awake: {e}")

    @staticmethod
    def _linux_inhibit():
        # Hold a systemd inhibitor lock; also ignore the lid switch where polkit allows it
        for what in ("idle:sleep:handle-lid-switch", "idle:sleep"):
            proc = subprocess.Popen(
                ["systemd-inhibit", f"--what={what}", "--who=BraveCast", "--why=Media receiver",
                 "--mode=block", "sleep", "infinity"],
                start_new_session=True,
            )
            time.sleep(0.5)
            if proc.poll() is None:
                return proc
        return None

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
            if IS_LINUX:
                try:  # systemd-inhibit forks the child; kill the whole group so `sleep infinity` doesn't leak
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
                except Exception:
                    self._proc.terminate()
            else:
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

    # Remove stale Chromium singleton locks left behind by `pkill -9` or background browser updates
    for lock_name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        lock_path = os.path.join(profile_dir, lock_name)
        try:
            if os.path.lexists(lock_path):
                os.unlink(lock_path)
        except OSError:
            pass


def park_cursor():
    """
    Move the OS mouse pointer to the bottom-right edge of the screen so it never sits
    in the middle of a video on the TV (complementing CSS `cursor: none` inside the kiosk).
    Disable with BRAVECAST_PARK_CURSOR=0.
    """
    if os.environ.get("BRAVECAST_PARK_CURSOR") == "0":
        return
    try:
        if IS_MAC:
            import ctypes

            class _CGPoint(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            cg = ctypes.cdll.LoadLibrary(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            cg.CGMainDisplayID.argtypes = []
            cg.CGMainDisplayID.restype = ctypes.c_uint32
            cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]
            cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
            cg.CGDisplayPixelsHigh.argtypes = [ctypes.c_uint32]
            cg.CGDisplayPixelsHigh.restype = ctypes.c_size_t
            cg.CGWarpMouseCursorPosition.argtypes = [_CGPoint]
            cg.CGWarpMouseCursorPosition.restype = ctypes.c_int32
            cg.CGAssociateMouseAndMouseCursorPosition.argtypes = [ctypes.c_int32]
            cg.CGAssociateMouseAndMouseCursorPosition.restype = ctypes.c_int32

            main_id = cg.CGMainDisplayID()
            w = int(cg.CGDisplayPixelsWide(main_id) or 1920)
            h = int(cg.CGDisplayPixelsHigh(main_id) or 1080)
            cg.CGWarpMouseCursorPosition(_CGPoint(float(max(0, w - 2)), float(max(0, h - 2))))
            cg.CGAssociateMouseAndMouseCursorPosition(1)
        elif IS_WINDOWS:
            import ctypes

            user32 = ctypes.windll.user32
            w = int(user32.GetSystemMetrics(0) or 1920)
            h = int(user32.GetSystemMetrics(1) or 1080)
            user32.SetCursorPos(max(0, w - 2), max(0, h - 2))
        elif IS_LINUX:
            ensure_display_env()
            if os.environ.get("DISPLAY") and shutil.which("xdotool"):
                _run(["xdotool", "mousemove", "99999", "99999"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def activate_kiosk(profile_dir=None):
    """Bring the kiosk window to the front and park the mouse cursor out of view."""
    park_cursor()
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
    elif IS_LINUX:
        ensure_display_env()
        # Wayland compositors give a fresh fullscreen window focus on their own; X11 may need a nudge
        if os.environ.get("DISPLAY") and shutil.which("xdotool"):
            _run(["xdotool", "search", "--onlyvisible", "--class", "brave", "windowactivate"],
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
