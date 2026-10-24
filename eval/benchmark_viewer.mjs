// Production viewer measurements with bundled synthetic data. Never connects to a vault/API.
import { createServer } from 'node:http';
import { readFile, writeFile, mkdir, readdir } from 'node:fs/promises';
import { cpus, platform, arch } from 'node:os';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from '@playwright/test';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const dist = path.join(root, 'apps/viewer/dist');
const output = process.argv[2] || 'test-results/viewer-profile.json';
const seconds = Number(process.argv[3] || 30);
if (!Number.isFinite(seconds) || seconds < 1) throw new Error('Playback seconds must be positive');
const mime = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2' };
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, 'http://localhost').pathname;
  if (pathname.startsWith('/api/')) {
    response.writeHead(503, { 'Content-Type': 'application/json' }).end('{"detail":"Synthetic offline profile"}');
    return;
  }
  const target = path.resolve(dist, '.' + (pathname === '/' ? '/index.html' : decodeURIComponent(pathname)));
  if (!target.startsWith(dist + path.sep)) { response.writeHead(403).end(); return; }
  try {
    const body = await readFile(target);
    response.writeHead(200, {
      'Content-Type': mime[path.extname(target)] || 'application/octet-stream',
      'Cache-Control': pathname.startsWith('/assets/') ? 'public, max-age=3600' : 'no-store',
    }).end(body);
  } catch { response.writeHead(404).end(); }
});
await new Promise((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.1', resolve); });
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
const samples = [], playback = [];
let renderer = null;
const digest = data => createHash('sha256').update(data).digest('hex');
try {
  browser = await chromium.launch();
  for (let repeat = 0; repeat < 3; repeat++) {
    const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
    const page = await context.newPage();
    await page.addInitScript(() => {
      window.profileDraws = 0;
      for (const prototype of [WebGLRenderingContext.prototype, WebGL2RenderingContext.prototype]) {
        for (const name of ['drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced']) {
          const original = prototype[name];
          if (original) prototype[name] = function (...args) { window.profileDraws++; return original.apply(this, args); };
        }
      }
      const original = CanvasRenderingContext2D.prototype.drawImage;
      CanvasRenderingContext2D.prototype.drawImage = function (...args) { window.profileDraws++; return original.apply(this, args); };
    });
    for (const [name, url] of [['calendar-cold', '/'], ['stage-cold', '/?date=2026-10-20'], ['stage-warm', '/?date=2026-10-20']]) {
      const start = performance.now();
      await page.goto(base + url);
      if (name === 'calendar-cold') await page.getByRole('button', { name: /Tuesday, October 20/ }).waitFor();
      else await page.waitForFunction(() => window.profileDraws > 0);
      const readyMs = performance.now() - start;
      const timing = await page.evaluate(() => ({
        fcp_ms: performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? null,
        resources: performance.getEntriesByType('resource').filter(r => r.name.includes('/assets/')).map(r => ({
          name: r.name.split('/').pop(), transfer_bytes: r.transferSize, encoded_bytes: r.encodedBodySize,
        })),
      }));
      samples.push({ repeat, name, ready_ms: readyMs, ...timing });
    }
    if (repeat === 0) {
      renderer = await page.evaluate(() => {
        const gl = document.querySelector('canvas').getContext('webgl2');
        const extension = gl?.getExtension('WEBGL_debug_renderer_info');
        return extension ? gl.getParameter(extension.UNMASKED_RENDERER_WEBGL) : gl?.getParameter(gl.RENDERER) ?? null;
      });
      const cdp = await context.newCDPSession(page);
      await cdp.send('Performance.enable');
      const metrics = async () => Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
      for (const engine of ['3d', '2d']) {
        if (engine === '2d') {
          await page.getByRole('button', { name: 'Settings', exact: true }).click();
          await page.getByRole('button', { name: 'Performance', exact: true }).click();
          await page.getByRole('button', { name: '2D', exact: true }).click();
          await page.getByRole('button', { name: 'Close settings', exact: true }).click();
        }
        await page.waitForTimeout(2000);
        await page.evaluate(() => { window.profileDraws = 0; });
        await page.waitForTimeout(600);
        const idleDraws = await page.evaluate(() => window.profileDraws);
        await cdp.send('HeapProfiler.collectGarbage');
        const before = await metrics();
        await page.evaluate(() => { window.profileDraws = 0; });
        await page.getByRole('button', { name: 'Play the day', exact: true }).click();
        await page.waitForTimeout(seconds * 1000);
        const after = await metrics();
        const draws = await page.evaluate(() => window.profileDraws);
        await page.getByRole('button', { name: 'Pause playback', exact: true }).click();
        await page.waitForTimeout(1000);
        await cdp.send('HeapProfiler.collectGarbage');
        const settled = await metrics();
        playback.push({ engine, seconds, idle_draws: idleDraws, playback_draws: draws,
          task_ms: 1000 * (after.TaskDuration - before.TaskDuration),
          script_ms: 1000 * (after.ScriptDuration - before.ScriptDuration),
          heap_before_bytes: before.JSHeapUsedSize, heap_after_gc_bytes: settled.JSHeapUsedSize });
      }
      await cdp.detach();
    }
    await context.close();
  }
  const assets = {};
  for (const name of (await readdir(path.join(dist, 'assets'))).filter(n => /\.(js|css)$/.test(n))) {
    const data = await readFile(path.join(dist, 'assets', name));
    assets[name] = { bytes: data.length, sha256: digest(data) };
  }
  const report = {
    revision: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    environment: { platform: platform(), arch: arch(), cpu: cpus()[0].model, node: process.version, chromium: browser.version(), renderer },
    harness_sha256: digest(await readFile(fileURLToPath(import.meta.url))),
    fixture: 'Bundled synthetic sample; every API route returns 503; uncompressed localhost static assets',
    samples, playback, assets,
    limits: 'Three cold contexts, warm stage reload within each context. Headless Chromium, no CPU/network throttle. Ready time includes Playwright polling. Short playback and post-GC JS heap do not prove hours-long stability, RSS bounds or real-GPU frame latency. No before/after viewer speed claim.',
  };
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({ samples: samples.map(({ resources, ...timing }) => timing), playback }, null, 2));
} finally {
  await browser?.close();
  await new Promise(resolve => server.close(resolve));
}
