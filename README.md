# 🦁 BraveCast

> Turn an old Mac, Windows PC, Linux box or Raspberry Pi into a high-performance, ad-free media receiver powered by **Brave Browser**, with native YouTube casting, Spotify Connect, and 1-click web streaming.

---

## 📺 Overview

Commercial streaming sticks like Google Chromecast or Apple TV are locked-down and cannot block ads. **BraveCast** transforms any repurposed Mac, Windows PC, Linux machine or Raspberry Pi (such as an older laptop or mini PC plugged into a TV via HDMI) into a dedicated TV media hub that supports:

1. **Native YouTube Casting (DIAL Protocol):** Tap the Cast button inside the official YouTube mobile app on iOS or Android, select **`Living Room TV (Mac)`**, and it launches directly into the official 10-foot YouTube TV interface in Brave—with **zero pre-roll or mid-roll ads** via Brave Shields.
2. **Native Spotify Connect:** Runs a headless `librespot` daemon in the background with CoreAudio integration. It advertises on your local Wi-Fi as a high-fidelity Spotify speaker.
3. **1-Click Web Stream Caster Extension:** A Manifest V3 Chrome/Brave extension that sniffs `.m3u8` and `.mp4` video streams from video websites and beams them directly to the TV in fullscreen.
4. **Mobile Web Remote Dashboard:** A responsive, glassmorphic web remote accessible from any phone or browser over your local network or Tailscale (`http://<ip>:8099/`).
5. **Automatic TV Wake & HDMI Signal Sync:** Wakes the display output on playback commands (`caffeinate` on macOS, `SetThreadExecutionState` on Windows, DPMS / `wlr-randr` on Linux) — and on a **Raspberry Pi it uses real HDMI-CEC** to power the TV on and switch its input, firing HDMI clock sync to auto-wake connected Smart TVs (e.g. Vizio, Samsung, LG).

---

## 🏛️ Architecture

```
 ┌────────────────┐          SSDP / HTTP (DIAL)
 │  YouTube App   │ ──────────────────────────────────────┐
 └────────────────┘                                       │
                                                          ▼
 ┌────────────────┐          Spotify Connect (mDNS)   ┌───────────────────┐
 │  Spotify App   │ ────────────────────────────────► │  BraveCast Daemon │  (macOS LaunchAgent)
 └────────────────┘                                   └─────────┬─────────┘
                                                                │
 ┌────────────────┐          REST API / SSE                     │
 │  StreamCaster  │ ────────────────────────────────────────────┤
 │  MV3 Extension │                                             │
 └────────────────┘                                             │
                                                                ▼
                                      ┌───────────────────────────────────────────────────┐
                                      │ 1. Wake Display Output (caffeinate -u -t 4)       │
                                      │ 2. Launch Dedicated Brave Kiosk Window:           │
                                      │    --start-fullscreen --user-data-dir=...         │
                                      │    --user-agent="Smart TV Leanback"               │
                                      │    https://youtube.com/tv (Ad-Free)               │
                                      └─────────────────────────┬─────────────────────────┘
                                                                │  HDMI Output
                                                                ▼
                                              ┌───────────────────────────────────┐
                                              │      Vizio / Smart TV Display     │
                                              │    (Auto-Wakes & Plays Stream)    │
                                              └───────────────────────────────────┘
```

---

## 📁 Repository Structure

```text
BraveCast/
├── Install BraveCast.app          # Double-click macOS GUI Installer (zero Terminal!)
├── Install BraveCast (Mac).command # Double-click macOS Finder Installer
├── Install BraveCast (Windows).bat # Double-click Windows GUI Installer
├── install.sh                    # Universal 1-command installer (macOS / Linux / Pi)
├── receiver/                     # Runs on the TV-connected machine
│   ├── server.py                 # Multi-threaded HTTP, SSDP DIAL & Jam Queue Daemon
│   ├── autoplay.py               # Headless CDP YouTube TV autopilot & InnerTube client
│   ├── doctor.py                 # Self-healing runtime diagnostics & health check
│   ├── platform_support.py       # macOS / Windows / Linux specifics (wake, CEC, keep-awake, kiosk control)
│   ├── receiver.html             # Ambient standby screen + HLS/MP4 video player
│   ├── remote.html               # Mobile and desktop Web Remote + BraveCast Jam dashboard
│   ├── hls.min.js                # Bundled HLS player library
│   ├── com.glen.bravecast.plist  # macOS LaunchAgent template
│   ├── com.glen.librespot.plist  # Spotify Connect LaunchAgent template
│   ├── on_spotify_event.sh       # Spotify playback event hook (macOS / Linux)
│   └── on_spotify_event.ps1      # Spotify playback event hook (Windows)
├── macos/
│   ├── install.sh                # Automated macOS installer (supports --gui and CLI)
│   └── uninstall.sh              # One-shot macOS uninstaller
├── linux/
│   ├── install.sh                # One-shot Linux / Raspberry Pi setup (systemd user service)
│   └── uninstall.sh
├── windows/
│   ├── install.ps1               # One-shot Windows setup (supports -Gui, winget, firewall + startup task)
│   └── uninstall.ps1
├── extension/                    # StreamCaster 2.0 Browser Extension
│   ├── manifest.json             # Manifest V3 configuration
│   ├── background.js             # Network stream sniffer & icon badge updater
│   ├── popup.html                # Dark glassmorphic popup UI
│   ├── popup.js                  # In-popup mini remote & 1-click casting
│   ├── player.html               # Clean local stream player fallback
│   └── icons/                    # Extension icons (16px, 48px, 128px)
├── deploy_to_mbp2.sh             # Deployment sync script for remote Mac
└── README.md
```

