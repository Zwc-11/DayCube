// An extension update with pages open: Chromium cuts the old page script off from the extension,
// so without help nothing would be recorded from those tabs until they are reloaded. The service
// worker injects the new script into them. Chromium is started here with remote debugging so the
// extension can be loaded and updated in place over the DevTools protocol (Extensions.loadUnpacked),
// as Opera does when it updates the extension or you press reload. No receiver: records wait in the
// extension's queue, where the test reads them.
// npx playwright test --config playwright.extension.config.ts update.e2e.spec.ts
import { type ChildProcess, spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { type Server, createServer } from 'node:http';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { type Browser, type BrowserContext, type CDPSession, type Worker, chromium, expect, test } from '@playwright/test';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const EXTENSION = path.join(ROOT, 'extensions/chrome');
const SITE = 'http://127.0.0.1:8767';
const PORT = 9337;
const ARTICLE = `<!doctype html><title>Open before the update</title>
<style>body{font:18px/1.6 serif;max-width:760px;margin:40px auto}</style>
<h1>Open before the update</h1><p id="body">${'A paragraph that was open in a tab when the extension was updated. '.repeat(4)}</p>`;

let profile: string;
let chrome: ChildProcess;
let site: Server;
let browser: Browser;
let context: BrowserContext;
let cdp: CDPSession;

async function until<T>(read: () => T | Promise<T>, ok: (value: T) => boolean, timeout = 15000): Promise<T> {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = await read();
    if (ok(value) || Date.now() > deadline) return value;
    await new Promise(resolve => setTimeout(resolve, 200));
  }
}

const ours = (w: Worker) => w.url().endsWith('/background.js') && w.url().includes(extensionId);
let extensionId = '';

async function load(): Promise<Worker> {
  const before = context.serviceWorkers();
  extensionId = (await cdp.send('Extensions.loadUnpacked' as any, { path: EXTENSION }) as any).id;
  const worker = await until(() => context.serviceWorkers().find(w => ours(w) && !before.includes(w)), Boolean, 20000);
  expect(worker, 'the extension service worker started').toBeTruthy();
  await until(() => worker!.evaluate(() => typeof chrome.storage).catch(() => ''), type => type === 'object');
  return worker!;
}

const queued = (worker: Worker) => worker.evaluate(() => chrome.storage.local.get({ queue: [] }).then(s => s.queue));

test.beforeAll(async () => {
  site = createServer((request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html' });
    response.end(ARTICLE);
  }).listen(8767, '127.0.0.1');
  profile = mkdtempSync(path.join(tmpdir(), 'daycube-update-'));
  const executable = process.env.DAYCUBE_CHROMIUM || chromium.executablePath();
  chrome = spawn(executable, [
    '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--no-first-run',
    '--no-sandbox', '--enable-unsafe-extension-debugging', '--window-size=1280,800', 'about:blank',
  ], { stdio: 'ignore' });
  browser = await until(() => chromium.connectOverCDP(`http://127.0.0.1:${PORT}`).catch(() => null as any), Boolean, 20000);
  context = browser.contexts()[0];
  cdp = await browser.newBrowserCDPSession();
});

test.afterAll(async () => {
  await browser?.close().catch(() => {});
  if (chrome && chrome.exitCode === null) {
    const exited = new Promise(resolve => chrome.once('exit', resolve));
    chrome.kill();
    await exited;
  }
  site?.close();
  if (profile) rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 });
});

test('after an update, a tab that was already open reports again without being reloaded', async () => {
  test.setTimeout(90000);
  let worker = await load();
  await worker.evaluate(v => chrome.storage.local.set(v), {
    enabled: true, token: 'no-receiver-here', exclusions: [], paused: false, pauseSynced: true,
  });
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(`${SITE}/article`);
  await page.bringToFront();
  await page.mouse.move(300, 200);
  await page.mouse.move(700, 420);
  const blocks = (records: any[]) => records.filter(r => r.kind === 'blocks' && r.url.endsWith('/article'));
  const first = blocks(await until(() => queued(worker), q => blocks(q).length > 0));
  expect(first.length, 'the page reports before the update').toBeGreaterThan(0);
  const before = new Set(first.map(r => r.doc));

  worker = await load(); // the same folder again: an update, which cuts the page's script off
  await page.bringToFront();
  await page.mouse.move(320, 220);
  await page.mouse.move(720, 440);
  const fresh = (q: any[]) => blocks(q).filter(r => !before.has(r.doc));
  const after = fresh(await until(() => queued(worker), q => fresh(q).length > 0, 20000));
  expect(after.length, 'the open tab reports again').toBeGreaterThan(0);
  const paragraph = after.flatMap(r => r.blocks).find((b: any) => b.text?.startsWith('A paragraph that was open'));
  expect(paragraph?.lines?.length, 'with its text and lines').toBeGreaterThanOrEqual(1);
  // One page script, not two: another snapshot later carries the same document id.
  await page.mouse.wheel(0, 60);
  await page.waitForTimeout(2500);
  expect(new Set(fresh(await queued(worker)).map(r => r.doc)).size).toBe(1);
});
