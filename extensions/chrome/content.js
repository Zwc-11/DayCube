(() => {
  let lastSent = 0;
  function report() {
    if (document.visibilityState !== 'visible' || !document.hasFocus()) return;
    // Suppress metadata for pages with a visible password input, not just a focused one.
    if ([...document.querySelectorAll('input[type="password"]')].some(input => input.getClientRects().length)) return;
    const now = Date.now();
    if (now - lastSent < 5000) return;
    lastSent = now;
    chrome.runtime.sendMessage({ kind: 'page', scroll_y: window.scrollY,
      viewport: [window.innerWidth, window.innerHeight] }).catch(() => {});
  }
  window.addEventListener('scroll', report, { passive: true });
  window.addEventListener('focus', report);
  window.addEventListener('popstate', report);
  document.addEventListener('visibilitychange', report);
  report();
})();
