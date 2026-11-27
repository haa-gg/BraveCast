// Native in-player Cast button injector for Movy.sx
(function () {
  const BTN_ID = 'bc-native-cast-btn';
  const TOAST_ID = 'bc-native-cast-toast';
  let toastTimer = null;

  function showToast(text, isError = false) {
    const shell = document.getElementById('vp-shell') || document.body;
    if (!shell) return;
    let toast = document.getElementById(TOAST_ID);
    if (!toast) {
      toast = document.createElement('div');
      toast.id = TOAST_ID;
      Object.assign(toast.style, {
        position: 'fixed',
        top: '24px',
        left: '50%',
        transform: 'translateX(-50%)',
        zIndex: '2147483647',
        padding: '10px 18px',
        borderRadius: '9999px',
        fontSize: '13px',
        fontWeight: '600',
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
        color: '#f8fafc',
        background: 'rgba(15, 23, 42, 0.92)',
        border: '1px solid rgba(168, 85, 247, 0.6)',
        boxShadow: '0 10px 30px rgba(0, 0, 0, 0.55)',
        backdropFilter: 'blur(12px)',
        pointerEvents: 'none',
        transition: 'opacity 0.25s ease'
      });
      shell.appendChild(toast);
    }
    toast.style.borderColor = isError ? 'rgba(239, 68, 68, 0.7)' : 'rgba(168, 85, 247, 0.65)';
    toast.textContent = text;
    toast.style.opacity = '1';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      if (toast) toast.style.opacity = '0';
    }, 3200);
  }

  function extractTitle() {
    const shell = document.getElementById('vp-shell');
    if (shell) {
      const heading = shell.querySelector('h1, h2, [class*="title"]');
      const mainTitle = heading ? heading.textContent.trim() : '';
      if (mainTitle && mainTitle.length < 100) return mainTitle;
    }
    const raw = (document.title || 'Movy.sx').split(' - ')[0].split(' | ')[0].trim();
    return raw || 'Movy.sx';
  }

  function buildCastIcon() {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '20');
    svg.setAttribute('height', '20');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.innerHTML =
      '<path d="M2 8V6a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-6"/>' +
      '<path d="M2 12a9 9 0 0 1 8 8"/>' +
      '<path d="M2 16a5 5 0 0 1 4 4"/>' +
      '<line x1="2" y1="20" x2="2.01" y2="20"/>';
    return svg;
  }

  function handleCastClick(e) {
    e.preventDefault();
    e.stopPropagation();
    const btn = document.getElementById(BTN_ID);
    if (btn) btn.style.color = '#c084fc';

    const video = document.querySelector('#vp-shell video') || document.querySelector('video');
    if (video && !video.paused) {
      try { video.pause(); } catch (_) {}
    }

    const title = extractTitle();
    const pageUrl = location.href;
    showToast(`📡 Casting "${title}" to BraveCast...`);

    chrome.runtime.sendMessage(
      {
        type: 'BRAVECAST_CAST',
        payload: {
          url: pageUrl,
          title: title,
          source: 'webpage',
          pageUrl: pageUrl
        }
      },
      (resp) => {
        if (chrome.runtime.lastError || !resp || !resp.ok) {
          if (btn) btn.style.color = '#f87171';
          showToast('⚠️ BraveCast receiver unreachable — open Stream Caster ⚙️', true);
          return;
        }
        if (btn) btn.style.color = '#a855f7';
        const label = resp.deviceName || 'TV';
        const msg = resp.result && resp.result.queued
          ? `✅ Queued on ${label} (#${resp.result.position})`
          : `✅ Playing on ${label}`;
        showToast(msg);
      }
    );
  }

  function injectCastButton() {
    const shell = document.getElementById('vp-shell');
    if (!shell) return;
    const fsBtn = shell.querySelector('#ButtonFullscreen');
    if (!fsBtn || !fsBtn.parentElement) return;
    if (shell.querySelector('#' + BTN_ID)) return;

    const btn = document.createElement('button');
    btn.id = BTN_ID;
    btn.type = 'button';
    btn.title = 'Cast to BraveCast TV';
    btn.setAttribute('aria-label', 'Cast to BraveCast TV');
    btn.className = 'tabbable';
    Object.assign(btn.style, {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      width: '36px',
      height: '36px',
      borderRadius: '9999px',
      border: 'none',
      background: 'transparent',
      color: '#ffffff',
      cursor: 'pointer',
      padding: '6px',
      transition: 'background-color 0.15s ease, color 0.15s ease, transform 0.1s ease'
    });
    btn.appendChild(buildCastIcon());

    btn.addEventListener('mouseenter', () => {
      btn.style.backgroundColor = 'rgba(255, 255, 255, 0.14)';
    });
    btn.addEventListener('mouseleave', () => {
      btn.style.backgroundColor = 'transparent';
    });
    btn.addEventListener('pointerdown', (e) => e.stopPropagation());
    btn.addEventListener('mousedown', (e) => e.stopPropagation());
    btn.addEventListener('click', handleCastClick);

    fsBtn.parentElement.insertBefore(btn, fsBtn);
  }

  let scheduled = false;
  function scheduleInject() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      injectCastButton();
    });
  }

  const observer = new MutationObserver(scheduleInject);
  if (document.body) {
    observer.observe(document.body, { childList: true, subtree: true });
    injectCastButton();
  } else {
    document.addEventListener('DOMContentLoaded', () => {
      observer.observe(document.body, { childList: true, subtree: true });
      injectCastButton();
    });
  }
})();
