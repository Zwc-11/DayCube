// DayCube page reporter. Runs in each page's top frame and sends the service worker:
// - what text is on screen ("blocks") and where the viewport sits on the monitor, so the
//   receiver can tell which paragraph the eye tracker's gaze falls on;
// - a heartbeat every 5 s while the page is in front;
// - clicks on links and buttons (with where they landed), text selected or copied, and media
//   play/pause/end.
// It never reads what is typed into fields, editable areas or code editors, and reports nothing
// while the page shows a password field (guard.js covers frames and shadow roots). Excluded
// domains and pausing are enforced by the service worker.

(() => {
  if (window.top !== window || window.__daycubeReporter) return;
  window.__daycubeReporter = true;

  // One load of this page. crypto.randomUUID exists only on https pages; this works on http too.
  const DOC = [...crypto.getRandomValues(new Uint8Array(8))].map(b => b.toString(16).padStart(2, '0')).join('');
  const MAX_BLOCKS = 150;
  // Where people type: fields, editable areas, and code editors that draw typed text in plain
  // elements (Monaco in Colab and github.dev, CodeMirror, Ace).
  const TYPING = [
    'textarea', '[contenteditable=""]', '[contenteditable="true" i]', '[contenteditable="plaintext-only" i]',
    '[role="textbox"]', '.monaco-editor', '.CodeMirror', '.cm-editor', '.ace_editor', '.ql-editor',
  ].join(',');
  const SKIP = [
    TYPING, 'input', 'select', 'script', 'style', 'noscript', 'template', 'option', 'svg', 'canvas', 'video', 'audio', 'iframe',
    'object', '[aria-hidden="true"]', '[data-daycube]',
  ].join(',');

  const ids = new WeakMap(); // element -> block id
  const elements = new Map(); // block id -> WeakRef(element)
  const sent = new Map(); // block id -> text the service worker accepted
  let counter = 0;
  let signature = '';
  let timer = null;
  let lastMutation = -Infinity;
  let mutationGap = 2000;
  let trailing = null;
  let epoch = null; // the service worker's text epoch when texts in `sent` were accepted
  let mouse = null;
  let zoom = null;

  const live = () => { try { return Boolean(chrome.runtime?.id); } catch { return false; } };
  const send = message => (live() ? chrome.runtime.sendMessage(message).catch(() => null) : Promise.resolve(null));
  const front = () => document.visibilityState === 'visible' && document.hasFocus() && !document.prerendering;
  const shows = root => [...root.querySelectorAll('input[type="password" i]')].some(input => input.getClientRects().length)
    || [...root.querySelectorAll('*')].some(element => element.shadowRoot && shows(element.shadowRoot));
  const guarded = () => shows(document);
  const clean = text => text.replace(/\s+/g, ' ').trim();
  const round = value => Math.round(value * 10) / 10;

  // -- where the viewport is on the screen ----------------------------------------------------------

  // DIP per CSS pixel. Two mouse samples measure it exactly; otherwise it is estimated from the
  // window's outer and inner widths, which differ by little more than the borders at 100% zoom.
  function scale() {
    if (zoom && zoom.dpr === devicePixelRatio) return zoom.value;
    const ratio = outerWidth / innerWidth;
    return Math.abs(ratio - 1) < 0.06 ? 1 : ratio;
  }

  addEventListener('mousemove', event => {
    if (!event.isTrusted) return;
    const now = performance.now();
    if (mouse && now - mouse.at < 250) return;
    const sample = {
      sx: event.screenX, sy: event.screenY, cx: event.clientX, cy: event.clientY,
      wx: screenX, wy: screenY, dpr: devicePixelRatio, at: now,
    };
    if (mouse && mouse.dpr === sample.dpr && mouse.wx === sample.wx && mouse.wy === sample.wy
        && Math.abs(sample.cx - mouse.cx) >= 100) {
      const value = (sample.sx - mouse.sx) / (sample.cx - mouse.cx);
      if (value > 0.2 && value < 6) zoom = { value: Math.round(value * 100) / 100, dpr: sample.dpr };
    }
    mouse = sample;
  }, { passive: true, capture: true });

  function geometry() {
    const z = scale();
    let vx;
    let vy;
    let exact = false;
    if (mouse && mouse.dpr === devicePixelRatio) {
      // Screen point minus client point is the viewport's origin; follow window moves since.
      vx = mouse.sx - mouse.cx * z + (screenX - mouse.wx);
      vy = mouse.sy - mouse.cy * z + (screenY - mouse.wy);
      exact = true;
    } else {
      const border = Math.max(0, (outerWidth - innerWidth * z) / 2);
      vx = screenX + border;
      vy = screenY + Math.max(0, outerHeight - innerHeight * z - border);
    }
    // The primary monitor's origin is (0, 0); its available area starts after any taskbar.
    const s = screen;
    const left = s.availLeft ?? 0;
    const top = s.availTop ?? 0;
    const primary = left >= 0 && left <= s.width - s.availWidth && top >= 0 && top <= s.height - s.availHeight;
    return {
      vx: round(primary ? vx : vx - left), vy: round(primary ? vy : vy - top), scale: z,
      inner_w: innerWidth, inner_h: innerHeight, screen_w: s.width, screen_h: s.height, primary, exact,
      scroll_y: Math.max(0, round(scrollY)), scroll_h: document.documentElement.scrollHeight,
    };
  }

  // -- the text on screen ---------------------------------------------------------------------------

  function snapshot() {
    timer = null;
    if (!front() || guarded() || !document.body) return;
    const started = performance.now();
    const vw = innerWidth;
    const vh = innerHeight;
    const styles = new Map();
    const style = element => {
      let value = styles.get(element);
      if (!value) styles.set(element, (value = getComputedStyle(element)));
      return value;
    };
    const blockOf = node => {
      for (let element = node.parentElement; element; element = element.parentElement) {
        if (element === document.body) return element;
        const display = style(element).display;
        if (!display.startsWith('inline') && display !== 'contents' && display !== 'ruby') return element;
      }
      return null;
    };
    const offscreen = rect => rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        if (node.matches(SKIP)) return NodeFilter.FILTER_REJECT;
        const rect = node.getBoundingClientRect();
        // A box with a size that lies wholly off screen shows nothing, except positioned children.
        if (rect.width > 0 && rect.height > 0 && offscreen(rect)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_SKIP;
      },
    });
    const range = document.createRange();
    const groups = new Map();
    for (let node = walker.nextNode(); node && groups.size < MAX_BLOCKS * 2; node = walker.nextNode()) {
      range.selectNodeContents(node);
      let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
      for (const rect of range.getClientRects()) {
        if (!rect.width || !rect.height || offscreen(rect)) continue;
        l = Math.min(l, rect.left); t = Math.min(t, rect.top); r = Math.max(r, rect.right); b = Math.max(b, rect.bottom);
      }
      if (l === Infinity) continue;
      const element = blockOf(node);
      if (!element) continue;
      let group = groups.get(element);
      if (!group) {
        const look = style(element);
        if (look.visibility === 'hidden' || look.opacity === '0') continue;
        groups.set(element, (group = { parts: [], l, t, r, b }));
      }
      group.parts.push(node.nodeValue);
      group.l = Math.min(group.l, l); group.t = Math.min(group.t, t);
      group.r = Math.max(group.r, r); group.b = Math.max(group.b, b);
    }
    const blocks = [];
    const texts = [];
    for (const [element, group] of groups) {
      const full = clean(group.parts.join(''));
      if (full.length < 2) continue;
      let id = ids.get(element);
      if (!id) {
        id = `b${(++counter).toString(36)}`;
        ids.set(element, id);
        elements.set(id, new WeakRef(element));
      }
      const left = Math.max(0, group.l);
      const top = Math.max(0, group.t);
      const block = {
        id, tag: element.tagName.toLowerCase().slice(0, 16),
        rect: [round(left), round(top), round(Math.min(vw, group.r) - left), round(Math.min(vh, group.b) - top)],
      };
      const text = full.slice(0, 1000);
      if (sent.get(id) !== text) {
        block.text = text;
        block.words = full.split(' ').length;
        texts.push([id, text]);
      }
      blocks.push(block);
      if (blocks.length === MAX_BLOCKS) break;
    }
    const shape = JSON.stringify([Math.round(scrollY), vw, vh, blocks.map(block => [block.id, ...block.rect])]);
    // Pages that change constantly (chats, tickers) are measured less often if measuring is slow.
    mutationGap = performance.now() - started > 40 ? 10000 : 2000;
    if (shape === signature && !texts.length) return;
    signature = shape;
    send({ kind: 'blocks', doc: DOC, geometry: geometry(), blocks }).then(reply => {
      if (!reply?.ok) { // not taken (paused, excluded): every text goes again with the next snapshot
        sent.clear();
        signature = '';
        return;
      }
      if (!follow(reply)) for (const [id, text] of texts) sent.set(id, text);
    });
    if (elements.size > 5000) for (const [id, ref] of elements) if (!ref.deref()) elements.delete(id);
  }

  // The service worker moves its epoch on when queued text was dropped: send every text again.
  function follow(reply) {
    if (reply?.epoch === undefined || reply.epoch === epoch) return false;
    const first = epoch === null;
    epoch = reply.epoch;
    if (first) return false;
    sent.clear();
    signature = '';
    schedule(300);
    return true;
  }

  function schedule(delay) {
    clearTimeout(timer);
    timer = setTimeout(snapshot, delay);
  }

  function refreshBlocks() {
    // The receiver forgets its front page on focus loss. Send positions again even
    // if this document is unchanged; its stable block ids and text cache still apply.
    signature = '';
    schedule(200);
  }

  addEventListener('scroll', () => schedule(350), { passive: true, capture: true });
  addEventListener('resize', () => { mouse = null; schedule(350); }, { passive: true });
  addEventListener('focus', refreshBlocks);
  document.addEventListener('visibilitychange', () => { if (front()) refreshBlocks(); });
  if (document.prerendering) document.addEventListener('prerenderingchange', () => schedule(300), { once: true });
  // At most one snapshot per gap for changing pages, with one more after the gap so content that
  // arrives later in it is not missed.
  new MutationObserver(() => {
    const now = performance.now();
    if (now - lastMutation >= mutationGap) {
      lastMutation = now;
      schedule(1000);
    } else if (!trailing) {
      trailing = setTimeout(() => {
        trailing = null;
        lastMutation = performance.now();
        schedule(0);
      }, lastMutation + mutationGap - now);
    }
  }).observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  setInterval(() => {
    if (front() && !guarded()) send({ kind: 'view', doc: DOC, geometry: geometry() }).then(follow);
  }, 5000);
  schedule(600);

  // -- actions --------------------------------------------------------------------------------------

  const CLICKABLE = 'a[href], button, summary, label, [role="button"], [role="link"], [role="tab"], [role="menuitem"], '
    + 'input[type="submit"], input[type="button"], input[type="checkbox"], input[type="radio"]';

  document.addEventListener('click', event => {
    if (!event.isTrusted || guarded()) return;
    const element = event.target instanceof Element ? event.target.closest(CLICKABLE) : null;
    if (!element || element.closest(TYPING)) return;
    const button = element instanceof HTMLInputElement && ['submit', 'button'].includes(element.type);
    const label = element.getAttribute('aria-label') || (element instanceof HTMLInputElement ? (button ? element.value : '') : element.innerText);
    const link = element.closest('a[href]');
    const href = link && /^https?:/.test(link.href) ? link.href : null;
    // Where the click landed: the eyes are almost always on what is clicked, so the receiver uses
    // each click to measure, and correct, the eye tracker's live error (anchors.py). A click made
    // with the keyboard (Enter on a link, Space on a button) has no position: it reports 0, 0.
    const pointer = event.detail > 0 ? { x: round(event.clientX), y: round(event.clientY), geometry: geometry() } : {};
    send({ kind: 'click', href, text: clean(label || '').slice(0, 300), ...pointer });
  }, true);

  let selectionTimer = null;
  let lastSelection = '';
  function selection(copied) {
    if (!front() || guarded()) return;
    const current = getSelection();
    if (!current || current.isCollapsed) return;
    const anchor = current.anchorNode instanceof Element ? current.anchorNode : current.anchorNode?.parentElement;
    if (anchor?.isContentEditable || anchor?.closest(TYPING)) return; // the user's own writing
    const text = clean(current.toString());
    if (text.length < 3 || (!copied && text === lastSelection)) return;
    lastSelection = text;
    send({ kind: 'selection', text: text.slice(0, 2000), copied });
  }
  document.addEventListener('selectionchange', () => {
    clearTimeout(selectionTimer);
    selectionTimer = setTimeout(() => selection(false), 900);
  });
  document.addEventListener('copy', () => selection(true), true);

  const MEDIA = { play: 'playing', pause: 'paused', ended: 'ended' };
  for (const type of Object.keys(MEDIA)) {
    document.addEventListener(type, event => {
      const media = event.target;
      // Muted autoplay (previews, ads) and short clips are not something being watched.
      if (!(media instanceof HTMLMediaElement) || media.muted || (Number.isFinite(media.duration) && media.duration < 3)) return;
      if (guarded()) return;
      send({
        kind: 'media', state: MEDIA[type], position_s: media.currentTime,
        duration_s: Number.isFinite(media.duration) ? media.duration : null,
      });
    }, true);
  }

  // -- dwell marks ----------------------------------------------------------------------------------

  // A thin bar beside the paragraph the eyes stayed on (when chosen in the options). It sits
  // outside the page's layout and ignores the pointer, and fades after a few seconds. It is an
  // element in the page, so the site's own scripts can see it.
  function mark(signal) {
    const element = elements.get(signal.block)?.deref();
    if (!element?.isConnected) return;
    const rect = element.getBoundingClientRect();
    const bar = document.createElement('div');
    bar.dataset.daycube = 'dwell';
    bar.setAttribute('aria-hidden', 'true');
    Object.assign(bar.style, {
      position: 'absolute', left: `${Math.max(0, rect.left + scrollX - 12)}px`, top: `${rect.top + scrollY}px`,
      width: '4px', height: `${Math.max(16, rect.height)}px`, background: '#d9a441', borderRadius: '2px',
      boxShadow: '0 0 0 1px rgba(255,255,255,.6)', zIndex: '2147483647', pointerEvents: 'none', opacity: '0.95',
    });
    document.documentElement.appendChild(bar);
    const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!calm) {
      bar.style.transition = 'opacity 1.2s ease 3s';
      requestAnimationFrame(() => requestAnimationFrame(() => { bar.style.opacity = '0'; }));
    }
    setTimeout(() => bar.remove(), calm ? 4000 : 4500);
  }

  chrome.runtime.onMessage.addListener(message => {
    if (message?.signal?.doc === DOC) mark(message.signal);
  });
})();
