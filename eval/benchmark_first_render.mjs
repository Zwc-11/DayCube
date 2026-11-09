// Time from navigation until the viewer has rendered its sidebar ("Daily"), for two running
// servers (two builds of the viewer), interleaved, each run in a fresh browser context (cold cache)
// with the CPU throttled 4x. Start each build with the desktop server, for example:
//   DAYCUBE_VIEWER_DIST=<dist> uv run uvicorn ingest.desktop:create_desktop_app --factory --port 7821
//   node eval/benchmark_first_render.mjs http://127.0.0.1:7821 http://127.0.0.1:7822 15
import { chromium } from '@playwright/test';

const [before, after, runs = '15'] = process.argv.slice(2);
if (!before || !after) throw new Error('usage: benchmark_first_render.mjs <url-before> <url-after> [runs]');
const browser = await chromium.launch();
const samples = { before: [], after: [] };
for (let run = 0; run < Number(runs); run++) {
  for (const [name, url] of [['before', before], ['after', after]]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await (await context.newCDPSession(page)).send('Emulation.setCPUThrottlingRate', { rate: 4 });
    await page.goto(`${url}/`, { waitUntil: 'commit' });
    await page.waitForFunction(() => document.body?.innerText.includes('Daily'), null, { polling: 'raf', timeout: 60000 });
    samples[name].push(await page.evaluate(() => performance.now()));
    await context.close();
  }
}
await browser.close();
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
for (const [name, values] of Object.entries(samples)) {
  console.log(`${name}: median ${median(values).toFixed(0)} ms, min ${Math.min(...values).toFixed(0)}, max ${Math.max(...values).toFixed(0)} (n=${values.length})`);
}
