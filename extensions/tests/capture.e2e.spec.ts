// The extension in a real Chromium, talking to a real receiver in a scratch vault.
// npx playwright test --config playwright.extension.config.ts
import { type ChildProcess, spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, existsSync, rmSync } from 'node:fs';
import { type Server, createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { type BrowserContext, type Page, type Worker, chromium, expect, test } from '@playwright/test';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const EXTENSION = path.join(ROOT, 'extensions/chrome');
const RECEIVER = 'http://127.0.0.1:7700';
const SITE = 'http://127.0.0.1:8765';
const SHORT = 'Gradient checkpointing trades compute for memory.';

const ARTICLE = `<!doctype html><title>Checkpointing notes</title>
<style>body{font:18px/1.6 serif;max-width:760px;margin:40px auto} p{margin:0 0 28px}</style>
<h1>Checkpointing notes</h1>
<p id="short">${SHORT}</p>
<p id="long">${'Activations are recomputed during the backward pass instead of being stored. '.repeat(12)}</p>
<p><a id="next" href="/second">Read the second page</a></p>
<audio id="clip" src="/tone.wav" controls></audio>
<div style="height:2400px"></div><p id="far">Far below the fold.</p>`;
const SECOND = '<!doctype html><title>Second page</title><h1>Second</h1><p id="body">The second page.</p>';
const LOGIN = '<!doctype html><title>Sign in</title><p id="greeting">Welcome back</p><input type="password"><button id="go">Sign in</button>';
// Content that keeps arriving, and a code editor whose typed text is drawn in plain elements.
const MIXED = `<!doctype html><title>Mixed</title><p>Opening paragraph of the mixed page.</p>
<div class="cm-editor"><div class="cm-content"><div class="cm-line">secret typed code</div></div></div>
<div id="feed"></div><script>
const add = text => { const p = document.createElement('p'); p.textContent = text; feed.append(p); };
setTimeout(() => add('First late paragraph.'), 2500);
setTimeout(() => add('Second late paragraph.'), 4000);
</script>`;
// Text kept in web components, as D2L Brightspace does: an open shadow root filled from a template
// (its light-DOM copy is never shown), a link inside it, and a closed shadow root.
const COMPONENTS = `<!doctype html><title>Assignment</title>
<style>body{font:18px/1.6 serif;max-width:760px;margin:40px auto}</style>
<h1>Assignment 2</h1>
<html-block><template><p>${'Open shadow text that wraps over several lines of this paragraph. '.repeat(5)}</p>
<p><a href="/second">Inner link</a></p></template></html-block>
<closed-block></closed-block>
<script>
customElements.define('html-block', class extends HTMLElement {
  connectedCallback() { this.attachShadow({ mode: 'open' }).append(this.querySelector('template').content.cloneNode(true)); }
});
customElements.define('closed-block', class extends HTMLElement {
  connectedCallback() { this.attachShadow({ mode: 'closed' }).innerHTML = '<p>Closed shadow paragraph text.</p>'; }
});
</script>`;
// A sign-in form in a frame from another site.
const FRAMED = `<!doctype html><title>Framed</title><p>Checkout summary for the framed page.</p>
<a id="buy" href="/second">Continue</a><iframe src="http://pay.test:8765/login" width="400" height="200"></iframe>`;

/** Four seconds of silence, so the page has something real to play. */
function tone(): Buffer {
  const rate = 8000;
  const samples = rate * 4;
  const wav = Buffer.alloc(44 + samples);
  wav.write('RIFF', 0); wav.writeUInt32LE(36 + samples, 4); wav.write('WAVEfmt ', 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate, 28); wav.writeUInt16LE(1, 32); wav.writeUInt16LE(8, 34);
  wav.write('data', 36); wav.writeUInt32LE(samples, 40); wav.fill(128, 44);
  return wav;
}

let vault: string;
let receiver: ChildProcess;
let site: Server;
let context: BrowserContext;
let worker: Worker;
let token: string;

function lines(name: string): any[] {
  const root = path.join(vault, 'capture');
  if (!existsSync(root)) return [];
  return readdirSync(root).filter(day => /^\d{4}-\d{2}-\d{2}$/.test(day)).sort().flatMap(day => {
    const file = path.join(root, day, name);
    return existsSync(file) ? readFileSync(file, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line)) : [];
  });
}

async function until<T>(read: () => T | Promise<T>, ok: (value: T) => boolean, timeout = 15000): Promise<T> {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await read();
    if (ok(value) || Date.now() > deadline) return value;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}

