const form = document.getElementById('settings');
const token = document.getElementById('token');
const enabled = document.getElementById('enabled');
const exclusions = document.getElementById('exclusions');
chrome.storage.local.get(['token', 'enabled', 'exclusions']).then(settings => {
  token.value = settings.token || '';
  enabled.checked = Boolean(settings.enabled);
  exclusions.value = (settings.exclusions || ['localhost', '127.0.0.1']).join('\n');
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (enabled.checked && !token.value.trim()) {
    document.getElementById('status').textContent = 'A vault token is required to enable capture.';
    return;
  }
  await chrome.storage.local.set({ token: token.value.trim(), enabled: enabled.checked,
    exclusions: exclusions.value.split('\n').map(value => value.trim().toLowerCase()).filter(Boolean) });
  document.getElementById('status').textContent = enabled.checked ? 'Saved. Browser metadata capture is enabled.' : 'Saved. Capture is disabled.';
});
