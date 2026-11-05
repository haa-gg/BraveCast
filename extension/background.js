// Stream Caster 2.0 Background Service Worker
// Automatically detects .m3u8, .mp4, and media playlist URLs

chrome.webRequest.onBeforeRequest.addListener(
  function(details) {
    const url = details.url;

    // Filter out common tracking / analytics / subtitle noise
    if (url.includes('.vtt') || url.includes('.srt') || url.includes('analytics') || url.includes('telemetry')) {
      return;
    }

    // Identify streaming files
    const isStream = url.includes('.m3u8') ||
                     url.includes('.mp4') ||
                     url.includes('/master.m3u8') ||
                     url.includes('/playlist.m3u8') ||
                     url.includes('/manifest.mpd') ||
                     url.includes('/chunklist');

    if (isStream) {
      chrome.tabs.get(details.tabId, function(tab) {
        const tabTitle = (tab && tab.title) ? tab.title : "";
        const cleanName = tabTitle ? tabTitle.split(" - ")[0].split(" | ")[0] : (url.split('/').pop().split('?')[0] || "Video Stream");

        chrome.storage.local.get({ streams: [] }, function(result) {
          let streams = result.streams;

          // Deduplicate by URL
          if (!streams.find(s => s.url === url)) {
            streams.unshift({
              url: url,
              title: cleanName,
              tabId: details.tabId,
              timestamp: Date.now()
            });

            // Keep latest 15 streams
            if (streams.length > 15) streams.pop();

            chrome.storage.local.set({ streams: streams });

            // Update icon badge
            chrome.action.setBadgeText({ text: String(streams.length) });
            chrome.action.setBadgeBackgroundColor({ color: "#a855f7" });
          }
        });
      });
    }
  },
  { urls: ["<all_urls>"] },
  []
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
