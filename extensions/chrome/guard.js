// Runs in every frame. Tells the service worker while this frame shows a password field (in its
// page, or inside a shadow root), so nothing from the tab is recorded meanwhile: not its text,
// clicks, selections or media. Covers embedded sign-in and payment frames the top page cannot see.

(() => {
  if (window.__daycubeGuard) return;
  window.__daycubeGuard = true;

  const live = () => { try { return Boolean(chrome.runtime?.id); } catch { return false; } };
  const visible = element => element.getClientRects().length > 0;

  function shows(root) {
    for (const input of root.querySelectorAll('input[type="password" i]')) if (visible(input)) return true;
    for (const element of root.querySelectorAll('*')) if (element.shadowRoot && shows(element.shadowRoot)) return true;
    return false;
  }

  let last = false;
  let soon = null;
  function check() {
    soon = null;
    if (!live()) return clearInterval(timer);
    const on = shows(document);
    // While on, the report is repeated: the worker forgets it after a few seconds without one.
    if (on || on !== last) chrome.runtime.sendMessage({ kind: 'guard', on }).catch(() => {});
    last = on;
  }
  const timer = setInterval(check, 2000);
  new MutationObserver(() => { if (!soon) soon = setTimeout(check, 250); }).observe(document.documentElement, {
    childList: true, subtree: true, attributes: true, attributeFilter: ['type', 'style', 'class', 'hidden'],
  });
  check();
})();
