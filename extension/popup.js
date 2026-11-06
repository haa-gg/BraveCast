// Stream Caster 2.0 Popup Controller

const DEFAULT_HOST = "http://100.111.144.16:8099";
let targetHost = DEFAULT_HOST;

const deviceDot = document.getElementById('device-dot');
const deviceStatusText = document.getElementById('device-status-text');
const refreshDeviceBtn = document.getElementById('refresh-device-btn');
const settingsSection = document.getElementById('settings-section');
const toggleSettingsBtn = document.getElementById('toggle-settings-btn');
const targetHostInput = document.getElementById('target-host-input');
const saveHostBtn = document.getElementById('save-host-btn');
const streamsContainer = document.getElementById('streams-container');
const castTabBtn = document.getElementById('cast-tab-btn');
const cleanPageBtn = document.getElementById('clean-page-btn');
const clearStreamsBtn = document.getElementById('clear-streams-btn');

// Mini Remote elements
const miniRemote = document.getElementById('mini-remote');
const remoteStatePill = document.getElementById('remote-state-pill');
const remoteTitleLabel = document.getElementById('remote-title-label');
const mBtnPlayPause = document.getElementById('m-btn-playpause');
const mBtnBack = document.getElementById('m-btn-back');
const mBtnFwd = document.getElementById('m-btn-fwd');
const mBtnStop = document.getElementById('m-btn-stop');

// Load stored target host
chrome.storage.local.get({ targetHost: DEFAULT_HOST }, (res) => {
  targetHost = res.targetHost || DEFAULT_HOST;
  targetHostInput.value = targetHost;
  checkDeviceStatus();
});

// Settings toggle
toggleSettingsBtn.addEventListener('click', () => {
  settingsSection.classList.toggle('open');
});

saveHostBtn.addEventListener('click', () => {
  let val = targetHostInput.value.trim();
  if (val && !val.startsWith('http://') && !val.startsWith('https://')) {
    val = 'http://' + val;
  }
  targetHost = val || DEFAULT_HOST;
  targetHostInput.value = targetHost;
  chrome.storage.local.set({ targetHost: targetHost });
  settingsSection.classList.remove('open');
  checkDeviceStatus();
});

refreshDeviceBtn.addEventListener('click', checkDeviceStatus);

// Check device status (auto-falls back to localhost:8099 for out-of-the-box local installs)
function checkDeviceStatus() {
  deviceDot.className = "dot";
  deviceStatusText.textContent = "Checking connection...";

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 2500);

  fetch(`${targetHost}/api/ping`, { signal: controller.signal })
    .then(r => r.json())
    .then(data => {
      clearTimeout(timeoutId);
      deviceDot.className = "dot online";
      deviceStatusText.textContent = "Online • BraveCast Ready";
      pollPlaybackStatus();
    })
    .catch(() => {
      clearTimeout(timeoutId);
      if (targetHost !== "http://localhost:8099") {
        fetch("http://localhost:8099/api/ping")
          .then(r => r.json())
          .then(() => {
            targetHost = "http://localhost:8099";
            targetHostInput.value = targetHost;
            chrome.storage.local.set({ targetHost: targetHost });
            deviceDot.className = "dot online";
            deviceStatusText.textContent = "Online • Local Receiver";
            pollPlaybackStatus();
          })
          .catch(() => {
            deviceDot.className = "dot offline";
            deviceStatusText.textContent = "Unreachable / Asleep";
            miniRemote.style.display = "none";
          });
        return;
      }
      deviceDot.className = "dot offline";
      deviceStatusText.textContent = "Unreachable / Asleep";
      miniRemote.style.display = "none";
    });
}

// Poll playback status to show mini remote
function pollPlaybackStatus() {
  fetch(`${targetHost}/api/status`)
    .then(r => r.json())
    .then(data => {
      if (data.state === 'playing' || data.state === 'paused') {
        miniRemote.style.display = "flex";
        remoteTitleLabel.textContent = (data.stream && data.stream.title) ? data.stream.title : "Live Video";
        if (data.state === 'playing') {
          remoteStatePill.textContent = "PLAYING";
          remoteStatePill.style.color = "#4ade80";
          mBtnPlayPause.textContent = "⏸️ Pause";
        } else {
          remoteStatePill.textContent = "PAUSED";
          remoteStatePill.style.color = "#eab308";
          mBtnPlayPause.textContent = "▶️ Play";
        }
      } else {
        miniRemote.style.display = "none";
      }
    })
    .catch(() => {
      miniRemote.style.display = "none";
    });
}

// Mini remote controls
function sendControl(action, value = null) {
  fetch(`${targetHost}/api/control`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action: action, value: value })
  })
  .then(() => pollPlaybackStatus())
  .catch(err => console.error(err));
}

mBtnPlayPause.addEventListener('click', () => sendControl('toggle'));
mBtnBack.addEventListener('click', () => sendControl('seek', 'back'));
mBtnFwd.addEventListener('click', () => sendControl('seek', 'fwd'));
mBtnStop.addEventListener('click', () => sendControl('stop'));

