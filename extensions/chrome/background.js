// DayCube capture: records browsing to the local receiver (127.0.0.1:7700) and nowhere else.
// Records wait in a queue in extension storage until the receiver acknowledges them, so nothing
// is lost while it is starting or stopped. The vault token never reaches a page.

import { RECEIVER, allowed, clip, record, sameDocument, searchOf, trim } from './lib.js';

const BATCH = 400;
const QUEUE_LIMIT = 5000;
const GUARD_TTL = 5000; // a frame's "password field visible" report lasts this long without a refresh
const DEFAULTS = {
  enabled: false, token: '', exclusions: ['localhost', '127.0.0.1'],
  paused: false, pauseSynced: true, exclusionsSynced: false, dwellAction: 'none', showLine: false, epoch: 0,
};

const settings = () => chrome.storage.local.get(DEFAULTS);
const recording = s => Boolean(s.enabled && s.token && !s.paused);

// Pages never need this storage (they talk to the worker by message); keep it from content scripts.
chrome.storage.local.setAccessLevel?.({ accessLevel: 'TRUSTED_CONTEXTS' })?.catch?.(() => {});

// -- delivery -------------------------------------------------------------------------------------

let pending = [];
let timer = null;
let chain = Promise.resolve();

function enqueue(item) {
  pending.push(item);
  if (!timer) timer = setTimeout(() => { timer = null; flush(); }, 1000);
}

function flush() {
  chain = chain.then(deliver).catch(error => report('error', String(error)));
  return chain;
}

// Heartbeats only matter live; everything else waits for the receiver.
const durable = items => items.filter(item => item.kind !== 'view');
const hasText = items => items.some(item => item.kind === 'blocks' && item.blocks?.some(block => block.text != null));

// Pages send a block's text once. When queued text is dropped (a pause, a full queue, a refusal),
// the epoch moves on, and pages that see the new epoch send their texts again.
async function dropped(items) {
  if (!hasText(items)) return;
  const { epoch } = await settings();
  await chrome.storage.local.set({ epoch: epoch + 1 });
}

async function deliver() {
  const s = await settings();
  const { queue = [] } = await chrome.storage.local.get('queue');
  const all = queue.concat(pending.splice(0));
  let items = trim(all, QUEUE_LIMIT);
  if (items.length < all.length) {
    const kept = new Set(items);
    await dropped(all.filter(item => !kept.has(item)));
  }
  if (!recording(s)) {
    await dropped(items);
    await chrome.storage.local.set({ queue: [] });
    return refreshBadge();
  }
  while (items.length) {
    const batch = items.slice(0, BATCH);
    let response;
    try {
      response = await fetch(`${RECEIVER}/events`, {
        method: 'POST', body: JSON.stringify(batch),
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${s.token}` },
      });
    } catch {
      return keep(items, 'offline', 'The DayCube receiver is not running');
    }
    if (response.status === 409) {
      // Paused at the receiver (from this toolbar, another client or a remembered pause).
      await dropped(items);
      await chrome.storage.local.set({ queue: [], paused: true, pauseSynced: true });
      return report('paused', 'Paused');
    }
    if (response.status === 401) return keep(items, 'error', 'The receiver refused the vault token');
    if (!response.ok) return keep(items, 'error', `The receiver answered ${response.status}`);
    const body = await response.json();
    const refused = new Set((body.rejected || []).map(item => item.id));
    if (refused.size) {
      console.warn('DayCube receiver rejected records', body.rejected);
      await dropped(batch.filter(item => refused.has(item.id)));
    }
    items = items.slice(batch.length);
    await chrome.storage.local.set({ queue: durable(items) });
    act(body.signals || [], s);
    await report('ok', `Delivered ${body.accepted} records`);
  }
}

async function keep(items, status, detail) {
  await chrome.storage.local.set({ queue: durable(items) });
  await report(status, detail);
}

async function report(status, detail) {
  const { queue = [] } = await chrome.storage.local.get('queue');
  await chrome.storage.local.set({ delivery: { status, detail, at: Date.now(), queued: queue.length } });
  await refreshBadge();
}

async function refreshBadge() {
  const s = await settings();
  const { delivery } = await chrome.storage.local.get('delivery');
  const failed = delivery && ['offline', 'error'].includes(delivery.status);
  const [text, color, title] =
    !s.token ? ['OFF', '#5b6472', 'DayCube is not set up. Click to open its settings.']
      : s.paused ? ['II', '#3f6f8f', 'DayCube is paused. Click to resume (Alt+Shift+D).']
        : !s.enabled ? ['OFF', '#5b6472', 'Browser recording is off; the eye tracker may still run. Click to pause it.']
          : failed ? ['ERR', '#a33a32', `DayCube: ${delivery.detail}. Records are kept until it is back.`]
            : ['REC', '#b5651d', 'DayCube is recording. Click to pause (Alt+Shift+D).'];
  await chrome.action.setBadgeText({ text });
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setTitle({ title });
}

// -- dwell actions --------------------------------------------------------------------------------

function act(signals, s) {
  for (const signal of signals) {
    if (signal.kind !== 'dwell') continue;
    if (s.dwellAction === 'mark') {
      chrome.tabs.sendMessage(signal.tab, { signal }).catch(() => {});
    } else if (s.dwellAction === 'notify') {
      chrome.notifications.create(`dwell-${signal.doc}-${signal.block}`, {
        type: 'basic', iconUrl: 'icons/icon128.png', priority: 0,
        title: `${Math.round(signal.dwell_s)} s on one passage`,
        message: clip(signal.text || signal.title, 180),
        contextMessage: new URL(signal.url).hostname,
      });
    }
  }
}

// -- pause ----------------------------------------------------------------------------------------

async function tellReceiver(paused, token) {
  try {
    const response = await fetch(`${RECEIVER}/${paused ? 'pause' : 'resume'}`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}` },
    });
    return response.ok;
  } catch {
    return false;
  }
}

