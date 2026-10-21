import { chromium } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';

const label = process.argv[2] || 'profile';
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 });
  await page.addInitScript(() => {
    window.__viewerProfile = { draws: 0, longTasks: [] };
    new PerformanceObserver(list => {
      for (const entry of list.getEntries()) window.__viewerProfile.longTasks.push(entry.duration);
    }).observe({ entryTypes: ['longtask'] });
    for (const context of [WebGLRenderingContext, WebGL2RenderingContext]) {
      for (const method of ['drawElements', 'drawArrays', 'drawElementsInstanced', 'drawArraysInstanced']) {
        const original = context.prototype[method];
        if (!original) continue;
        context.prototype[method] = function (...args) {
          window.__viewerProfile.draws++;
          return original.apply(this, args);
        };
      }
    }
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  await page.goto('http://127.0.0.1:3000/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(2000);
  const before = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
  await page.evaluate(() => { window.__viewerProfile.draws = 0; window.__viewerProfile.longTasks = []; });
  await page.waitForTimeout(4000);
  const after = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(m => [m.name, m.value]));
  const observed = await page.evaluate(() => ({ ...window.__viewerProfile,
    canvas: Array.from(document.querySelectorAll('canvas')).map(canvas => ({ width: canvas.width, height: canvas.height })),
    renderer: (() => { const gl = document.querySelector('canvas')?.getContext('webgl2');
      if (!gl) return null; const ext = gl.getExtension('WEBGL_debug_renderer_info');
      return ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER); })(),
  }));
  const report = { label, measurement: '4 seconds idle after settling, headless Chromium',
    taskMs: Math.round((after.TaskDuration - before.TaskDuration) * 1000),
    scriptMs: Math.round((after.ScriptDuration - before.ScriptDuration) * 1000),
    layoutMs: Math.round((after.LayoutDuration - before.LayoutDuration) * 1000),
    styleMs: Math.round((after.RecalcStyleDuration - before.RecalcStyleDuration) * 1000),
    heapMB: +(after.JSHeapUsedSize / 1024 / 1024).toFixed(1), ...observed };
  await mkdir('test-results', { recursive: true });
  await writeFile(`test-results/perf-${label}.json`, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
} finally { await browser.close(); }
