/** Synthetic loader failure/early-close resource counters; no API, images or user data. */
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';

const root = fileURLToPath(new URL('../', import.meta.url));
const output = process.argv[process.argv.indexOf('--output') + 1];
if (!process.argv.includes('--output') || !output) throw new Error('--output is required');
const port = 13110;
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
  const results = {};
  const positions = Buffer.from(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]).buffer);
  const triangle = { asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 } }] }],
    buffers: [{ uri: `data:application/octet-stream;base64,${positions.toString('base64')}`, byteLength: positions.length }],
    bufferViews: [{ buffer: 0, byteLength: positions.length }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3', min: [0, 0, 0], max: [1, 1, 0] }] };
  for (const scenario of ['mesh_failure', 'mesh_closed_while_loading', 'splat_failure']) {
    const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
    await page.route('**/api/**', route => route.abort());
    // Count actual renderer creation/disposal/draw calls in the transformed module, without
    // adding diagnostics or a test branch to production code. Fail if the insertion point drifts.
    await page.route(/\/src\/components\/modals\/(?:Mesh|Splat)View\.tsx(?:\?.*)?$/, async route => {
      const response = await route.fetch();
      const source = await response.text();
      const marker = 'el.appendChild(renderer.domElement);';
      if (!source.includes(marker)) throw new Error('Renderer instrumentation marker changed');
      await route.fulfill({ response, body: source.replace(marker, `${marker}
        window.sceneCounters.created++;
        const originalDispose = renderer.dispose.bind(renderer);
        renderer.dispose = () => { window.sceneCounters.disposed++; originalDispose(); };
        const originalRender = renderer.render.bind(renderer);
        renderer.render = (...args) => { window.sceneCounters.renders++; return originalRender(...args); };
      `) });
    });
    let release = () => {};
    let arrived;
    const request = new Promise(resolve => { arrived = resolve; });
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/synthetic/**', async route => {
      arrived();
      if (scenario === 'mesh_closed_while_loading') {
        await gate;
        await route.fulfill({ json: triangle });
      } else await route.fulfill({ status: 503, body: 'Synthetic loader failure' });
    });
    await page.goto(`${base}/e2e/fixtures/scene-lifecycle.html`);
    await page.waitForFunction(() => window.sceneProfile);
    await page.evaluate(kind => window.sceneProfile.show(kind), scenario.startsWith('mesh') ? 'mesh' : 'splat');
    await request;
    if (scenario === 'mesh_closed_while_loading') {
      await page.evaluate(() => window.sceneProfile.hide());
      await page.waitForFunction(() => document.querySelector('canvas') === null);
      release();
      await page.waitForFunction(() => window.sceneCounters.geometriesDisposed > 0, null, { timeout: 1000 }).catch(() => {});
    } else await page.waitForFunction(() => document.querySelector('[data-state="failed"]'), null, { timeout: 30000 });
    await page.waitForTimeout(150);
    const before = await page.evaluate(() => ({ ...window.sceneCounters }));
    await page.waitForTimeout(300);
    const after = await page.evaluate(() => ({ ...window.sceneCounters }));
    await page.evaluate(() => window.sceneProfile.hide());
    await page.waitForTimeout(100);
    const closed = await page.evaluate(() => ({ ...window.sceneCounters }));
    results[scenario] = { observed: after, draws_after_settling: after.renders - before.renders, closed };
    if (process.argv.includes('--verify')) {
      if (closed.created !== closed.disposed || after.created !== after.disposed || after.renders !== before.renders)
        throw new Error(`Scene resources remain active: ${scenario}`);
      if (scenario === 'mesh_closed_while_loading' && closed.geometriesDisposed < 1)
        throw new Error('Late mesh geometry was not released');
    }
    await page.close();
  }
  const sources = ['apps/viewer/src/components/modals/MeshView.tsx', 'apps/viewer/src/components/modals/SplatView.tsx',
    'apps/viewer/e2e/fixtures/scene-lifecycle.html', 'apps/viewer/e2e/fixtures/scene-lifecycle.tsx',
    'eval/profile_scene_lifecycle.mjs', 'package-lock.json'];
  const report = { revision: execFileSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' }).trim(),
    source_hashes: Object.fromEntries(await Promise.all(sources.map(async p => [p, createHash('sha256').update(await readFile(path.join(root, p))).digest('hex')]))),
    environment: { platform: os.platform(), node: process.version, browser: browser.version(), cpu: os.cpus()[0].model },
    workload: { kind: 'synthetic failed/late loaders', viewport: [800, 600], observation_ms: 300 }, results,
    limits: 'One headless Chromium run. Instrumented actual renderer methods; counts are resource/drawing evidence, not FPS, heap/RSS or real-GPU performance. No API, recordings or frames are read or saved.' };
  await mkdir(path.dirname(output), { recursive: true });
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  process.stdout.write(JSON.stringify(results, null, 2) + '\n');
} finally {
  await browser?.close();
  server.kill();
}