// A pause the receiver has not heard about is retried every few seconds for a minute, then on
// every tick, so the eye tracker stops as soon as the receiver is reachable.
let pauseRetry = null;
function retryPause(attempt = 0) {
  clearTimeout(pauseRetry);
  pauseRetry = setTimeout(async () => {
    const s = await settings();
    if (s.pauseSynced || !s.token) return;
    if (await tellReceiver(s.paused, s.token)) await chrome.storage.local.set({ pauseSynced: true });
    else if (attempt < 20) retryPause(attempt + 1);
  }, 3000);
}

async function setPaused(paused) {
  const s = await settings();
  if (!s.token) return chrome.runtime.openOptionsPage();
  // The pause controls the receiver, so it also stops the eye tracker when browser recording is off.
  const synced = await tellReceiver(paused, s.token);
  if (paused) {
    await dropped(pending);
    pending = [];
  }
  await chrome.storage.local.set({ paused, pauseSynced: synced, ...(paused ? { queue: [] } : {}) });
  if (!synced) retryPause();
  await refreshBadge();
  if (!paused) focusSoon();
}

const toggle = async () => setPaused(!(await settings()).paused);
chrome.action.onClicked.addListener(toggle);
chrome.commands.onCommand.addListener(command => { if (command === 'toggle-recording') toggle(); });

// The receiver remembers a pause across restarts, so its state wins, except for a pause or
// resume made here while it was unreachable, which is sent to it instead.
async function tick() {
  const s = await settings();
  if (s.token) {
    try {
      if (!s.pauseSynced) {
        if (await tellReceiver(s.paused, s.token)) await chrome.storage.local.set({ pauseSynced: true });
      } else {
        const status = await (await fetch(`${RECEIVER}/status`)).json();
        if (status.paused !== s.paused) {
          await chrome.storage.local.set({ paused: status.paused });
          if (!status.paused) focusSoon();
        }
      }
      if (!s.exclusionsSynced) await syncExclusions();
    } catch { /* offline: keep the local state */ }
  }
  await flush();
  await refreshBadge();
}

