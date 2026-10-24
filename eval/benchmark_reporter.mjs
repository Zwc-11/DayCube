// Synthetic DOM only. No extension, receiver, camera or user vault is opened.
import { chromium } from '@playwright/test';
import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

const outputAt = process.argv.indexOf('--output');
if (outputAt < 0 || !process.argv[outputAt + 1]) throw new Error('--output is required');
const output = path.resolve(process.argv[outputAt + 1]);
const compareAt = process.argv.indexOf('--compare');
const source = await readFile('extensions/chrome/content.js', 'utf8');
const hook = `window.__reporterReview = {snapshot, sizes: () => ({elements: elements.size, sent: sent.size, sentLines: sentLines.size, measured: measured.size})};`;
if (!source.includes('  schedule(600);')) throw new Error('Reporter inspection hook moved');
const inspected = source.replace('  schedule(600);', hook);
const samples = [];
const browser = await chromium.launch({headless: true});
try {
  for (let repeat = 0; repeat < 3; repeat++) {
    const context = await browser.newContext({viewport: {width: 1200, height: 800}});
    try {
      const page = await context.newPage();
      await page.setContent('<!doctype html><style>p{margin:0;height:2px;font:1px monospace}</style><p>Initial synthetic paragraph.</p>');
      await page.evaluate(() => {
        window.__initialParagraph = document.querySelector('p');
        window.__latestSnapshot = null;
        Object.defineProperty(window, 'chrome', {value: {runtime: {
          id: 'synthetic-review',
          sendMessage: async message => {
            if (message.kind === 'blocks') window.__latestSnapshot = structuredClone(message);
            return {ok: true, epoch: 0};
          },
          onMessage: {addListener: () => {}},
        }}});
      });
      await page.addScriptTag({content: inspected});
      const result = await page.evaluate(async () => {
        const report = window.__reporterReview;
        report.snapshot();
        await Promise.resolve();
        const first = window.__latestSnapshot.blocks[0];
        const started = performance.now();
        for (let batch = 0; batch < 80; batch++) {
          const nodes = Array.from({length: 150}, (_, i) => {
            const p = document.createElement('p');
            p.textContent = `Synthetic paragraph ${batch * 150 + i}, for cache lifetime checks.`;
            return p;
          });
          document.body.replaceChildren(...nodes);
          report.snapshot();
          await Promise.resolve();
        }
        document.body.replaceChildren(window.__initialParagraph);
        report.snapshot();
        await Promise.resolve();
        const last = window.__latestSnapshot.blocks[0];
        if (last.id !== first.id || JSON.stringify(last.rect) !== JSON.stringify(first.rect)) throw new Error('Evidence identity or geometry changed');
        return {
          duration_ms: performance.now() - started,
          caches: report.sizes(),
          revisited_text_resent: last.text !== undefined,
          latest: {id: last.id, rect: last.rect, text: last.text ?? first.text},
        };
      });
      const cdp = await context.newCDPSession(page);
      await cdp.send('Performance.enable');
      await cdp.send('HeapProfiler.collectGarbage');
      const metrics = await cdp.send('Performance.getMetrics');
      result.heap_used_bytes = metrics.metrics.find(m => m.name === 'JSHeapUsedSize').value;
      result.latest_sha256 = createHash('sha256').update(JSON.stringify(result.latest)).digest('hex');
      delete result.latest;
      samples.push(result);
    } finally { await context.close(); }
  }
  const report = {
    environment: {platform: os.platform(), arch: os.arch(), cpu: os.cpus()[0].model, node: process.version, chromium: browser.version()},
    source_sha256: createHash('sha256').update(source).digest('hex'),
    workload: {batches: 80, blocks_per_batch: 150, repeats: 3, viewport: [1200, 800]},
    samples,
    limits: 'Headless Chromium, synthetic DOM only. Private cache-size hook is inspection code, not production telemetry. Timed traversal includes layout and cache work; heap is measured after GC separately. Cache eviction may resend text; identity, geometry and reconstructed text must agree.',
  };
  if (compareAt >= 0) {
    const before = JSON.parse(await readFile(process.argv[compareAt + 1], 'utf8'));
    if (JSON.stringify(before.workload) !== JSON.stringify(report.workload) || samples.some((s, i) => s.latest_sha256 !== before.samples[i].latest_sha256)) throw new Error('Workload or latest evidence changed');
  }
  await mkdir(path.dirname(output), {recursive: true});
  await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({samples}, null, 2));
} finally { await browser.close(); }
