// Stream Caster 2.0 Background Service Worker
// Automatically detects .m3u8, .mp4, obfuscated HLS playlists (VidSrc/Ramoflix/MegaCloud), and Content-Type media headers

const NOISE_PATTERNS = [
  '.vtt', '.srt', '.woff', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.css', '.js',
  'analytics', 'telemetry', 'doubleclick', 'googlesyndication', '/seg-', '/frag-',
  '.ts?', '.m4s?', '/videos/intro.mp4'
];

function isNoiseUrl(url) {
  const lower = url.toLowerCase();
  if (NOISE_PATTERNS.some(p => lower.includes(p))) return true;
  // Ignore individual HLS .ts/.m4s segments and fMP4 init-* segments (we want the playlist, not chunks/init headers)
  if (/\.(ts|m4s|aac)(\?|$)/i.test(lower)) return true;
  if (/\/init-[^/]*\.(mp4|m4s)(\?|$)/i.test(lower)) return true;
  return false;
}

function isLikelyStreamUrl(url) {
  if (isNoiseUrl(url)) return false;
  const lower = url.toLowerCase();
  return (
    lower.includes('.m3u8') ||
    lower.includes('.mp4') ||
    lower.includes('/master.txt') ||
    lower.includes('/playlist.txt') ||
    lower.includes('/index.txt') ||
    lower.includes('/manifest.mpd') ||
    lower.includes('/hls/') ||
    lower.includes('/m3u8/')
  );
}

function isDrmPlatformUrl(url, referer = "") {
  const combined = (url + " " + referer).toLowerCase();
  // Amazon / Prime Video uses Widevine-encrypted MPEG-DASH (.mpd + _video_/_audio_ mp4 chunks)
  if (
    /(?:^|\/\/|[^a-z0-9])(amazon\.[a-z.]+|primevideo\.com|pv-cdn\.net|atv-ps\.amazon\.com)\//i.test(combined) &&
    (/\.mpd(\?|$)/i.test(url) || /_(?:video|audio)_\d+\.mp4(\?|$)/i.test(url) || /manifest\.mpd/i.test(url))
  ) {
    return true;
  }
  return false;
}

function recordStream(details, streamTypeHint = "") {
  const url = details.url;
  if (!url || url.startsWith('chrome-extension://') || url.includes(':8099/')) return;
  if (isNoiseUrl(url)) return;

  const referer = details.documentUrl || (details.initiator ? details.initiator + '/' : '');
  if (isDrmPlatformUrl(url, referer)) return;

  const saveWithTab = (tab) => {
    const tabTitle = (tab && tab.title) ? tab.title : "";
    const pageUrl = (tab && tab.url) ? tab.url : referer;
    const cleanName = tabTitle
      ? tabTitle.split(" - ")[0].split(" | ")[0].trim()
      : (url.split('/').pop().split('?')[0] || "Video Stream");

    chrome.storage.local.get({ streams: [] }, function(result) {
      let streams = result.streams || [];

      if (!streams.find(s => s.url === url)) {
        streams.unshift({
          url: url,
          title: cleanName,
          referer: referer || pageUrl || "",
          pageUrl: pageUrl || "",
          streamType: streamTypeHint || (url.includes('.mp4') ? 'MP4' : 'HLS'),
          tabId: details.tabId,
          timestamp: Date.now()
        });

        if (streams.length > 15) streams.pop();

        chrome.storage.local.set({ streams: streams });
        chrome.action.setBadgeText({ text: String(streams.length) });
        chrome.action.setBadgeBackgroundColor({ color: "#a855f7" });
      }
    });
  };

  if (details.tabId && details.tabId >= 0) {
    chrome.tabs.get(details.tabId, function(tab) {
      if (chrome.runtime.lastError) {
        saveWithTab(null);
      } else {
        saveWithTab(tab);
      }
    });
  } else {
    saveWithTab(null);
  }
}

chrome.webRequest.onBeforeRequest.addListener(
  function(details) {
    if (isLikelyStreamUrl(details.url)) {
      recordStream(details);
    }
  },
  { urls: ["<all_urls>"] },
  []
);

// Also catch obfuscated HLS/MP4 streams (like Ramoflix / VidSrc / MegaCloud) by Content-Type header
chrome.webRequest.onHeadersReceived.addListener(
  function(details) {
    if (!details.responseHeaders || isNoiseUrl(details.url)) return;
    const ctHeader = details.responseHeaders.find(h => h.name && h.name.toLowerCase() === 'content-type');
    if (!ctHeader || !ctHeader.value) return;
    const ct = ctHeader.value.toLowerCase();
    if (
      ct.includes('mpegurl') ||
      ct.includes('dash+xml') ||
      (ct.includes('video/mp4') && !details.url.includes('range='))
    ) {
      recordStream(details, ct.includes('mp4') ? 'MP4' : 'HLS');
    }
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// Clear streams on browser restart
chrome.runtime.onStartup.addListener(() => {
  chrome.storage.local.set({ streams: [] });
  chrome.action.setBadgeText({ text: "" });
});

const FALLBACK_HOSTS = [
  "http://192.168.1.19:8099",
  "http://100.111.144.16:8099",
  "http://localhost:8099"
];

async function pingHost(host, timeoutMs = 1800) {
  const controller = new AbortController();
  const tid = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${host}/api/ping`, { signal: controller.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const info = await res.json();
    return { host, info };
  } finally {
    clearTimeout(tid);
  }
}

async function resolveReceiverHost() {
  const stored = await chrome.storage.local.get({ targetHost: FALLBACK_HOSTS[0] });
  const primary = (stored.targetHost || FALLBACK_HOSTS[0]).replace(/\/+$/, "");
  try {
    const found = await pingHost(primary, 1600);
    await chrome.storage.local.set({ targetHost: found.host, deviceName: found.info.name || "BraveCast TV" });
    return found;
  } catch (_) {}

  const candidates = FALLBACK_HOSTS.filter(h => h !== primary);
  const found = await Promise.any(candidates.map(h => pingHost(h, 2000)));
  await chrome.storage.local.set({ targetHost: found.host, deviceName: found.info.name || "BraveCast TV" });
  return found;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return;

  if (msg.type === 'BRAVECAST_PING') {
    resolveReceiverHost()
      .then(found => sendResponse({ ok: true, host: found.host, info: found.info }))
      .catch(err => sendResponse({ ok: false, error: String(err) }));
    return true;
  }

  if (msg.type === 'BRAVECAST_CAST') {
    (async () => {
      try {
        const { host, info } = await resolveReceiverHost();
        const payload = Object.assign({
          mode: 'now',
          addedBy: 'Extension'
        }, msg.payload || {});
        const res = await fetch(`${host}/api/cast`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        sendResponse({
          ok: true,
          host,
          deviceName: info.name || 'BraveCast TV',
          result: data
        });
      } catch (err) {
        sendResponse({ ok: false, error: err && err.message ? err.message : String(err) });
      }
    })();
    return true;
  }
});

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({
    streams: [],
    targetHost: FALLBACK_HOSTS[0]
  });
  chrome.action.setBadgeText({ text: "" });
});