---

## 🚀 Quickstart Guide

### ✨ Option A: Easy Double-Click Install (No Command Line Needed!)

Don't want to touch a terminal? Just [Download the ZIP](https://github.com/haa-gg/BraveCast/archive/refs/heads/main.zip), unzip it, and double-click:

* **🍎 On a Mac:** Double-click **`Install BraveCast.app`** (or **`Install BraveCast (Mac).command`**).
  * *(First time tip: If macOS says the app is from an unidentified developer, **Right-Click → Open** and click **Open**).*
  * A friendly popup asks what you want to name your TV, automatically downloads & installs **Brave Browser** if you don't have it yet, installs BraveCast into `~/Library/Application Support/BraveCast`, sets it to start automatically at login, and opens your Web Remote!
* **🪟 On Windows 10 / 11:** Double-click **`Install BraveCast (Windows).bat`**.
  * Click **Yes** on the Administrator prompt, pick a name for your TV in the popup box, and the wizard automatically installs **Python 3** and **Brave Browser** (via `winget` if missing), sets your Wi-Fi to Private so phones can discover your PC, opens the firewall, and launches your Web Remote!

---

### ⚡ Option B: Universal 1-Line Terminal Install (macOS / Linux / Raspberry Pi)

```bash
git clone https://github.com/haa-gg/BraveCast.git && cd BraveCast && bash install.sh
```

---

### 1a. Manual / CLI Setup (macOS)

1. **Automated CLI install:**
   ```bash
   bash macos/install.sh --name "Living Room TV" --install-brave
   ```
   *(Remove anytime with `bash macos/uninstall.sh`)*

2. **Or run the receiver daemon manually (to test):**
   ```bash
   python3 receiver/server.py
   ```
   * Web Remote & Jam Queue: `http://localhost:8099/`
   * Ambient Receiver Screen: `http://localhost:8099/receiver`
   * Self-Diagnostic Doctor: `python3 receiver/doctor.py`

---

### 1b. Set Up the Receiver (Windows 10 / 11)

