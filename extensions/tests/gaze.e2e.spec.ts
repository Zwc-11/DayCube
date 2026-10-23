// The Windows paragraph test, run without a person: example.com at 200% zoom in a real Chromium
// with the extension, the real receiver in a scratch vault, and the real tracker loop (smoother,
// fixation detector, receiver link) fed by simulated eyes and a webcam error model fitted to the
// recalibrated session's held-out error (69 px median, 133 px p90 on a 2560 px monitor).
// Needs port 7700 free. npx playwright test --config playwright.extension.config.ts
import { type ChildProcess, spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, readdirSync, existsSync, rmSync } from 'node:fs';
import { type Server, createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { type BrowserContext, type Page, type Worker, chromium, expect, test } from '@playwright/test';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const EXTENSION = path.join(ROOT, 'extensions/chrome');
const PAGES = path.join(ROOT, 'extensions/tests/pages');
const RECEIVER = 'http://127.0.0.1:7700';
const SITE = 'http://127.0.0.1:8766';
// The workstation's 2560 x 1440 monitor at 125% scaling, Opera maximized: a 2048 x 1152 DIP screen.
const SCREEN = { width: 2048, height: 1152 };

let vault: string;
let receiver: ChildProcess;
let site: Server;
let context: BrowserContext;
let worker: Worker;

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

test.beforeAll(async () => {
  vault = mkdtempSync(path.join(tmpdir(), 'daycube-gaze-'));
  site = createServer((request, response) => {
    const name = { '/example': 'example-domain.html', '/reading': 'reading.html' }[new URL(request.url || '/', SITE).pathname];
    response.writeHead(name ? 200 : 404, { 'Content-Type': name ? 'text/html' : 'text/plain' });
    response.end(name ? readFileSync(path.join(PAGES, name)) : 'not found');
  }).listen(8766, '127.0.0.1');
  // Another spec's receiver may still be shutting down on the same port.
  await until(() => fetch(`${RECEIVER}/status`).then(() => false, () => true), free => free, 20000);
  receiver = spawn('uv', ['run', 'python', '-m', 'capture', 'run', '--receive'], {
    cwd: ROOT, stdio: 'ignore',
    env: { ...process.env, DAYCUBE_VAULT_ROOT: vault, PYTHONPATH: path.join(ROOT, 'services/capture-win') },
  });
  const up = await until(() => fetch(`${RECEIVER}/status`).then(r => r.ok, () => false), ok => ok, 60000);
  expect(up && existsSync(path.join(vault, '.token')), 'our receiver started').toBe(true);
  const token = readFileSync(path.join(vault, '.token'), 'utf8').trim();
  context = await chromium.launchPersistentContext(mkdtempSync(path.join(tmpdir(), 'daycube-profile-')), {
    headless: true,
    executablePath: process.env.DAYCUBE_CHROMIUM || undefined,
    viewport: { width: SCREEN.width, height: 1017 },
    screen: SCREEN,
    args: [`--disable-extensions-except=${EXTENSION}`, `--load-extension=${EXTENSION}`],
  });
  const ours = (w: Worker) => w.url().endsWith('/background.js');
  worker = context.serviceWorkers().find(ours) ?? (await context.waitForEvent('serviceworker', { predicate: ours }));
  await until(() => worker.evaluate(() => typeof chrome.storage), type => type === 'object');
  await until(() => worker.evaluate(() => chrome.storage.local.get('epoch').then(s => s.epoch !== undefined)), ok => ok);
  await worker.evaluate(v => chrome.storage.local.set(v), {
    enabled: true, token, exclusions: ['localhost'], dwellAction: 'mark', paused: false, pauseSynced: true,
  });
});

test.afterAll(async () => {
  await context?.close();
  receiver?.kill();
  site?.close();
  if (vault) rmSync(vault, { recursive: true, force: true });
});

/** Open a page at a zoom, with the two mouse samples the reporter needs to measure where it is. */
async function open(address: string, zoom: number): Promise<Page> {
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(address);
  await page.bringToFront();
  const tab = await worker.evaluate(async url => (await chrome.tabs.query({ url }))[0].id, address);
  await worker.evaluate(([id, factor]) => chrome.tabs.setZoom(id, factor), [tab, zoom] as const);
  await page.waitForFunction(z => Math.abs(window.devicePixelRatio - z) < 0.01, zoom);
  await page.mouse.move(150, 120);
  await page.mouse.move(450, 300);
  // A zoom change re-measures the page at once, before the mouse has moved; returning focus
  // sends the positions again with the geometry the mouse measured.
  await page.waitForTimeout(500);
  await page.evaluate(() => window.dispatchEvent(new FocusEvent('focus')));
  return page;
}

/** Run the real tracker loop with simulated eyes staring at a rectangle of the monitor. */
function stare(rect: number[], seconds: number, seed: number): Promise<string> {
  return new Promise((resolve, reject) => {
    const tracker = spawn('uv', [
      'run', 'python', 'eval/gaze_sim.py', 'live', '--token-file', path.join(vault, '.token'),
      '--monitor', String(SCREEN.width), String(SCREEN.height), '--rect', ...rect.map(v => v.toFixed(1)),
      '--seconds', String(seconds), '--profile', 'recalibrated', '--seed', String(seed),
    ], { cwd: ROOT, stdio: ['ignore', 'pipe', 'inherit'] });
    let out = '';
    tracker.stdout.on('data', chunk => { out += chunk; });
    tracker.on('exit', code => (code === 0 ? resolve(out) : reject(new Error(`tracker exited with ${code}`))));
  });
}

test('a 20 s stare at the example.com paragraph at 200% zoom becomes a dwell through webcam-grade gaze', async () => {
  test.setTimeout(150000);
  const page = await open(`${SITE}/example`, 2);

  // What the extension reported: the zoom it measured and the paragraph's block id.
  const snapshot = (await until(() => lines('blocks.jsonl'),
    found => found.some(b => b.geometry.scale === 2 && b.geometry.exact && b.blocks.some((x: any) => x.text?.startsWith('This domain')))))
    .find(b => b.geometry.scale === 2 && b.geometry.exact && b.blocks.some((x: any) => x.text?.startsWith('This domain')));
  expect(snapshot, 'the reporter measured 200% zoom from the mouse').toBeTruthy();
  const body = snapshot.blocks.find((x: any) => x.text?.startsWith('This domain'));

  // Where the paragraph really is on the screen, from Chromium's own mouse coordinates rather than
  // the extension's geometry: a mouse event's screen point minus its page point times the zoom.
  await page.evaluate(() => addEventListener('mousemove', e => {
    (window as any).origin = [e.screenX - e.clientX * devicePixelRatio, e.screenY - e.clientY * devicePixelRatio];
  }, { once: true }));
  await page.mouse.move(600, 350);
  const truth = await page.evaluate(() => {
    const r = document.getElementById('body')!.getBoundingClientRect();
    return { rect: [r.left, r.top, r.width, r.height], origin: (window as any).origin as number[] };
  });
  const [ox, oy] = truth.origin;
  const rect = [ox + truth.rect[0] * 2, oy + truth.rect[1] * 2, truth.rect[2] * 2, truth.rect[3] * 2];
  expect(Math.abs(snapshot.geometry.vx - ox)).toBeLessThan(2);
  expect(Math.abs(snapshot.geometry.vy - oy)).toBeLessThan(2);
  expect(Math.abs(body.rect[1] - truth.rect[1])).toBeLessThan(3);

  // The bar beside the paragraph shows with the next heartbeat after the dwell, and fades after 4 s.
  const marked = page.locator('[data-daycube="dwell"]').waitFor({ state: 'attached', timeout: 60000 });
  await stare(rect, 20, 3);
  await marked;
  const placed = lines('gaze.jsonl');
  expect(placed.length, 'fixations arrived').toBeGreaterThan(20);
  const dwell = await until(() => lines('attention.jsonl').find(d => d.phase === 'start'), found => Boolean(found), 10000);
  expect(dwell?.block, 'the dwell is on the paragraph').toBe(body.id);
  expect(dwell.dwell_s).toBeGreaterThanOrEqual(dwell.reading_s * 2);

  const [health] = lines('health.jsonl');
  expect(health?.gaze.coverage, 'most gaze became fixations').toBeGreaterThan(0.6);
});

test('clicks carry where they landed, and a glance is not a dwell', async () => {
  test.setTimeout(90000);
  const before = lines('attention.jsonl').filter(d => d.phase === 'start').length;
  const page = await open(`${SITE}/reading`, 1);
  await until(() => lines('blocks.jsonl'), found => found.some(b => b.url.endsWith('/reading')));
  await page.evaluate(() => addEventListener('mousemove', e => {
    (window as any).origin = [e.screenX - e.clientX, e.screenY - e.clientY];
  }, { once: true }));
  await page.mouse.move(600, 350);
  const [ox, oy, left, top, width, height] = await page.evaluate(() => {
    const r = document.getElementById('p2')!.getBoundingClientRect();
    return [...(window as any).origin, r.left, r.top, r.width, r.height];
  });
  await stare([ox + left, oy + top, width, height], 5, 4); // 5 s on a 34-word paragraph: below the 8 s floor
  expect(lines('attention.jsonl').filter(d => d.phase === 'start').length).toBe(before);

  // Enter on a focused link is a click with no position: it must not become a calibration point.
  await page.focus('#next');
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/second$/);
  await page.goBack();
  const keyboard = await until(() => lines('browser.jsonl').find(r => r.kind === 'click' && r.url.endsWith('/reading')), Boolean);
  expect(keyboard).toMatchObject({ text: 'Read the next note', x: null, y: null, geometry: null });

  await page.mouse.move(600, 350);
  await page.locator('#next').scrollIntoViewIfNeeded();
  const link = await page.locator('#next').boundingBox();
  await page.mouse.click(link!.x + 10, link!.y + 5);
  const click = await until(() => lines('browser.jsonl').find(r => r.kind === 'click' && r.url.endsWith('/reading') && r.x !== null), Boolean);
  expect(Math.abs(click.x - (link!.x + 10))).toBeLessThan(1.5); // mouse events land on whole pixels
  expect(Math.abs(click.y - (link!.y + 5))).toBeLessThan(1.5);
  expect(click.geometry).toMatchObject({ scale: 1, exact: true, primary: true });
});
