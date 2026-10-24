import { fetchReceiver, gazeLabel, normalizeDomains, readingLabel } from './lib.js';

const $ = id => document.getElementById(id);
const DEFAULTS = {
  token: '', enabled: false, exclusions: ['localhost', '127.0.0.1'], dwellAction: 'none', showLine: false, paused: false,
};

async function load() {
  const s = await chrome.storage.local.get(DEFAULTS);
  $('token').value = s.token;
  $('enabled').checked = s.enabled;
  $('exclusions').value = s.exclusions.join('\n');
  $('showLine').checked = s.showLine;
  for (const choice of document.querySelectorAll('input[name=dwell]')) choice.checked = choice.value === s.dwellAction;
}

$('settings').addEventListener('submit', async event => {
  event.preventDefault();
  const token = $('token').value.trim();
  if ($('enabled').checked && !token) {
    $('saved').textContent = 'Paste the vault token before turning recording on.';
    return;
  }
  await chrome.storage.local.set({
    token, enabled: $('enabled').checked, exclusions: normalizeDomains($('exclusions').value),
    dwellAction: document.querySelector('input[name=dwell]:checked')?.value || 'none',
    showLine: $('showLine').checked,
  });
  $('saved').textContent = $('enabled').checked ? 'Saved. Recording is on.' : 'Saved. Recording is off.';
  await load();
  refresh();
});

function show(id, text, tone) {
  $(id).textContent = text;
  $(id).className = tone || '';
}

const ago = iso => {
  if (!iso) return 'never';
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  return seconds < 60 ? `${seconds} s ago` : `${Math.round(seconds / 60)} min ago`;
};

async function refresh() {
  const s = await chrome.storage.local.get({ ...DEFAULTS, queue: [], delivery: null });
  show('recording', !s.enabled || !s.token ? 'Off' : s.paused ? 'Paused' : 'On', s.enabled && s.token && !s.paused ? 'good' : '');
  show('queued', `${s.queue.length} record${s.queue.length === 1 ? '' : 's'}`);
  try {
    const response = await fetchReceiver('/status');
    if (!response.ok) throw new Error(`Receiver answered ${response.status}`);
    const status = response.body;
    show('receiver', status.paused ? 'Running, paused' : `Running, last record ${ago(status.browser.last)}`, 'good');
    const gaze = status.gaze;
    show('gaze', gazeLabel(gaze, status.accuracy), gaze.tracking ? 'good' : '');
    show('reading', readingLabel(status.reading, status.line_decoding), status.reading ? 'good' : '');
  } catch {
    show('receiver', 'Not running. Start it with python -m capture run --receive, or install autostart.', 'bad');
    show('gaze', '—');
    show('reading', '—');
  }
}

load();
refresh();
setInterval(refresh, 3000);
