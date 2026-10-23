// Pure helpers shared by the service worker and its tests (node --test extensions/chrome/tests).

export const RECEIVER = 'http://127.0.0.1:7700';

// Result pages whose address carries the query. Scholar is listed before Google, whose host
// pattern would also match it.
const ENGINES = [
  { engine: 'google_scholar', host: /^scholar\.google\.[a-z.]+$/, path: /^\/scholar$/, param: 'q' },
  { engine: 'google', host: /(^|\.)google\.[a-z.]+$/, path: /^\/search$/, param: 'q' },
  { engine: 'bing', host: /(^|\.)bing\.com$/, path: /^\/search$/, param: 'q' },
  { engine: 'duckduckgo', host: /(^|\.)duckduckgo\.com$/, path: /^\/$/, param: 'q' },
  { engine: 'brave', host: /^search\.brave\.com$/, path: /^\/search$/, param: 'q' },
  { engine: 'yahoo', host: /^search\.yahoo\.com$/, path: /^\/search/, param: 'p' },
  { engine: 'baidu', host: /^www\.baidu\.com$/, path: /^\/s$/, param: 'wd' },
  { engine: 'perplexity', host: /(^|\.)perplexity\.ai$/, path: /^\/search/, param: 'q' },
  { engine: 'youtube', host: /(^|\.)youtube\.com$/, path: /^\/results$/, param: 'search_query' },
  { engine: 'github', host: /^github\.com$/, path: /^\/search$/, param: 'q' },
  { engine: 'wikipedia', host: /\.wikipedia\.org$/, path: /^\/w\/index\.php$/, param: 'search' },
  { engine: 'reddit', host: /(^|\.)reddit\.com$/, path: /^\/search\/?$/, param: 'q' },
  { engine: 'x', host: /^(x|twitter)\.com$/, path: /^\/search$/, param: 'q' },
  { engine: 'amazon', host: /(^|\.)amazon\.[a-z.]+$/, path: /^\/s$/, param: 'k' },
];

export function searchOf(address) {
  let url;
  try { url = new URL(address); } catch { return null; }
  for (const { engine, host, path, param } of ENGINES) {
    if (!host.test(url.hostname) || !path.test(url.pathname)) continue;
    const query = (url.searchParams.get(param) || '').trim();
    return query ? { engine, query: query.slice(0, 1000) } : null;
  }
  return null;
}

/** Whether a page may be recorded: http(s), not the receiver, not an excluded domain or its subdomains. */
export function allowed(address, exclusions = []) {
  let url;
  try { url = new URL(address); } catch { return false; }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return false;
  if (url.origin === RECEIVER) return false;
  const host = url.hostname.toLowerCase();
  return !exclusions.some(domain => host === domain || host.endsWith(`.${domain}`));
}

/** Whether two addresses differ only in the fragment (#section), i.e. are the same page. */
export function sameDocument(a, b) {
  if (!a || !b) return false;
  try {
    const [x, y] = [new URL(a), new URL(b)];
    x.hash = '';
    y.hash = '';
    return x.href === y.href;
  } catch {
    return false;
  }
}

export function normalizeDomains(text) {
  return [...new Set(text.split(/[\s,]+/).map(value => value.trim().toLowerCase()
    .replace(/^[a-z]+:\/\//, '').replace(/[/:].*$/, '').replace(/^\*?\./, '')).filter(Boolean))];
}

/** Bound the offline queue: drop the oldest text snapshots first, then the oldest records. */
export function trim(queue, limit) {
  if (queue.length <= limit) return queue;
  let excess = queue.length - limit;
  const kept = queue.filter(item => !(excess > 0 && item.kind === 'blocks' && excess-- > 0));
  return kept.length > limit ? kept.slice(kept.length - limit) : kept;
}

export function record(kind, fields) {
  return { id: crypto.randomUUID(), t: new Date().toISOString(), kind, ...fields };
}

export const clip = (value, length) => String(value ?? '').slice(0, length);

export function gazeLabel(gaze = {}) {
  if (!gaze.tracking) return `Not tracking: ${gaze.reason || 'unknown'}`;
  const rate = Number.isFinite(gaze.fps) ? `, ${gaze.fps}${Number.isFinite(gaze.target_fps) ? `/${gaze.target_fps}` : ''} fps` : '';
  const face = Number.isFinite(gaze.face) ? `, face ${Math.round(gaze.face * 100)}%` : '';
  const error = Number.isFinite(gaze.error_px) ? `, calibrated to ${Math.round(gaze.error_px)} px` : '';
  return `Tracking${rate}${face}${error}`;
}
