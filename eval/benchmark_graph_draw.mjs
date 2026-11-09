/** Actual GraphCanvas redraws: synthetic graph, fixed browser, no API or personal data. */
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));
const option = (name, fallback) => process.argv.includes(name) ? process.argv[process.argv.indexOf(name) + 1] : fallback;
const output = option('--output');
if (!output) throw new Error('--output is required');
const nodes = Number(option('--nodes', 6000));
const repeats = Number(option('--repeats', 61));
if (!Number.isInteger(nodes) || nodes < 2 || !Number.isInteger(repeats) || repeats < 3) throw new Error('Invalid sizes');
const port = 13109;
const base = `http://127.0.0.1:${port}`;
let logs = '';
const server = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', `${port}`, '--strictPort'], {
  cwd: path.join(root, 'apps/viewer'), stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
});
server.stdout.on('data', (b) => { logs = (logs + b).slice(-8000); });
server.stderr.on('data', (b) => { logs = (logs + b).slice(-8000); });
let browser;
try {
  let ready = false;
  for (let i = 0; i < 150 && !ready; i++) {
    if (server.exitCode !== null) throw new Error(logs);
    try { ready = (await fetch(base)).ok; } catch { /* starting */ }
    if (!ready) await new Promise((r) => setTimeout(r, 200));
  }
  if (!ready) throw new Error('Vite did not start');
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 1 });
  await page.route('**/api/**', (route) => route.abort());
  await page.addInitScript(() => {
    window.graphDraws = 0;
    const fill = CanvasRenderingContext2D.prototype.fillRect;
    CanvasRenderingContext2D.prototype.fillRect = function (...args) { window.graphDraws++; return fill.apply(this, args); };
  });
  await page.goto(`${base}/e2e/fixtures/graph-profile.html?nodes=${nodes}`);
  await page.waitForFunction(() => window.__graph?.shown() > 0 && window.__graph.settled(), null, { timeout: 60000 });
  await page.evaluate(() => window.graphProfile.fit());
  await page.waitForFunction(() => window.__graph.settled());
  const cdp = await page.context().newCDPSession(page);
  const operations = {};
  for (const all of [false, true]) {
    await page.evaluate((all) => window.graphProfile.search(all), all);
    await page.waitForTimeout(100);
    await cdp.send('HeapProfiler.collectGarbage');
    await cdp.send('HeapProfiler.startSampling', { samplingInterval: 16384,
      includeObjectsCollectedByMajorGC: true, includeObjectsCollectedByMinorGC: true });
    const samples = await page.evaluate(async (count) => {
      const samples = [];
      for (let i = 0; i < count + 5; i++) {
        window.graphProfile.select(i % 2);
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        if (i >= 5) samples.push(window.__graph.drawMs());
      }
      return samples;
    }, repeats);
    const sampled = await cdp.send('HeapProfiler.stopSampling');
    const allocated = (node) => node.selfSize + node.children.reduce((sum, child) => sum + allocated(child), 0);
    await page.evaluate(() => window.graphProfile.select(0));
    await page.waitForTimeout(100);
    const sha256 = await page.evaluate(async () => {
      const canvas = document.querySelector('canvas');
      const bytes = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      const hash = await crypto.subtle.digest('SHA-256', bytes);
      return Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, '0')).join('');
    });
    const draws = await page.evaluate(() => window.graphDraws);
    await page.waitForTimeout(500);
    const idle = await page.evaluate(() => window.graphDraws);
    if (idle !== draws) throw new Error('Settled graph kept drawing');
    const sorted = [...samples].sort((a, b) => a - b);
    operations[all ? 'broad_search' : 'selection'] = {
      draw_ms: samples, median_ms: sorted[Math.floor(sorted.length / 2)], p95_ms: sorted[Math.floor(sorted.length * .95)],
      sampled_allocated_bytes: allocated(sampled.profile.head), sha256, idle_draws: idle - draws,
    };
  }
  const sources = ['apps/viewer/src/components/knowledge/GraphCanvas.tsx', 'apps/viewer/src/lib/noteGraph.ts',
    'apps/viewer/src/lib/graphLabels.ts',
    'apps/viewer/src/lib/graphSim.ts', 'apps/viewer/e2e/fixtures/graph-profile.tsx',
    'apps/viewer/e2e/fixtures/graph-profile.html', 'apps/viewer/src/styles/app.css',
    'apps/viewer/vite.config.ts', 'package-lock.json', 'eval/benchmark_graph_draw.mjs'];
  const system = await (await browser.newBrowserCDPSession()).send('SystemInfo.getInfo');
  const report = {
    revision: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    source_hashes: Object.fromEntries(await Promise.all(sources.map(async (p) => [p, createHash('sha256').update(await readFile(path.join(root, p))).digest('hex')]))),
    environment: { platform: os.platform(), cpu: os.cpus()[0].model, node: process.version, browser: browser.version(),
      gpu: system.gpu.devices.map(({vendorString, deviceString}) => ({vendorString, deviceString})) },
    workload: { nodes, repeats, warmups: 5, viewport: [1600, 1000], device_scale: 1 }, operations,
    limits: 'Development build, synthetic graph with deterministic saved positions, one headless Chromium run. Redraw callback durations exclude React and waiting for animation frames. Allocation sampling covers the whole page and is statistical. Pixel hashes and idle stopping are checked. No HTTP, user data or inference.',
  };
  const compare = option('--compare');
  if (compare) {
    const before = JSON.parse(await readFile(compare, 'utf8'));
    if (JSON.stringify(before.workload) !== JSON.stringify(report.workload)) throw new Error('Workload differs');
    for (const [name, value] of Object.entries(operations)) if (before.operations[name].sha256 !== value.sha256) throw new Error(`Canvas pixels differ: ${name}`);
  }
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(operations, null, 2) + '\n');
} finally {
  await browser?.close();
  server.kill();
}