1. Install [Python 3](https://www.python.org/downloads/) (tick **Add python.exe to PATH**) and [Brave](https://brave.com/download/).
2. Clone the repo, then run the installer from an **Administrator** PowerShell:
   ```powershell
   git clone https://github.com/haa-gg/BraveCast.git
   cd BraveCast
   powershell -ExecutionPolicy Bypass -File windows\install.ps1
   ```
   It opens the firewall (TCP 8099, UDP 1900), registers a `BraveCast` scheduled task that starts at logon, and starts the receiver.
3. To test without installing, just run `python receiver\server.py`.

**Windows notes**
* The receiver must run in your **logged-in desktop session** (that's why it's a logon task rather than a service) — enable auto-login and disable the lock screen on a dedicated TV PC.
* Your network must be set to **Private** or phones won't discover the PC.
* Set the HDMI output as the default audio device (Settings → System → Sound).
* *(Optional)* Spotify Connect: build [librespot](https://github.com/librespot-org/librespot) (`cargo install librespot`) and run it with `--name "Living Room TV (PC)" --onevent "powershell -File <repo>\receiver\on_spotify_event.ps1"`.
* Override defaults with environment variables: `BRAVECAST_BROWSER` (path to brave.exe), `BRAVECAST_NAME` (cast target name), `BRAVECAST_PORT`.
* Remove everything with `windows\uninstall.ps1`.

---

### 1c. Set Up the Receiver (Raspberry Pi / Linux)

**Hardware:** a Raspberry Pi 4 (4 GB) or **Pi 5** running **64-bit Raspberry Pi OS (Bookworm) with the desktop**, or any Debian/Ubuntu-style desktop machine. Brave only ships 64-bit ARM/x86 Linux builds — on 32-bit Pi OS, BraveCast falls back to Chromium (works, but no Brave Shields ad blocking).

1. Clone the repo and run the installer as your **normal user** (it calls `sudo` where needed):
   ```bash
   git clone https://github.com/haa-gg/BraveCast.git
   cd BraveCast
   bash linux/install.sh --install-brave --autologin
   ```
   * `--install-brave` installs Brave using Brave's official script. Skip it if Brave is already installed.
   * `--autologin` makes a Raspberry Pi boot straight to the desktop (the kiosk needs a desktop session to draw on).
2. The installer also installs `cec-utils`, turns off screen blanking on Raspberry Pi OS, opens `ufw` if it's active, and registers a `bravecast` **systemd user service** that starts at boot.
3. Check it: `curl http://localhost:8099/api/ping` · logs: `journalctl --user -u bravecast -f`
4. Remove it with `bash linux/uninstall.sh`.

**Raspberry Pi bonus — real HDMI-CEC.** Unlike a Mac, the Pi's HDMI port has CEC hardware. On every cast BraveCast sends `on 0` + `as` through `cec-client`, which powers the TV on **and makes the Pi the active source, switching the TV's HDMI input for you**. Make sure CEC is enabled in your TV's menu (Vizio: *System → CEC*, Samsung: *Anynet+*, LG: *SIMPLINK*). Set `BRAVECAST_CEC=0` to disable it.

**Linux notes**
* Works on **Wayland** (Pi OS Bookworm's default) and **X11**. Brave is launched with `--ozone-platform-hint=auto` and `--password-store=basic` so there's no keyring prompt.
* **Performance:** Brave decodes video in software on a Pi. 1080p YouTube is fine on a Pi 5; a Pi 4 may drop frames on 1080p60 — if so, pick 720p/1080p30 or use a faster machine.
* Audio goes out over HDMI by default; pick the output in the desktop volume menu if not.
* *(Optional)* Spotify Connect: install [Raspotify](https://github.com/dtcooper/raspotify) (a packaged librespot) and point its event hook at `receiver/on_spotify_event.sh` (`LIBRESPOT_ONEVENT=<repo>/receiver/on_spotify_event.sh` in `/etc/raspotify/conf`).
* Env overrides: `BRAVECAST_BROWSER` (path to the browser), `BRAVECAST_NAME` (cast target name), `BRAVECAST_PORT`, `BRAVECAST_CEC=0`.
* If the machine is a **laptop**, the receiver asks systemd to ignore the lid switch (where polkit allows), so you can close the lid.

---

### 2. Install the StreamCaster Browser Extension

1. Open **Brave** or **Google Chrome** on your computer.
2. Navigate to `brave://extensions` (or `chrome://extensions`).
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** (top left).
5. Select the [`extension/`](extension/) directory inside this repository.
6. Pin **Stream Caster** to your browser toolbar!

---

## 📱 How to Use

### Casting YouTube from iOS or Android
1. Connect your phone to the same Wi-Fi network as the Mac.
2. Open the regular **YouTube** app.
3. Tap the native **Cast button** on any video.
4. Select **`Living Room TV (Mac)`**.
5. The Mac wakes the display, opens Brave in fullscreen kiosk mode with YouTube TV, and plays your video **without any ads**.

### Casting Music from Spotify
1. Open the **Spotify** app on your phone, tablet, or laptop.
2. Tap the **Devices / Speaker** icon ("Connect to a device").
3. Select **`Living Room TV (Mac)`**.
4. Playback starts immediately through CoreAudio/HDMI.

### Casting Web Video Streams (Movies, Shows, Sports)
1. On your laptop, navigate to any streaming site.
2. Start the video. The StreamCaster icon badge will show the count of sniffed streams.
3. Click the extension and tap **🚀 Cast to Glen's MacBook**.
4. The stream immediately beams to the TV. You can control playback via the in-popup mini remote or the mobile dashboard at `http://<laptop-ip>:8099/`.

---

## ⚙️ TV Hardware Tips

* **Vizio TVs:**
  * **System $\rightarrow$ Power Mode:** Set to **Quick Start** *(keeps HDMI signal sensing active when the screen is dark)*.
  * **System $\rightarrow$ CEC:** Ensure CEC is **Enabled**.
* **Samsung TVs:** Enable **Anynet+ (HDMI-CEC)** and **Auto Power Link**.
* **LG TVs:** Enable **Simplink (HDMI-CEC)** and **Auto Power Sync**.
* **Sony TVs:** Enable **BRAVIA Sync** and **Auto Devices Power On**.

---

## 📜 License

[MIT License](LICENSE) © 2026 Glen Gengel
