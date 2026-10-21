chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());

async function badge(enabled, failed = false) {
  await chrome.action.setBadgeText({ text: failed ? 'ERR' : enabled ? 'ON' : 'OFF' });
  await chrome.action.setBadgeBackgroundColor({ color: failed ? '#9d3333' : enabled ? '#9b5422' : '#535b66' });
}

chrome.runtime.onInstalled.addListener(async () => {
  const settings = await chrome.storage.local.get(['enabled']);
  if (settings.enabled === undefined) await chrome.storage.local.set({ enabled: false, exclusions: ['localhost', '127.0.0.1'] });
  await badge(Boolean(settings.enabled));
});
chrome.storage.onChanged.addListener(async (_, area) => {
  if (area === 'local') await badge(Boolean((await chrome.storage.local.get('enabled')).enabled));
});

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.kind !== 'page' || !sender.tab?.id) return;
  (async () => {
    const { enabled, token, exclusions = [] } = await chrome.storage.local.get(['enabled', 'token', 'exclusions']);
    if (!enabled || !token || !sender.tab.active || sender.tab.incognito) return;
    const window = await chrome.windows.get(sender.tab.windowId);
    if (!window.focused) return;
    const url = new URL(sender.url);
    if (!['http:', 'https:'].includes(url.protocol) || exclusions.some(domain => url.hostname === domain || url.hostname.endsWith(`.${domain}`))) return;
    // Use browser-provided URL/title. Content scripts never receive the bearer token.
    const event = { t: new Date().toISOString(), url: sender.url, title: (sender.tab.title || '').slice(0, 500),
      scroll_y: Math.max(0, Number(message.scroll_y) || 0), viewport: message.viewport };
    const response = await fetch('http://127.0.0.1:7700/events', { method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }, body: JSON.stringify([event]) });
    if (!response.ok) throw new Error(`Receiver returned ${response.status}`);
    await badge(true);
  })().then(() => respond({ ok: true })).catch(async () => {
    await badge(true, true); respond({ ok: false });
  });
  return true;
});
