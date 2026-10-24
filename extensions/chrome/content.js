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
  const live = () => { try { return Boolean(chrome.runtime?.id); } catch { return false; } };
  // After an extension update the service worker injects this script into tabs already open. A
  // copy from before the update may still be in the page, cut off from the extension: it stays
  // inert, and this one takes over. (Versions before 0.5.0 left `true` here.)
  const previous = window.__daycubeReporter;
  if (window.top !== window || (typeof previous === 'function' && previous())) return;
  window.__daycubeReporter = live;

  // One load of this page. crypto.randomUUID exists only on https pages; this works on http too.
  const DOC = [...crypto.getRandomValues(new Uint8Array(8))].map(b => b.toString(16).padStart(2, '0')).join('');
  const MAX_BLOCKS = 150;
  const MAX_CACHED_BLOCKS = 5000;
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
  const sentLines = new Map(); // block id -> layout key of the lines the service worker accepted
  const measured = new Map(); // block id -> its lines as last measured, for the line marker
  const MAX_LINES = 60;
  let counter = 0;
  let signature = '';
  let timer = null;
  let lastMutation = -Infinity;
  let mutationGap = 2000;
  let trailing = null;
  let epoch = null; // the service worker's text epoch when texts in `sent` were accepted
  let mouse = null;
  let zoom = null;

  const send = message => (live() ? chrome.runtime.sendMessage(message).catch(() => null) : Promise.resolve(null));
  // On screen: the tab shown in a window that is not minimized or covered. The window need not have
  // focus: a page read beside another app is still read.
  const shown = () => document.visibilityState === 'visible' && !document.prerendering;
  const front = () => shown() && document.hasFocus();
  // Web components (D2L Brightspace, YouTube, Reddit, Salesforce) keep their text in shadow roots,
  // open or closed; chrome.dom reaches closed ones from a content script.
  const shadowOf = element => {
    try { return element.shadowRoot ?? chrome.dom?.openOrClosedShadowRoot?.(element) ?? null; } catch { return null; }
  };
  const shows = root => [...root.querySelectorAll('input[type="password" i]')].some(input => input.getClientRects().length)
    || [...root.querySelectorAll('*')].some(element => { const inner = shadowOf(element); return inner && shows(inner); });
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

  // The visual lines of a block, top to bottom: each line's rectangle in page coordinates (CSS px,
  // scroll included, so it holds while the page scrolls) and the exact text on it. A text node that
  // wraps is split where its characters move to the next line, found by binary search on
  // one-character ranges: about ten measurements a line. Past the time budget, the rest of a node
  // is shared among its lines by width instead.
  function linesOf(nodes, deadline) {
    const range = document.createRange();
    const fragments = []; // [rect, text] in DOM order
    for (const node of nodes) {
      const text = node.nodeValue;
      range.selectNodeContents(node);
      const rects = [...range.getClientRects()].filter(rect => rect.width && rect.height);
      if (!rects.length) continue;
      if (rects.length === 1) {
        fragments.push([rects[0], text]);
        continue;
      }
      let start = 0;
      for (let k = 0; k < rects.length; k++) {
        let end = text.length;
        if (k < rects.length - 1) {
          if (performance.now() > deadline) {
            const total = rects.slice(k).reduce((sum, rect) => sum + rect.width, 0);
            end = start + Math.round((text.length - start) * rects[k].width / total);
          } else {
            const below = rects[k].top + rects[k].height / 2; // a character at or under this is on a later line
            let lo = start + 1;
            let hi = text.length;
            while (lo < hi) {
              const mid = (lo + hi) >> 1;
              range.setStart(node, mid);
              range.setEnd(node, mid + 1);
              const box = range.getBoundingClientRect();
              if (box.height && box.top >= below) hi = mid; else lo = mid + 1;
            }
            end = lo;
          }
        }
        fragments.push([rects[k], text.slice(start, end)]);
        start = end;
      }
    }
    const lines = []; // { l, t, r, b, parts }
    for (const [rect, text] of fragments) {
      const line = lines.find(x => Math.min(x.b, rect.bottom) - Math.max(x.t, rect.top) > 0.5 * Math.min(x.b - x.t, rect.height));
      if (line) {
        line.l = Math.min(line.l, rect.left); line.r = Math.max(line.r, rect.right);
        line.t = Math.min(line.t, rect.top); line.b = Math.max(line.b, rect.bottom);
        line.parts.push(text);
      } else {
        lines.push({ l: rect.left, t: rect.top, r: rect.right, b: rect.bottom, parts: [text] });
      }
    }
    lines.sort((a, b) => a.t - b.t);
    return lines.slice(0, MAX_LINES).map(line => ({
      rect: [round(line.l + scrollX), round(line.t + scrollY), round(line.r - line.l), round(line.b - line.t)],
      text: clean(line.parts.join('')).slice(0, 300),
    }));
  }

  function snapshot() {
    timer = null;
    if (!shown() || guarded() || !document.body) return;
    const started = performance.now();
    const vw = innerWidth;
    const vh = innerHeight;
    const styles = new Map();
    const style = element => {
      let value = styles.get(element);
      if (!value) styles.set(element, (value = getComputedStyle(element)));
      return value;
    };
    // The parent across a shadow boundary: the top of a shadow tree belongs to its host.
    const up = node => node.parentElement ?? (node.parentNode instanceof ShadowRoot ? node.parentNode.host : null);
    const blockOf = node => {
      for (let element = up(node); element; element = up(element)) {
        if (element === document.body) return element;
        const display = style(element).display;
        if (!display.startsWith('inline') && display !== 'contents' && display !== 'ruby') return element;
      }
      return null;
    };
    const offscreen = rect => rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw;
    const filter = {
      acceptNode(node) {
        if (node.nodeType === Node.TEXT_NODE) return node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        if (node.matches(SKIP)) return NodeFilter.FILTER_REJECT;
        const rect = node.getBoundingClientRect();
        // A box with a size that lies wholly off screen shows nothing, except positioned children.
        if (rect.width > 0 && rect.height > 0 && offscreen(rect)) return NodeFilter.FILTER_REJECT;
        // A shadow host is handed to the loop, which walks its shadow tree; its slotted children follow.
        return shadowOf(node) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP;
      },
    };
    const range = document.createRange();
    const groups = new Map();
    const textNodes = function* walk(root, depth = 0) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, filter);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        if (node.nodeType === Node.TEXT_NODE) yield node;
        else if (depth < 30) yield* walk(shadowOf(node), depth + 1);
      }
    };
    for (const node of textNodes(document.body)) {
      if (groups.size >= MAX_BLOCKS * 2) break;
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
        groups.set(element, (group = { parts: [], nodes: [], l, t, r, b }));
      }
      group.parts.push(node.nodeValue);
      group.nodes.push(node);
      group.l = Math.min(group.l, l); group.t = Math.min(group.t, t);
      group.r = Math.max(group.r, r); group.b = Math.max(group.b, b);
    }
    const blocks = [];
    const texts = [];
    const layouts = [];
    const deadline = started + 25; // line splitting gives way to the rest of the snapshot after 25 ms
    for (const [element, group] of groups) {
      const full = clean(group.parts.join(''));
      if (full.length < 2) continue;
      let id = ids.get(element);
      if (!id) {
        id = `b${(++counter).toString(36)}`;
        ids.set(element, id);
      }
      const reference = elements.get(id) ?? new WeakRef(element);
      elements.delete(id);
      elements.set(id, reference); // oldest first, including revisits after an eviction
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
      // Lines go with a block's text, and again when the wrapping changes (width, zoom) or the
      // block moves on the page (an image above it loaded): line rectangles are page coordinates.
      const box = element.getBoundingClientRect();
      const layout = `${Math.round(box.width)}:${Math.round(box.top + scrollY)}:${devicePixelRatio}:${text.length}`;
      if (sentLines.get(id) !== layout) {
        block.lines = linesOf(group.nodes, deadline);
        measured.set(id, block.lines);
        layouts.push([id, layout]);
      }
      blocks.push(block);
      if (blocks.length === MAX_BLOCKS) break;
    }
    const shape = JSON.stringify([Math.round(scrollY), vw, vh, blocks.map(block => [block.id, ...block.rect])]);
    // Pages that change constantly (chats, tickers) are measured less often if measuring is slow.
    mutationGap = performance.now() - started > 40 ? 10000 : 2000;
    if (shape === signature && !texts.length && !layouts.length) return;
    signature = shape;
    send({ kind: 'blocks', doc: DOC, geometry: geometry(), blocks }).then(reply => {
      if (!reply?.ok) { // not taken (paused, excluded): every text goes again with the next snapshot
        sent.clear();
        sentLines.clear();
        signature = '';
        return;
      }
      if (!follow(reply)) {
        for (const [id, text] of texts) if (elements.has(id)) sent.set(id, text);
        for (const [id, layout] of layouts) if (elements.has(id)) sentLines.set(id, layout);
      }
    });
    if (elements.size > MAX_CACHED_BLOCKS) {
      const forget = id => { elements.delete(id); sent.delete(id); sentLines.delete(id); measured.delete(id); };
      for (const [id, ref] of elements) {
        if (!ref.deref()) forget(id);
        if (elements.size <= MAX_CACHED_BLOCKS) break;
      }
      while (elements.size > MAX_CACHED_BLOCKS) forget(elements.keys().next().value);
    }
  }

  // The service worker moves its epoch on when queued text was dropped: send every text again.
  function follow(reply) {
    if (reply?.epoch === undefined || reply.epoch === epoch) return false;
    const first = epoch === null;
    epoch = reply.epoch;
    if (first) return false;
    sent.clear();
    sentLines.clear();
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
  document.addEventListener('visibilitychange', () => {
    if (shown()) refreshBlocks();
    // Off screen now: the receiver stops placing gaze on this page at once, not after its heartbeat lapses.
    else if (!document.prerendering) send({ kind: 'view', doc: DOC, geometry: geometry(), hidden: true });
  });
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
    if (shown() && !guarded()) send({ kind: 'view', doc: DOC, geometry: geometry() }).then(follow);
  }, 5000);
  schedule(600);

  // -- actions --------------------------------------------------------------------------------------

  const CLICKABLE = 'a[href], button, summary, label, [role="button"], [role="link"], [role="tab"], [role="menuitem"], '
    + 'input[type="submit"], input[type="button"], input[type="checkbox"], input[type="radio"]';

  // closest() that continues from a shadow tree to its host: a click inside a web component reaches
  // the document with the host as its target.
  const closestDeep = (start, selector) => {
    for (let element = start; element; element = element.parentElement ?? element.getRootNode()?.host) {
      if (element.matches?.(selector)) return element;
    }
    return null;
  };

  document.addEventListener('click', event => {
    if (!event.isTrusted || guarded()) return;
    const origin = event.composedPath?.()[0] ?? event.target;
    const start = origin instanceof Element ? origin : origin?.parentElement;
    const element = start ? closestDeep(start, CLICKABLE) : null;
    if (!element || closestDeep(element, TYPING)) return;
    const button = element instanceof HTMLInputElement && ['submit', 'button'].includes(element.type);
    const label = element.getAttribute('aria-label') || (element instanceof HTMLInputElement ? (button ? element.value : '') : element.innerText);
    const link = closestDeep(element, 'a[href]');
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

  // -- where the eyes are, and the line being read -----------------------------------------------

  // When chosen in the options: a ring where the tracker has the eyes now, and a marker beside the
  // line the receiver decodes. The ring follows the live gaze point, which the tracker streams once
  // per camera frame while a page asks for it, and is drawn every display frame easing toward it;
  // it fades while no point arrives, and sits dashed at the window's edge when the gaze is off the
  // page. Pages ask about 30 times a second while on screen (every 10 s whether it is wanted at
  // all: storage, where the option lives, is kept from content scripts). Like the dwell bar they
  // are elements in the page, outside its layout, and ignore the pointer.
  let marker = null;
  let ring = null;
  let ringAt = 0; // when the ring last moved (Date.now())
  // The ring is drawn every display frame, easing toward the newest point (time constant
  // RING_FOLLOW_MS), so it glides instead of stepping from one update to the next; points are
  // asked for every RING_POLL_MS (the tracker streams one per camera frame).
  const RING_POLL_MS = 33;
  const RING_FOLLOW_MS = 25;
  const ringTarget = { x: 0, y: 0 };
  const ringShown = { x: null, y: null };
  let ringFrame = 0; // requestAnimationFrame id, 0 when idle
  let ringTime = 0;
  const calmMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const age = item => (item ? Date.now() - Date.parse(item.t) : Infinity);

  function overlay(kind, style) {
    const element = document.createElement('div');
    element.dataset.daycube = kind;
    element.setAttribute('aria-hidden', 'true');
    Object.assign(element.style, { zIndex: '2147483647', pointerEvents: 'none', ...style });
    return element;
  }

  function place(reading) {
    const rect = reading && reading.doc === DOC && age(reading) < 4000 ? measured.get(reading.block)?.[reading.line]?.rect : null;
    if (!rect) {
      marker?.remove();
      marker = null;
      return;
    }
    marker ??= overlay('line', {
      position: 'absolute', width: '4px', background: '#3b82f6', borderRadius: '2px',
      boxShadow: '0 0 0 1px rgba(255,255,255,.6)', transition: calmMotion() ? '' : 'top .15s ease, opacity .3s',
    });
    Object.assign(marker.style, {
      left: `${Math.max(0, rect[0] - 12)}px`, top: `${rect[1]}px`, height: `${Math.max(12, rect[3])}px`,
      opacity: `${0.4 + 0.6 * reading.p}`,
    });
    if (!marker.isConnected) document.documentElement.appendChild(marker);
  }

  // ``point``: live, in viewport px. ``fixation``: the last fixation placed on this page, for a tracker
  // that does not stream (viewport px with the page's scroll then).
  function look(point, fixation) {
    let x = null;
    let y = null;
    let inside = true;
    if (point && age(point) < 2000) {
      ({ x, y, inside } = point);
    } else if (fixation && fixation.doc === DOC && age(fixation) < 4000) {
      x = fixation.x;
      y = fixation.y + fixation.scroll_y - scrollY;
    }
    if (x !== null) {
      if (!ring) {
        ring = overlay('gaze', {
          position: 'fixed', width: '24px', height: '24px', marginLeft: '-12px', marginTop: '-12px', borderRadius: '50%',
          border: '2px solid rgba(239,68,68,.9)', background: 'rgba(239,68,68,.15)', boxShadow: '0 0 0 1px rgba(255,255,255,.7)',
          transition: 'opacity .3s', willChange: 'left, top',
        });
        ringShown.x = null; // a new ring starts where the eyes are
      }
      ringTarget.x = Math.min(Math.max(x, 0), innerWidth);
      ringTarget.y = Math.min(Math.max(y, 0), innerHeight);
      Object.assign(ring.style, { borderStyle: inside ? 'solid' : 'dashed', opacity: '1' });
      if (!ring.isConnected) document.documentElement.appendChild(ring);
      ringAt = Date.now();
      if (!ringFrame) ringFrame = requestAnimationFrame(drawRing);
    } else if (ring) {
      // No point for a moment (a blink, the face lost): fade, and go after a few seconds.
      const quiet = Date.now() - ringAt;
      if (quiet > 4000) {
        if (ringFrame) cancelAnimationFrame(ringFrame);
        ringFrame = 0;
        ring.remove();
        ring = null;
      } else if (quiet > 600) {
        ring.style.opacity = '0.35';
      }
    }
  }

  function dropRing() {
    if (ringFrame) cancelAnimationFrame(ringFrame);
    ringFrame = 0;
    ring?.remove();
    ring = null;
  }

  // One display frame of the ring: ease toward the target, and keep going until there.
  function drawRing(now) {
    ringFrame = 0;
    if (!ring) return;
    const step = ringTime ? Math.min(now - ringTime, 100) : 16;
    ringTime = now;
    if (ringShown.x === null || calmMotion()) {
      ringShown.x = ringTarget.x;
      ringShown.y = ringTarget.y;
    } else {
      const k = 1 - Math.exp(-step / RING_FOLLOW_MS);
      ringShown.x += (ringTarget.x - ringShown.x) * k;
      ringShown.y += (ringTarget.y - ringShown.y) * k;
    }
    ring.style.left = `${ringShown.x.toFixed(1)}px`;
    ring.style.top = `${ringShown.y.toFixed(1)}px`;
    if (Math.abs(ringTarget.x - ringShown.x) + Math.abs(ringTarget.y - ringShown.y) > 0.3) {
      ringFrame = requestAnimationFrame(drawRing);
    } else {
      ringTime = 0;
    }
  }

  async function watchGaze() {
    if (!live()) { place(null); look(null, null); return; } // the extension was updated or removed: this copy stops
    let wanted = false;
    if (shown() && !guarded()) {
      const reply = await send({ kind: 'reading', doc: DOC });
      wanted = Boolean(reply?.show);
      place(reply?.reading);
      if (reply?.native) dropRing(); // drawn over every window by the eye tracker
      else look(reply?.point, reply?.fixation);
    } else {
      place(null);
      look(null, null);
    }
    setTimeout(watchGaze, wanted ? RING_POLL_MS : 10000);
  }
  setTimeout(watchGaze, 1000);
})();
