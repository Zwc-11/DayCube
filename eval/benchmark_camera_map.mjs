/** Actual map component updates on synthetic camera paths; no API, video, GPU model or user data. */
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';

const root = fileURLToPath(new URL('../', import.meta.url));
const option = name => process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : undefined;
const output = option('--output');
if (!output) throw new Error('--output is required');
const port = 13111;
const base = `http://127.0.0.1:${port}`;
let logs = '';
const server = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', `${port}`, '--strictPort'], {
  cwd: path.join(root, 'apps/viewer'), stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
});
for (const stream of [server.stdout, server.stderr]) stream.on('data', b => { logs = (logs + b).slice(-4000); });
let browser;
try {
  let ready = false;
  for (let i = 0; i < 150 && !ready; i++) {
    if (server.exitCode !== null) throw new Error(logs);
    try { ready = (await fetch(base)).ok; } catch { /* starting */ }
    if (!ready) await new Promise(r => setTimeout(r, 200));
  }
  if (!ready) throw new Error('Vite did not start');
  browser = await chromium.launch({ headless: true });
  const operations = {};
  for (const n of [600, 12000, 50000]) {
    const page = await browser.newPage({ viewport: { width: 800, height: 600 }, deviceScaleFactor: 1 });
    await page.route('**/api/**', route => route.abort());
    // Expose the existing local component to the fixture without expanding the production API.
    await page.route(/\/src\/components\/modals\/RecordedMomentModal\.tsx(?:\?.*)?$/, async route => {
      const response = await route.fetch();
      await route.fulfill({ response, body: (await response.text()) + '\nexport { CameraMap };\n' });
    });
    await page.goto(`${base}/e2e/fixtures/camera-map.html?points=${n}`);
    await page.waitForFunction(() => window.mapProfile && document.querySelector('polyline'));
    const update = count => page.evaluate(async ({ count, n }) => {
      for (let i = 0; i < count; i++) {
        window.mapProfile.setTime(((i % 31) + 1) * n / 320);
        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
      }
    }, { count, n });
    await update(5);
    await page.evaluate(() => { window.mapProfile.samples.length = 0; });
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('HeapProfiler.collectGarbage');
    await cdp.send('HeapProfiler.startSampling', { samplingInterval: 16384,
      includeObjectsCollectedByMajorGC: true, includeObjectsCollectedByMinorGC: true });
    await update(61);
    const sampled = await cdp.send('HeapProfiler.stopSampling');
    const allocated = node => node.selfSize + node.children.reduce((sum, child) => sum + allocated(child), 0);
    await page.evaluate(n => window.mapProfile.setTime(n * .07), n);
    await page.waitForTimeout(50);
    const sha256 = await page.evaluate(async () => {
      const bytes = new TextEncoder().encode(document.querySelector('figure').outerHTML);
      return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
    });
    const samples = await page.evaluate(() => window.mapProfile.samples.slice(0, 61));
    if (samples.length !== 61) throw new Error('Missing render samples');
    const sorted = [...samples].sort((a, b) => a - b);
    operations[n] = { render_ms: samples, median_ms: sorted[30], p95_ms: sorted[57], sampled_allocated_bytes: allocated(sampled.profile.head), sha256 };
    await page.close();
  }
  const sources = ['apps/viewer/src/components/modals/RecordedMomentModal.tsx', 'apps/viewer/src/components/modals/CameraMap.tsx',
    'apps/viewer/src/lib/cameraMap.ts',
    'apps/viewer/e2e/fixtures/camera-map.html', 'apps/viewer/e2e/fixtures/camera-map.tsx',
    'eval/benchmark_camera_map.mjs', 'package-lock.json'];
  const hashes = await Promise.all(sources.map(async p => {
    try { return [p, createHash('sha256').update(await readFile(path.join(root, p))).digest('hex')]; }
    catch (error) { if (error.code === 'ENOENT') return [p, null]; throw error; }
  }));
  const report = { revision: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    source_hashes: Object.fromEntries(hashes),
    environment: { platform: os.platform(), node: process.version, browser: browser.version(), cpu: os.cpus()[0].model },
    workload: { points: [600, 12000, 50000], repeats: 61, warmups: 5, viewport: [800, 600], device_scale: 1 }, operations,
    limits: 'Development build; React Profiler update durations exclude initial preparation, SVG paint and frame waiting. Statistical allocation sampling covers the whole page, including collected objects. Exact final SVG markup is compared; no wall-time budget is asserted.' };
  const compare = option('--compare');
  if (compare) {
    const before = JSON.parse(await readFile(compare, 'utf8'));
    if (JSON.stringify(before.workload) !== JSON.stringify(report.workload)) throw new Error('Workload differs');
    for (const [name, result] of Object.entries(operations)) if (result.sha256 !== before.operations[name].sha256) throw new Error(`Map output differs: ${name}`);
  }
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(Object.fromEntries(Object.entries(operations).map(([k, v]) => [k, { median_ms: v.median_ms, p95_ms: v.p95_ms, sampled_allocated_bytes: v.sampled_allocated_bytes }]))));
} finally { await browser?.close(); server.kill(); }
