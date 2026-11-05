# 🦁 BraveCast

> Turn an old Mac or Windows PC into a high-performance, ad-free media receiver powered by **Brave Browser**, with native YouTube casting, Spotify Connect, and 1-click web streaming.

---

## 📺 Overview

Commercial streaming sticks like Google Chromecast or Apple TV are locked-down and cannot block ads. **BraveCast** transforms any repurposed Mac or Windows PC (such as an older laptop or mini PC plugged into a TV via HDMI) into a dedicated TV media hub that supports:

1. **Native YouTube Casting (DIAL Protocol):** Tap the Cast button inside the official YouTube mobile app on iOS or Android, select **`Living Room TV (Mac)`**, and it launches directly into the official 10-foot YouTube TV interface in Brave—with **zero pre-roll or mid-roll ads** via Brave Shields.
2. **Native Spotify Connect:** Runs a headless `librespot` daemon in the background with CoreAudio integration. It advertises on your local Wi-Fi as a high-fidelity Spotify speaker.
3. **1-Click Web Stream Caster Extension:** A Manifest V3 Chrome/Brave extension that sniffs `.m3u8` and `.mp4` video streams from video websites and beams them directly to the TV in fullscreen.
4. **Mobile Web Remote Dashboard:** A responsive, glassmorphic web remote accessible from any phone or browser over your local network or Tailscale (`http://<ip>:8099/`).
5. **Automatic TV Wake & HDMI Signal Sync:** Wakes the display output on playback commands (`caffeinate` on macOS, `SetThreadExecutionState` on Windows), firing HDMI clock sync to auto-wake connected Smart TVs (e.g. Vizio, Samsung, LG).

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
├── receiver/                     # Runs on the TV-connected Mac
│   ├── server.py                 # Multi-threaded HTTP & SSDP DIAL Daemon
│   ├── platform_support.py       # macOS / Windows specifics (wake, keep-awake, kiosk control)
│   ├── receiver.html             # Ambient standby screen + HLS/MP4 video player
│   ├── remote.html               # Mobile and desktop Web Remote dashboard
│   ├── hls.min.js                # Bundled HLS player library
│   ├── com.glen.bravecast.plist  # macOS LaunchAgent configuration
│   ├── com.glen.librespot.plist  # Spotify Connect LaunchAgent
│   ├── on_spotify_event.sh       # Spotify playback event hook (macOS)
│   └── on_spotify_event.ps1      # Spotify playback event hook (Windows)
├── windows/
│   ├── install.ps1               # One-shot Windows setup (firewall + startup task)
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

### 1. Set Up the Receiver (macOS)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/haa-gg/BraveCast.git
   cd BraveCast
   ```

2. **Run the receiver daemon manually (to test):**
   ```bash
   python3 receiver/server.py
   ```
   * Web Remote: `http://localhost:8099/`
   * Ambient Receiver Screen: `http://localhost:8099/receiver`
   * DIAL XML: `http://localhost:8099/dd.xml`

3. **Install as a 24/7 background service (`launchd`):**
   ```bash
   mkdir -p ~/Library/LaunchAgents
   cp receiver/com.glen.bravecast.plist ~/Library/LaunchAgents/
   launchctl load -w ~/Library/LaunchAgents/com.glen.bravecast.plist
   ```

4. *(Optional)* **Enable Spotify Connect:**
   ```bash
   brew install librespot
   cp receiver/com.glen.librespot.plist ~/Library/LaunchAgents/
   launchctl load -w ~/Library/LaunchAgents/com.glen.librespot.plist
   ```

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