// The receiver keeps a copy of the excluded domains, so importing Opera's history leaves them out too.
async function syncExclusions() {
  const s = await settings();
  if (!s.token) return;
  try {
    const response = await fetch(`${RECEIVER}/exclusions`, {
      method: 'POST', body: JSON.stringify({ domains: s.exclusions }),
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${s.token}` },
    });
    await chrome.storage.local.set({ exclusionsSynced: response.ok });
  } catch {
    await chrome.storage.local.set({ exclusionsSynced: false });
  }
}

function ensureAlarm() {
  chrome.alarms.get('tick').then(alarm => alarm || chrome.alarms.create('tick', { periodInMinutes: 0.5 }));
}
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === 'tick') tick(); });

// -- navigation, focus, titles, idle --------------------------------------------------------------

const commits = new Map(); // tab id -> last recorded address
const searches = new Map(); // tab id -> last recorded "engine query"
const guards = new Map(); // tab id -> Map(frame id -> time a password field was last reported)

const topFrame = details => details.frameId === 0 && details.documentLifecycle !== 'prerender';

async function navigation(tabId, url, transition, qualifiers) {
  const s = await settings();
  if (!recording(s)) return;
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (!tab || tab.incognito || !allowed(url, s.exclusions)) return;
  commits.set(tabId, url);
  // At commit the tab can still show the previous page's title; a title record follows.
  const base = { tab: tabId, window: tab.windowId, url, title: '' };
  enqueue(record('page', { ...base, transition: clip(transition, 40), qualifiers: qualifiers.slice(0, 10) }));
  const search = searchOf(url);
  if (search && searches.get(tabId) !== `${search.engine} ${search.query}`) {
    searches.set(tabId, `${search.engine} ${search.query}`);
    enqueue(record('search', { ...base, ...search }));
  }
}

chrome.webNavigation.onCommitted.addListener(d => {
  if (!topFrame(d)) return;
  guards.delete(d.tabId); // a new document: its frames report again
  navigation(d.tabId, d.url, d.transitionType, d.transitionQualifiers);
});
chrome.webNavigation.onHistoryStateUpdated.addListener(d => {
  if (!topFrame(d)) return;
  // Pages that rewrite only the fragment as you scroll (section links) are not new pages.
  if (sameDocument(commits.get(d.tabId), d.url)) {
    commits.set(d.tabId, d.url);
    return;
  }
  navigation(d.tabId, d.url, d.transitionType, [...d.transitionQualifiers, 'history_state']);
});

const titles = new Map();
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.url) {
    // A page shown without a commit event (a prerendered page being activated, say).
    setTimeout(() => {
      const last = commits.get(tabId);
      if (last !== change.url && !sameDocument(last, change.url)) navigation(tabId, change.url, 'unknown', []);
    }, 1500);
  }
  if (change.title !== undefined && titles.get(tabId) !== tab.title) {
    titles.set(tabId, tab.title);
    settings().then(s => {
      if (recording(s) && !tab.incognito && allowed(tab.url, s.exclusions)) {
        enqueue(record('title', { tab: tabId, window: tab.windowId, url: tab.url, title: clip(tab.title, 1000) }));
      }
    });
  }
});
chrome.tabs.onRemoved.addListener(tabId => {
  for (const map of [commits, searches, titles, guards]) map.delete(tabId);
});

let lastFocus = '';
let focusTimer = null;

// Switching between two browser windows reports "no window" in between; wait for it to settle.
function focusSoon(windowId) {
  clearTimeout(focusTimer);
  focusTimer = setTimeout(() => recordFocus(windowId), 300);
}

async function recordFocus(windowId) {
  const s = await settings();
  if (!recording(s)) return;
  if (windowId === undefined) {
    const window = await chrome.windows.getLastFocused().catch(() => null);
    windowId = window?.focused ? window.id : chrome.windows.WINDOW_ID_NONE;
  }
  let fields = { tab: null, window: null, url: null, title: null };
  if (windowId !== chrome.windows.WINDOW_ID_NONE) {
    const [tab] = await chrome.tabs.query({ active: true, windowId });
    if (tab && !tab.incognito) {
      // An excluded page in front is recorded as "somewhere excluded", not by address.
      const shown = allowed(tab.url, s.exclusions);
      fields = { tab: tab.id, window: windowId, url: shown ? tab.url : null, title: shown ? clip(tab.title, 1000) : null };
    }
  }
  const key = JSON.stringify(fields);
  if (key === lastFocus) return;
  lastFocus = key;
  enqueue(record('focus', fields));
}

chrome.tabs.onActivated.addListener(({ windowId }) => focusSoon(windowId));
chrome.windows.onFocusChanged.addListener(windowId => focusSoon(windowId));

chrome.idle.setDetectionInterval(60);
chrome.idle.onStateChanged.addListener(async state => {
  if (recording(await settings())) enqueue(record('idle', { state }));
  if (state === 'active') focusSoon();
});

// -- from pages -----------------------------------------------------------------------------------

const number = value => (Number.isFinite(Number(value)) && Number(value) >= 0 ? Number(value) : 0);

function guarded(tabId) {
  const frames = guards.get(tabId);
  if (!frames) return false;
  const now = Date.now();
  for (const [frame, at] of frames) if (now - at > GUARD_TTL) frames.delete(frame);
  return frames.size > 0;
}

function guard(message, sender) {
  let frames = guards.get(sender.tab.id);
  if (message.on) {
    if (!frames) guards.set(sender.tab.id, (frames = new Map()));
    frames.set(sender.frameId, Date.now());
  } else {
    frames?.delete(sender.frameId);
  }
}

// Whether the page should show the line being read (the ``showLine`` option), and the line the
// receiver's decoder has the eyes on.
async function readingFor(tab) {
  const s = await settings();
  const show = Boolean(s.showLine) && recording(s) && !tab.incognito && allowed(tab.url, s.exclusions);
  if (!show || !tab.active || guarded(tab.id)) return { show, reading: null };
  const status = await (await fetch(`${RECEIVER}/status`)).json();
  return { show, reading: status.reading ?? null };
}

async function fromPage(message, tab) {
  const s = await settings();
  // Any frame of the tab showing a password field (an embedded sign-in, a payment form) silences it.
  if (!recording(s) || tab.incognito || !allowed(tab.url, s.exclusions) || guarded(tab.id)) return false;
  const base = { tab: tab.id, window: tab.windowId, url: tab.url, title: clip(tab.title, 1000) };
  switch (message?.kind) {
    case 'view':
    case 'blocks': {
      if (!tab.active || !(await chrome.windows.get(tab.windowId)).focused) return false;
      const extra = message.kind === 'blocks' ? { blocks: message.blocks } : {};
      enqueue(record(message.kind, { ...base, doc: clip(message.doc, 40), geometry: message.geometry, ...extra }));
      return true;
    }
    case 'click': {
      const href = message.href && allowed(message.href, s.exclusions) ? clip(message.href, 4096) : null;
      const point = Number.isFinite(message.x) && Number.isFinite(message.y) && message.geometry
        ? { x: message.x, y: message.y, geometry: message.geometry } : {};
      enqueue(record('click', { ...base, href, text: clip(message.text, 300), ...point }));
      return true;
    }
    case 'selection':
      enqueue(record('selection', { ...base, text: clip(message.text, 2000), copied: Boolean(message.copied) }));
      return true;
    case 'media': {
      const duration = Number(message.duration_s);
      enqueue(record('media', {
        ...base, state: message.state, position_s: number(message.position_s),
        duration_s: Number.isFinite(duration) && duration >= 0 ? duration : null,
      }));
      return true;
    }
    default:
      return false;
  }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.tab) return false;
  if (message?.kind === 'guard') {
    guard(message, sender);
    return false;
  }
  if (sender.frameId !== 0) return false;
  if (message?.kind === 'reading') {
    readingFor(sender.tab).then(respond, () => respond({ show: false, reading: null }));
    return true;
  }
  // The reply tells the page whether its record was taken, and the text epoch (see dropped()).
  Promise.all([fromPage(message, sender.tab).catch(() => false), settings()])
    .then(([ok, s]) => respond({ ok, epoch: s.epoch }), () => respond({ ok: false }));
  return true;
});

// -- start ----------------------------------------------------------------------------------------

// Pages open when the extension is installed or updated have no page script of this version (an
// update cuts the old one off), so nothing would be recorded from them until they are reloaded.
async function injectOpenTabs() {
  const tabs = await chrome.tabs.query({ url: ['http://*/*', 'https://*/*'] }).catch(() => []);
  await Promise.all(tabs.filter(tab => !tab.discarded).map(async tab => {
    await chrome.scripting.executeScript({ target: { tabId: tab.id, allFrames: true }, files: ['guard.js'] }).catch(() => {});
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ['content.js'] }).catch(() => {});
  }));
}

chrome.runtime.onInstalled.addListener(async details => {
  // Only settings that are missing: an update keeps everything the user chose.
  const stored = await chrome.storage.local.get(Object.keys(DEFAULTS));
  await chrome.storage.local.set(Object.fromEntries(Object.entries(DEFAULTS).filter(([key]) => !(key in stored))));
  ensureAlarm();
  await refreshBadge();
  syncExclusions();
  if (details.reason === 'install' || details.reason === 'update') await injectOpenTabs();
});
chrome.runtime.onStartup.addListener(() => { ensureAlarm(); focusSoon(); tick(); });
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'local') return;
  if (changes.enabled || changes.token || changes.paused) refreshBadge();
  if (changes.enabled?.newValue || changes.token?.newValue) focusSoon();
  if (changes.exclusions || changes.token?.newValue) syncExclusions();
});
ensureAlarm();