// Cast action
function castUrl(url, title, btnElement, mode = 'auto', defaultLabel = "🚀 Cast Now") {
  if (btnElement) {
    btnElement.textContent = "⏳ Sending...";
    btnElement.disabled = true;
  }

  fetch(`${targetHost}/api/cast`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url: url, title: title, mode: mode, addedBy: "Extension" })
  })
  .then(r => r.json())
  .then(res => {
    if (btnElement) {
      btnElement.textContent = res.queued ? `✅ Queued (#${res.position})` : "✅ Playing!";
      setTimeout(() => {
        btnElement.textContent = defaultLabel;
        btnElement.disabled = false;
      }, 2000);
    }
    setTimeout(pollPlaybackStatus, 800);
  })
  .catch(err => {
    alert("Cast failed. Make sure Glen's MacBook is awake and BraveCast daemon is running:\n" + err);
    if (btnElement) {
      btnElement.textContent = "❌ Failed";
      btnElement.disabled = false;
    }
  });
}

// Render streams
function renderStreams() {
  chrome.storage.local.get({ streams: [] }, (res) => {
    const streams = res.streams || [];

    if (streams.length === 0) {
      streamsContainer.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">🎬</div>
          <div class="empty-text">
            No video stream sniffed yet.<br>
            Start playback on any movie/video site!
          </div>
        </div>
      `;
      return;
    }

    streamsContainer.innerHTML = '';
    streams.forEach(stream => {
      const isHls = stream.url.includes('.m3u8');
      const ext = isHls ? "HLS" : "MP4";

      const card = document.createElement('div');
      card.className = "stream-card";

      card.innerHTML = `
        <div class="stream-card-header">
          <div class="stream-title" title="${stream.title}">${stream.title}</div>
          <span class="stream-type">${ext}</span>
        </div>
        <div class="btn-row">
          <button class="btn-beam" data-url="${encodeURIComponent(stream.url)}" data-title="${encodeURIComponent(stream.title)}">
            ➕ Jam Queue
          </button>
          <button class="btn-local btn-now" data-url="${encodeURIComponent(stream.url)}" data-title="${encodeURIComponent(stream.title)}">
            🚀 Now
          </button>
          <button class="btn-local" data-url="${encodeURIComponent(stream.url)}">
            Local
          </button>
        </div>
      `;

      // Wire Jam button
      const beamBtn = card.querySelector('.btn-beam');
      beamBtn.addEventListener('click', () => {
        castUrl(stream.url, stream.title, beamBtn, 'queue', '➕ Jam Queue');
      });

      // Wire Play Now button
      const nowBtn = card.querySelector('.btn-now');
      nowBtn.addEventListener('click', () => {
        castUrl(stream.url, stream.title, nowBtn, 'now', '🚀 Now');
      });

      // Wire local player button
      const localBtns = card.querySelectorAll('.btn-local:not(.btn-now)');
      localBtns.forEach(localBtn => {
        localBtn.addEventListener('click', () => {
          chrome.tabs.create({ url: chrome.runtime.getURL(`player.html?url=${encodeURIComponent(stream.url)}`) });
        });
      });

      streamsContainer.appendChild(card);
    });
  });
}

renderStreams();

// Cast current web page tab directly
castTabBtn.addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.url) {
    castUrl(tab.url, tab.title || "Web Page", castTabBtn);
  }
});

// Nuke ads and expand iframe (original feature preserved)
cleanPageBtn.addEventListener('click', async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab && tab.id) {
    chrome.scripting.executeScript({
      target: { tabId: tab.id },
      function: () => {
        const iframes = Array.from(document.querySelectorAll('iframe'));
        const mainIframe = iframes.find(f => f.src.includes('vid') || f.src.includes('embed') || f.src.includes('stream')) || iframes[0];

        if (mainIframe) {
          mainIframe.style.setProperty('position', 'fixed', 'important');
          mainIframe.style.setProperty('top', '0', 'important');
          mainIframe.style.setProperty('left', '0', 'important');
          mainIframe.style.setProperty('width', '100vw', 'important');
          mainIframe.style.setProperty('height', '100vh', 'important');
          mainIframe.style.setProperty('z-index', '2147483647', 'important');
          mainIframe.style.setProperty('background', '#000', 'important');
          document.body.style.setProperty('overflow', 'hidden', 'important');

          document.querySelectorAll('div').forEach(div => {
            if (parseInt(window.getComputedStyle(div).zIndex, 10) > 1000 && div !== mainIframe) {
              div.remove();
            }
          });
          alert("Iframe expanded fullscreen and ads nuked!");
        } else {
          alert("Couldn't find the video player on this page.");
        }
      }
    });
  }
});

// Clear streams
clearStreamsBtn.addEventListener('click', () => {
  chrome.storage.local.set({ streams: [] }, () => {
    chrome.action.setBadgeText({ text: "" });
    renderStreams();
  });
});