const settings = (values: Record<string, unknown>) => worker.evaluate(v => chrome.storage.local.set(v), values);

test.beforeAll(async () => {
  vault = mkdtempSync(path.join(tmpdir(), 'daycube-capture-'));
  site = createServer((request, response) => {
    const pages: Record<string, [string, string | Buffer]> = {
      '/article': ['text/html', ARTICLE], '/second': ['text/html', SECOND], '/login': ['text/html', LOGIN],
      '/mixed': ['text/html', MIXED], '/framed': ['text/html', FRAMED], '/components': ['text/html', COMPONENTS],
      '/tone.wav': ['audio/wav', tone()],
    };
    const page = pages[new URL(request.url || '/', SITE).pathname];
    response.writeHead(page ? 200 : 404, { 'Content-Type': page ? page[0] : 'text/plain' });
    response.end(page ? page[1] : 'not found');
  }).listen(8765, '127.0.0.1');
  receiver = spawn('uv', ['run', 'python', '-m', 'capture', 'run', '--receive'], {
    cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, DAYCUBE_VAULT_ROOT: vault, PYTHONPATH: path.join(ROOT, 'services/capture-win') },
  });
  const up = await until(() => fetch(`${RECEIVER}/status`).then(r => r.ok, () => false), ok => ok, 60000);
  expect(up, 'receiver started').toBe(true);
  token = readFileSync(path.join(vault, '.token'), 'utf8').trim();
  context = await chromium.launchPersistentContext(mkdtempSync(path.join(tmpdir(), 'daycube-profile-')), {
    headless: true,
    executablePath: process.env.DAYCUBE_CHROMIUM || undefined,
    viewport: { width: 1280, height: 800 },
    args: [
      `--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`, '--autoplay-policy=no-user-gesture-required',
      '--host-resolver-rules=MAP *.test 127.0.0.1', // plain-http sites that are not localhost
    ],
  });
  const ours = (w: Worker) => w.url().endsWith('/background.js');
  worker = context.serviceWorkers().find(ours) ?? (await context.waitForEvent('serviceworker', { predicate: ours }));
  // Extension APIs appear a moment after the worker itself.
  await until(() => worker.evaluate(() => typeof chrome.storage), type => type === 'object');
  // Settings written before the install handler has stored its defaults would be overwritten.
  await until(() => worker.evaluate(() => chrome.storage.local.get('epoch').then(s => s.epoch !== undefined)), ok => ok);
  await settings({ enabled: true, token, exclusions: ['localhost'], dwellAction: 'mark', paused: false, pauseSynced: true });
});

test.afterAll(async () => {
  await context?.close();
  receiver?.kill();
  site?.close();
  if (vault) rmSync(vault, { recursive: true, force: true });
});

async function open(address: string): Promise<Page> {
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(address);
  await page.bringToFront();
  await page.mouse.move(300, 200);
  await page.mouse.move(700, 420); // two samples: the viewport origin and zoom are measured, not guessed
  return page;
}

