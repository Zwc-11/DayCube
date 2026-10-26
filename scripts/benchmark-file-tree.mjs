// Isolated browser benchmark. Usage: node scripts/benchmark-file-tree.mjs [output.json]
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFile, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { chromium } from '@playwright/test';

const port = 13112;
const server = spawn(process.execPath, [path.resolve('node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd: 'apps/viewer', stdio: 'ignore' });
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const hash = async (file) => createHash('sha256').update(await readFile(file)).digest('hex');
const percentile = (a, p) => a.slice().sort((x, y) => x - y)[Math.floor((a.length - 1) * p)];
let browser;
try {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(`http://127.0.0.1:${port}/e2e/fixtures/file-tree-profile.html`)).ok) break; } catch {}
    if (server.exitCode !== null || i === 99) throw new Error('Vite fixture server failed to start');
    await sleep(100);
  }
  browser = await chromium.launch({ headless: true });
  const results = [];
  for (const size of [25000, 100000]) {
    for (const selection of ['none', 'last']) {
      const page = await browser.newPage({ viewport: { width: 800, height: 700 } });
      await page.goto(`http://127.0.0.1:${port}/e2e/fixtures/file-tree-profile.html?size=${size}`);
      await page.waitForSelector('[role=treeitem]');
      if (selection === 'last') await page.evaluate((n) => window.fileTreeProfile.setCurrent(`Synthetic ${String(n - 1).padStart(6, '0')}.md`), size);
      await page.waitForTimeout(200);
      const out = await page.evaluate(async () => {
        const tree = document.querySelector('[role=tree]');
        const profile = window.fileTreeProfile;
        const snapshots = [];
        profile.times.length = 0;
        for (let i = 0; i < 70; i++) {
          tree.scrollTop = i % 2 ? 20000 : 10000;
          await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          if (i >= 10) snapshots.push(Array.from(tree.querySelectorAll('[role=treeitem]'), (el) => [el.getAttribute('data-row'), el.getAttribute('title'), el.getAttribute('aria-current')]));
        }
        return { times: profile.times.slice(-60), snapshots };
      });
      if (out.times.length !== 60) throw new Error(`Expected 60 render samples, got ${out.times.length}`);
      results.push({ size, selection, samples: out.times.length, p50_ms: percentile(out.times, 0.5), p95_ms: percentile(out.times, 0.95),
        output_sha256: createHash('sha256').update(JSON.stringify(out.snapshots)).digest('hex') });
      await page.close();
    }
  }
  const report = { workload: 'file-tree-scroll-synthetic-v1', measurement: 'React development Profiler actualDuration; 10 warmup and 60 alternating scroll updates; synthetic root notes; initial tree creation excluded',
    runtime: process.version, os: `${os.type()} ${os.release()} ${os.arch()}`, cpu: os.cpus()[0]?.model, browser: browser.version(),
    hashes: { component: await hash('apps/viewer/src/components/knowledge/FileTree.tsx'), fixture: await hash('apps/viewer/e2e/fixtures/file-tree-profile.tsx'), benchmark: await hash('scripts/benchmark-file-tree.mjs') }, results };
  const json = JSON.stringify(report, null, 2) + '\n';
  if (process.argv[2]) await writeFile(process.argv[2], json);
  process.stdout.write(json);
} finally {
  if (browser) await browser.close();
  server.kill();
}
