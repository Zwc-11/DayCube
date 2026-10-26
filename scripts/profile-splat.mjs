// A scan's 3D view measured on this PC's GPU: frame intervals while following the walk and while
// orbiting, frames drawn at rest, the GPU the browser drew with, and the scene's load time.
//
//   node scripts/profile-splat.mjs [--date 2026-10-24] [--label spark-2m] [--channel msedge|chrome|chromium] [--headless]
//
// It starts its own viewer (Vite on port 3100) and, unless the vault API already answers on 7701,
// its own API (port 7711), so a viewer you have open is left alone. It drives a headed browser
// (Edge by default: it is on every Windows PC and draws with the real GPU; headless Chromium
// draws in software, about 8 frames a second). The result goes to
// vault/logs/splat-perf-<label>.json and is printed. Checks (docs/splat.md): following, p95
// frame interval <= 8.3 ms and p99 <= 16.7 ms; no long task over 50 ms; no frames drawn at rest.
import { chromium } from '@playwright/test';
import { spawn } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = Object.fromEntries(process.argv.slice(2).reduce((out, arg, k, all) => {
  if (arg.startsWith('--')) out.push([arg.slice(2), all[k + 1] && !all[k + 1].startsWith('--') ? all[k + 1] : 'true']);
  return out;
}, []));
const date = args.date || '2026-10-24';
const label = args.label || 'splat';
const channel = args.channel || 'msedge';
const VIEWER = 'http://127.0.0.1:3100';