test('a reading session reaches the vault, and a stare marks the paragraph', async () => {
  const page = await open(`${SITE}/article`);
  const shown = await until(() => lines('blocks.jsonl'), found => found.some(b => b.blocks.some((x: any) => x.text === SHORT)));
  const snapshot = shown.find(b => b.blocks.some((x: any) => x.text === SHORT));
  expect(snapshot, 'visible text reached blocks.jsonl').toBeTruthy();
  expect(snapshot.url).toBe(`${SITE}/article`);
  expect(snapshot.geometry.exact).toBe(true);
  expect(snapshot.blocks.some((x: any) => x.text === 'Far below the fold.')).toBe(false);

  // Where the short paragraph is on the monitor, from the extension's own geometry.
  const g = snapshot.geometry;
  const block = snapshot.blocks.find((x: any) => x.text === SHORT);
  const [left, top, width, height] = block.rect;
  const x = (g.vx + (left + width / 2) * g.scale) / g.screen_w;
  const y = (g.vy + (top + height / 2) * g.scale) / g.screen_h;
  const start = Date.now() - 10000;
  const fixations = Array.from({ length: 18 }, (_, i) => ({
    id: `e2e-fixation-${i.toString().padStart(3, '0')}`, samples: 8, x, y,
    t: new Date(start + i * 500).toISOString(), t_end: new Date(start + i * 500 + 500).toISOString(),
  }));
  const gaze = await fetch(`${RECEIVER}/gaze`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    // An exact synthetic tracker: 12 px of error, so every fixation belongs to the line it is on.
    body: JSON.stringify({ state: { tracking: true, calibrated: true, error_px: 12, monitor: [g.screen_w, g.screen_h] }, fixations }),
  });
  expect(await gaze.json()).toEqual({ accepted: 18, duplicates: 0, stale: 0, live: false, ring: false, offset_grid: null });
  const placed = lines('gaze.jsonl');
  expect(placed.every(f => f.block === block.id), 'every fixation landed on the short paragraph').toBe(true);
  const dwell = lines('attention.jsonl').find(d => d.phase === 'start');
  expect(dwell?.text).toBe(SHORT);
  // The next heartbeat carries the dwell back to the page, which marks the paragraph.
  await expect(page.locator('[data-daycube="dwell"]')).toHaveCount(1, { timeout: 12000 });

  await page.evaluate(() => {
    const range = document.createRange();
    range.selectNodeContents(document.getElementById('long')!);
    getSelection()!.removeAllRanges();
    getSelection()!.addRange(range);
    document.dispatchEvent(new ClipboardEvent('copy'));
  });
  await page.evaluate(() => (document.getElementById('clip') as HTMLAudioElement).play());
  await page.waitForTimeout(1200);
  const link = (await page.locator('#next').boundingBox())!;
  await page.mouse.click(link.x + 12, link.y + 6);
  await page.waitForURL(`${SITE}/second`);
  // A search, as a result page's address (the test site stands in for Google on plain http).
  await page.goto('http://www.google.test:8765/search?q=gradient+checkpointing');
  const browser = await until(() => lines('browser.jsonl'), found => found.some(r => r.kind === 'search'));
  const kinds = new Set(browser.map(r => r.kind));
  for (const kind of ['page', 'title', 'focus', 'click', 'selection', 'media', 'search']) expect(kinds, kind).toContain(kind);
  expect(browser.find(r => r.kind === 'search')).toMatchObject({ engine: 'google', query: 'gradient checkpointing' });
  // Where the click landed, for the eye tracker's live accuracy: viewport CSS px and the geometry.
  const click = browser.find(r => r.kind === 'click');
  expect(click).toMatchObject({ href: `${SITE}/second`, text: 'Read the second page' });
  expect(Math.abs(click.x - (link.x + 12))).toBeLessThan(1.5); // mouse events land on whole pixels
  expect(Math.abs(click.y - (link.y + 6))).toBeLessThan(1.5);
  expect(click.geometry).toMatchObject({ scale: 1, exact: true, primary: true });
  expect(browser.find(r => r.kind === 'selection' && r.copied)?.text).toContain('Activations are recomputed');
  expect(browser.find(r => r.kind === 'page' && r.url.endsWith('/second'))?.transition).toBe('link');
});

test('excluded domains and password pages are left out', async () => {
  const before = lines('browser.jsonl').length;
  const login = await open(`${SITE}/login`);
  await login.waitForTimeout(1500); // long enough for a text snapshot, were one allowed
  await login.click('#go');
  await open('http://localhost:8765/second');
  await open(`${SITE}/second`);
  const browser = await until(() => lines('browser.jsonl'), found => found.slice(before).some(r => r.kind === 'page' && r.url === `${SITE}/second`));
  const recent = browser.slice(before);
  expect(recent.some(r => r.url?.includes('localhost'))).toBe(false);
  expect(recent.some(r => r.kind === 'click')).toBe(false); // the click on the sign-in page
  expect(recent.some(r => r.kind === 'page' && r.url === `${SITE}/login`)).toBe(true); // the address is still a page visit
  expect(lines('blocks.jsonl').some(b => b.url === `${SITE}/login`)).toBe(false);
});

test('plain-http pages report text as it arrives, but not what is typed in a code editor', async () => {
  await open('http://example.test:8765/mixed');
  const texts = () => lines('blocks.jsonl').filter(b => b.url.startsWith('http://example.test'))
    .flatMap(b => b.blocks.map((x: any) => x.text).filter(Boolean));
  const seen = await until(texts, found => found.includes('Second late paragraph.'), 12000);
  expect(seen).toContain('Opening paragraph of the mixed page.');
  expect(seen).toContain('First late paragraph.');
  expect(seen).toContain('Second late paragraph.'); // arrived inside the snapshot gap, still captured
  expect(seen.join(' ')).not.toContain('secret typed code');
});

