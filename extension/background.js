// Stream Caster 2.0 Background Service Worker
// Automatically detects .m3u8, .mp4, obfuscated HLS playlists (VidSrc/Ramoflix/MegaCloud), and Content-Type media headers

const NOISE_PATTERNS = [
  '.vtt', '.srt', '.woff', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.css', '.js',
  'analytics', 'telemetry', 'doubleclick', 'googlesyndication', '/seg-', '/frag-', '.ts?', '.m4s?'
];

function isNoiseUrl(url) {
  const lower = url.toLowerCase();
  if (NOISE_PATTERNS.some(p => lower.includes(p))) return true;
  // Ignore individual HLS .ts or .m4s segments (we want the playlist, not 500 chunks)
  if (/\.(ts|m4s|aac)(\?|$)/i.test(lower)) return true;
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

function recordStream(details, streamTypeHint = "") {
  const url = details.url;
  if (!url || url.startsWith('chrome-extension://') || url.includes(':8099/')) return;
  if (isNoiseUrl(url)) return;

  const referer = details.documentUrl || (details.initiator ? details.initiator + '/' : '');

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

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.set({
    streams: [],
    targetHost: "http://100.111.144.16:8099"
  });
  chrome.action.setBadgeText({ text: "" });
});