const children = [];
const start = (command, argv, env) => {
  const child = spawn(command, argv, { cwd: root, env: { ...process.env, ...env }, stdio: 'ignore', shell: process.platform === 'win32', windowsHide: true });
  children.push(child);
  return child;
};
const answers = async (url) => {
  try {
    return (await fetch(url)).ok;
  } catch {
    return false;
  }
};
const waitFor = async (url, seconds) => {
  for (let k = 0; k < seconds * 2; k++) {
    if (await answers(url)) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${url} did not answer in ${seconds} s`);
};
const quantile = (values, q) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return +sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))].toFixed(2);
};
const summary = (intervals) => ({
  frames: intervals.length,
  p50_ms: quantile(intervals, 0.5),
  p95_ms: quantile(intervals, 0.95),
  p99_ms: quantile(intervals, 0.99),
  max_ms: intervals.length ? +Math.max(...intervals).toFixed(2) : null,
  fps: intervals.length ? Math.round(1000 / (intervals.reduce((a, b) => a + b, 0) / intervals.length)) : null,
});

let api = 'http://127.0.0.1:7701';
let browser;
try {
  if (!(await answers(`${api}/health`))) {
    api = 'http://127.0.0.1:7711';
    start('uv', ['run', 'uvicorn', 'ingest.api:app', '--host', '127.0.0.1', '--port', '7711']);
    await waitFor(`${api}/health`, 90);
  }
  start('npm', ['run', 'dev', '--workspace', '@daycube/viewer', '--', '--port', '3100', '--strictPort'], { DAYCUBE_API_TARGET: api });
  await waitFor(VIEWER, 90);

  // Unthrottled frames: the intervals measure what drawing costs, not the display's refresh.
  browser = await chromium.launch({ headless: args.headless === 'true', channel: channel === 'chromium' ? undefined : channel,
    args: ['--disable-frame-rate-limit', '--disable-gpu-vsync'] });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.addInitScript(() => {
    window.__longTasks = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) window.__longTasks.push(entry.duration);
    }).observe({ entryTypes: ['longtask'] });
  });
  await page.goto(`${VIEWER}/?date=${date}`, { waitUntil: 'domcontentloaded' });
  const scan = page.getByRole('button', { name: /^\d\d:\d\d LiDAR scan$/ }).first();
  await scan.waitFor({ timeout: 60_000 });
  const loadStart = Date.now();
  await scan.click();
  const view = page.getByTestId('scan-stage').getByTestId('splat-view');
  await view.and(page.locator('[data-state="ready"]')).waitFor({ timeout: 180_000 });
  const loadSeconds = (Date.now() - loadStart) / 1000;
  const caption = await page.getByTestId('scan-caption').textContent();
  const gpu = await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    const ext = gl?.getExtension('WEBGL_debug_renderer_info');
    return gl ? (ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)) : null;
  });
  await page.waitForTimeout(3000); // the splat tree's first refinements

  // Frame intervals over ``ms`` of requestAnimationFrame, with the camera's per-frame steps.
  const record = (ms) => page.evaluate((ms) => new Promise((resolve) => {
    const times = [];
    const steps = [];
    let last = null;
    window.__longTasks.length = 0;
    const tick = (now) => {
      const p = window.__splat.camera.position;
      if (last) steps.push(Math.hypot(p.x - last.x, p.y - last.y, p.z - last.z));
      last = { x: p.x, y: p.y, z: p.z };
      times.push(now);
      if (now - times[0] < ms) requestAnimationFrame(tick);
      else resolve({ intervals: times.slice(1).map((t, k) => t - times[k]), steps, longTasks: [...window.__longTasks] });
    };
    requestAnimationFrame(tick);
  }), ms);

  // Following: the day played over the scan (the walk at its own speed, 4x).
  await page.getByRole('button', { name: /^\d\d:\d\d LiDAR scan$/ }).first().click();
  await page.getByRole('button', { name: /^Playback speed 1 times/ }).click(); // 1x -> 4x
  await page.getByRole('button', { name: 'Play the day' }).click();
  const following = await record(10_000);
  await page.getByRole('button', { name: 'Pause playback' }).click();
  const moving = following.steps.filter((s) => s > 1e-6);
  const medianStep = quantile(moving, 0.5) ?? 0;

  // Orbiting: dragging in circles.
  const box = await view.boundingBox();
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  const orbitDone = record(5000);
  for (let k = 0; k < 250; k++) {
    await page.mouse.move(cx + 200 * Math.cos(k / 20), cy + 80 * Math.sin(k / 20));
    await page.waitForTimeout(16);
  }
  const orbit = await orbitDone;
  await page.mouse.up();

  // At rest: frames drawn in the second after a second of stillness.
  await page.waitForTimeout(1500);
  const before = await page.evaluate(() => window.__splat.frames);
  await page.waitForTimeout(1000);
  const restFrames = (await page.evaluate(() => window.__splat.frames)) - before;

  const followed = summary(following.intervals);
  const orbited = summary(orbit.intervals);
  const report = {
    label, date, when: new Date().toISOString(), browser: `${channel} ${browser.version()}`, gpu, caption,
    load_s: +loadSeconds.toFixed(1),
    following: { ...followed, frames_moving: following.steps.length ? +(moving.length / following.steps.length).toFixed(3) : null,
      largest_step_vs_median: medianStep ? +(Math.max(...moving) / medianStep).toFixed(2) : null,
      long_tasks_ms: following.longTasks.map((d) => Math.round(d)) },
    orbiting: { ...orbited, long_tasks_ms: orbit.longTasks.map((d) => Math.round(d)) },
    rest_frames_per_s: restFrames,
    checks: {
      nvidia: /nvidia/i.test(gpu ?? ''),
      following_p95_le_8_3: followed.p95_ms !== null && followed.p95_ms <= 8.3,
      following_p99_le_16_7: followed.p99_ms !== null && followed.p99_ms <= 16.7,
      no_long_task_over_50: ![...following.longTasks, ...orbit.longTasks].some((d) => d > 50),
      rests: restFrames === 0,
    },
  };
  await mkdir(path.join(root, 'vault', 'logs'), { recursive: true });
  const out = path.join(root, 'vault', 'logs', `splat-perf-${label}.json`);
  await writeFile(out, JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  console.log(`\nWritten to ${out}`);
} finally {
  await browser?.close();
  for (const child of children) {
    if (process.platform === 'win32') spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    else child.kill();
  }
}