test('a sign-in form inside a frame silences the page', async () => {
  const before = lines('browser.jsonl').length;
  const page = await open('http://example.test:8765/framed');
  await page.waitForTimeout(2500);
  await page.click('#buy');
  await page.waitForURL('http://example.test:8765/second');
  const recent = await until(() => lines('browser.jsonl').slice(before),
    found => found.some(r => r.kind === 'page' && r.url === 'http://example.test:8765/second'));
  expect(recent.some(r => r.kind === 'click')).toBe(false);
  expect(lines('blocks.jsonl').some(b => b.url === 'http://example.test:8765/framed')).toBe(false);
});

test('text inside web components is recorded with its lines, and links in them carry their address', async () => {
  const page = await open(`${SITE}/components`);
  const ours = (b: any) => b.url.endsWith('/components');
  const found = await until(() => lines('blocks.jsonl').filter(ours).flatMap(b => b.blocks),
    blocks => blocks.some((x: any) => x.text?.startsWith('Open shadow text')) && blocks.some((x: any) => x.text?.startsWith('Closed shadow')));
  const shadowed = found.find((x: any) => x.text?.startsWith('Open shadow text'));
  expect(shadowed, 'the open shadow root').toBeTruthy();
  expect(shadowed.lines.length, 'with its lines').toBeGreaterThanOrEqual(2);
  expect(shadowed.lines[0].text).toMatch(/^Open shadow text/);
  expect(found.some((x: any) => x.text === 'Closed shadow paragraph text.'), 'the closed shadow root').toBe(true);
  expect(found.filter((x: any) => x.text?.startsWith('Open shadow text')).length, 'not the template copy too').toBe(1);
  await page.locator('html-block a').click();
  await page.waitForURL(/\/second$/);
  const click = await until(() => lines('browser.jsonl').find(r => r.kind === 'click' && ours(r)), Boolean);
  expect(click).toMatchObject({ text: 'Inner link', href: `${SITE}/second` });
  expect(click.x).not.toBeNull();
});

test('a page beside a focused window keeps reporting what is on screen', async () => {
  const page = await open(`${SITE}/article`);
  const tab = await worker.evaluate(async url => (await chrome.tabs.query({ url }))[0], `${SITE}/article`);
  // Another window takes focus, as a terminal or a second browser window beside this one would.
  const other = await worker.evaluate(url => chrome.windows.create({ url, focused: true, width: 600, height: 500 }).then(w => w.id), `${SITE}/second`);
  await worker.evaluate(id => chrome.windows.update(id, { focused: false }), tab.windowId); // headless keeps both focused otherwise
  expect(await until(() => worker.evaluate(id => chrome.windows.get(id).then(w => w.focused), tab.windowId), focused => !focused),
    'the article\'s window lost focus').toBe(false);
  const since = Date.now();
  await page.evaluate(() => scrollTo(0, 600)); // the reader scrolls the page beside it
  const moved = await until(() => lines('blocks.jsonl').find(b => b.url.endsWith('/article') && b.geometry.scroll_y >= 500
    && Date.parse(b.t) >= since - 1000), Boolean);
  expect(moved, 'the unfocused window\'s page reported its scrolled text').toBeTruthy();
  expect(moved.window).toBe(tab.windowId);
  await worker.evaluate(id => chrome.windows.remove(id), other);
  await page.evaluate(() => scrollTo(0, 0));
});

test('records wait in the queue while the receiver refuses them, and pausing stops recording', async () => {
  await settings({ token: 'not-the-token' });
  const page = await open(`${SITE}/article`);
  await page.click('#next');
  const queued = await until(() => worker.evaluate(() => chrome.storage.local.get('queue').then(s => s.queue.length)), n => n > 0);
  expect(queued).toBeGreaterThan(0);
  expect(await worker.evaluate(() => chrome.action.getBadgeText({}))).toBe('ERR');
  await settings({ token });
  await page.goBack();
  await until(() => worker.evaluate(() => chrome.storage.local.get('queue').then(s => s.queue.length)), n => n === 0);
  const clicks = lines('browser.jsonl').filter(r => r.kind === 'click');
  expect(clicks.length).toBeGreaterThanOrEqual(2); // the one made while refused arrived late

  await fetch(`${RECEIVER}/pause`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
  await page.click('#next'); // refused with 409: the extension adopts the pause and drops it
  await until(() => worker.evaluate(() => chrome.action.getBadgeText({})), text => text === 'II');
  const count = lines('browser.jsonl').length;
  await page.goBack();
  await page.click('#next');
  await page.waitForTimeout(2000);
  expect(lines('browser.jsonl').length).toBe(count);
  await fetch(`${RECEIVER}/resume`, { method: 'POST', headers: { Authorization: `Bearer ${token}` } });
});
