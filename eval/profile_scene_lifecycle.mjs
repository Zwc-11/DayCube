/** Synthetic loader failure/early-close resource counters; no API, images or user data. */
import { chromium } from '@playwright/test';
import { spawn, execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import os from 'node:os';
import { captureSceneLifecycle } from '../apps/viewer/e2e/fixtures/scene-lifecycle.mjs';

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
  for (const scenario of ['mesh_failure', 'mesh_closed_while_loading', 'splat_failure']) {
    const page = await browser.newPage({ viewport: { width: 800, height: 600 } });
    const result = await captureSceneLifecycle(page, scenario, base);
    results[scenario] = result;
    if (process.argv.includes('--verify')) {
      if (result.closed.created !== result.closed.disposed || result.observed.created !== result.observed.disposed || result.draws_after_settling !== 0)
        throw new Error(`Scene resources remain active: ${scenario}`);
      if (scenario === 'mesh_closed_while_loading' && result.closed.geometriesDisposed < 1)
        throw new Error('Late mesh geometry was not released');
    }
    await page.close();
  }
  const sources = ['apps/viewer/src/components/modals/MeshView.tsx', 'apps/viewer/src/components/modals/SplatView.tsx',
    'apps/viewer/e2e/fixtures/scene-lifecycle.html', 'apps/viewer/e2e/fixtures/scene-lifecycle.tsx',
    'apps/viewer/e2e/fixtures/scene-lifecycle.mjs', 'apps/viewer/src/lib/sceneResources.ts',
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
