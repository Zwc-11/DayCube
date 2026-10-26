// A scan's 3D view measured on this PC's GPU: what each drawn frame cost the GPU, frames drawn while
// following the walk and while orbiting, long tasks and where the main thread spent its time, frames
// drawn at rest, the GPU the browser drew with, the splat budget the view settled on, and the
// scene's load time.
//
//   node scripts/profile-splat.mjs [--date 2026-10-24] [--label splat] [--gpu high|default]
//                                  [--channel msedge|chrome|chromium] [--headless] [--size 1600x1000]
//                                  [--unthrottled]
//
// It starts its own viewer (Vite on port 3100) and, unless the vault API already answers on 7701,
// its own API (port 7711), so a viewer you have open is left alone. It drives a headed browser
// (Edge by default: it is on every Windows PC and draws with the real GPU; headless Chromium
// draws in software, about 8 frames a second). ``--gpu high`` (the default) starts it with
// --force_high_performance_gpu, Chromium's switch for the discrete GPU on a laptop with two;
// ``--gpu default`` leaves the choice to Windows, as a browser opened by hand gets it.
//
// Frames come at the display's refresh, as in a browser opened by hand: the refresh is measured
// first, and a frame that took longer shows as a longer interval. ``--unthrottled``
// (--disable-frame-rate-limit --disable-gpu-vsync) lets rAF run ahead of the GPU instead: on
// 2026-10-26 that queued thousands of frames a second and the page then waited 99.6 s for the GPU,
// so it measures the queue, not drawing.
//
// The result goes to vault/logs/splat-perf-<label>.json and is printed; the main thread's CPU
// profile to vault/logs/splat-cpu-<label>.cpuprofile (DevTools › Performance › Load profile).
// Checks (docs/splat.md): the NVIDIA GPU drew (with --gpu high); following, each drawn frame's GPU
// time p95 <= 8.3 ms (the interval between frames drawn back to back where the browser has no GPU
// timer); following, rAF intervals p95 <= 1.5 refreshes and p99 <= 3; no long task over 50 ms; no
// frames drawn at rest.
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
const gpuChoice = args.gpu || 'high';
const [width, height] = (args.size || '1600x1000').split('x').map(Number);
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
  return sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))];
};
const fixed = (value, digits = 2) => (value === null || value === undefined ? null : +value.toFixed(digits));
const summary = (intervals) => ({
  frames: intervals.length,
  p50_ms: fixed(quantile(intervals, 0.5)),
  p95_ms: fixed(quantile(intervals, 0.95)),
  p99_ms: fixed(quantile(intervals, 0.99)),
  max_ms: intervals.length ? fixed(Math.max(...intervals)) : null,
});

/** Self time per function in a CPU profile, the largest first. */
function hotFunctions(profile, top = 15) {
  const byId = new Map(profile.nodes.map((node) => [node.id, node]));
  const self = new Map();
  for (let k = 0; k < profile.samples.length; k++) {
    const node = byId.get(profile.samples[k]);
    const { functionName, url, lineNumber } = node.callFrame;
    if (functionName === '(idle)') continue;
    const name = `${functionName || '(anonymous)'}${url ? ` ${url.split('/').pop().split('?')[0]}:${lineNumber + 1}` : ''}`;
    self.set(name, (self.get(name) ?? 0) + (profile.timeDeltas[k + 1] ?? 0) / 1000);
  }
  return [...self.entries()].sort((a, b) => b[1] - a[1]).slice(0, top).map(([name, ms]) => ({ name, ms: Math.round(ms) }));
}

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

  const unthrottled = args.unthrottled === 'true';
  browser = await chromium.launch({ headless: args.headless === 'true', channel: channel === 'chromium' ? undefined : channel,
    args: [...(unthrottled ? ['--disable-frame-rate-limit', '--disable-gpu-vsync'] : []), ...(gpuChoice === 'high' ? ['--force_high_performance_gpu'] : [])] });
  const page = await browser.newPage({ viewport: { width, height } });
  page.setDefaultTimeout(180_000); // a GPU far behind blocks the page for seconds at a time
  await page.addInitScript(() => {
    window.__longTasks = [];
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries()) window.__longTasks.push(entry.duration);
    }).observe({ entryTypes: ['longtask'] });
  });
  await page.goto(`${VIEWER}/?date=${date}`, { waitUntil: 'domcontentloaded' });
  const scan = page.getByRole('button', { name: /^\d\d:\d\d LiDAR scan$/ }).first();
  await scan.waitFor({ timeout: 60_000 });
  // The display's refresh: rAF intervals for a second before the scan opens.
  const idle = await page.evaluate(() => new Promise((resolve) => {
    const times = [];
    const tick = (now) => {
      times.push(now);
      if (now - times[0] < 1000) requestAnimationFrame(tick);
      else resolve(times.slice(1).map((t, k) => t - times[k]));
    };
    requestAnimationFrame(tick);
  }));
  const refreshMs = quantile(idle, 0.5);
  const loadStart = Date.now();
  await scan.click();
  const view = page.getByTestId('scan-stage').getByTestId('splat-view');
  await view.and(page.locator('[data-state="ready"]')).waitFor({ timeout: 180_000 });
  const loadSeconds = (Date.now() - loadStart) / 1000;
  const box = await view.boundingBox();
  const caption = await page.getByTestId('scan-caption').textContent();
  const drawn = await page.evaluate(() => ({ gpu: window.__splat.gpu, budget: window.__splat.budget, timed: window.__splat.timed }));
  await page.waitForTimeout(3000); // the splat tree's first refinements

  // ``ms`` of requestAnimationFrame: intervals, the camera's per-frame steps, the frames the view
  // drew and each one's GPU time (or interval), and the long tasks.
  const record = (ms) => page.evaluate((ms) => new Promise((resolve) => {
    const times = [];
    const steps = [];
    let last = null;
    window.__longTasks.length = 0;
    const frames0 = window.__splat.frames;
    const measured0 = window.__splat.frameMs.length;
    const tick = (now) => {
      const p = window.__splat.camera.position;
      if (last) steps.push(Math.hypot(p.x - last.x, p.y - last.y, p.z - last.z));
      last = { x: p.x, y: p.y, z: p.z };
      times.push(now);
      if (now - times[0] < ms) requestAnimationFrame(tick);
      else resolve({
        seconds: (now - times[0]) / 1000,
        intervals: times.slice(1).map((t, k) => t - times[k]),
        steps,
        drawn: window.__splat.frames - frames0,
        frameMs: window.__splat.frameMs.slice(measured0),
        longTasks: [...window.__longTasks],
        budget: window.__splat.budget,
      });
    };
    requestAnimationFrame(tick);
  }), ms);
  const phase = (r) => {
    const moving = r.steps.filter((s) => s > 1e-6);
    const medianStep = quantile(moving, 0.5);
    return {
      seconds: fixed(r.seconds, 1),
      drawn_fps: Math.round(r.drawn / r.seconds),
      frame_ms: summary(r.frameMs),
      raf: summary(r.intervals),
      // Frames that missed the refresh after theirs (an interval over 1.5 refreshes).
      late_share: r.intervals.length ? fixed(r.intervals.filter((d) => d > 1.5 * refreshMs).length / r.intervals.length, 3) : null,
      frames_moving: r.steps.length ? fixed(moving.length / r.steps.length, 3) : null,
      largest_step_vs_median: medianStep ? fixed(Math.max(...moving) / medianStep) : null,
      long_tasks_ms: r.longTasks.map((d) => Math.round(d)),
      budget: r.budget,
    };
  };

  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Profiler.enable');
  await cdp.send('Profiler.setSamplingInterval', { interval: 500 });
  await cdp.send('Profiler.start');

  // Following: the day played over the scan (the walk at its own speed, 4x). The buttons get click
  // events, not mouse clicks: those wait for a button to hold still over two frames, and on a slow
  // GPU frames can be seconds apart.
  await page.getByRole('button', { name: /^\d\d:\d\d LiDAR scan$/ }).first().dispatchEvent('click');
  await page.getByRole('button', { name: /^Playback speed 1 times/ }).dispatchEvent('click'); // 1x -> 4x
  await page.getByRole('button', { name: 'Play the day' }).dispatchEvent('click');
  const following = phase(await record(10_000));
  await page.getByRole('button', { name: 'Pause playback' }).dispatchEvent('click');

  // Orbiting: dragging in circles.
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  await page.mouse.move(cx, cy);
  await page.mouse.down();
  const orbitDone = record(5000);
  const orbitUntil = Date.now() + 5000; // each move waits for the page: a stalled one gets fewer
  for (let k = 0; k < 250 && Date.now() < orbitUntil; k++) {
    await page.mouse.move(cx + 200 * Math.cos(k / 20), cy + 80 * Math.sin(k / 20));
    await page.waitForTimeout(16);
  }
  const orbit = phase(await orbitDone);
  await page.mouse.up();

  const { profile } = await cdp.send('Profiler.stop');

  // At rest: frames drawn in the second after a second and a half of stillness.
  await page.waitForTimeout(1500);
  const before = await page.evaluate(() => window.__splat.frames);
  await page.waitForTimeout(1000);
  const restFrames = (await page.evaluate(() => window.__splat.frames)) - before;

  const gpuName = drawn.gpu?.name ?? null;
  // Following's frame cost: GPU time where there is a timer; else the interval between frames
  // drawn back to back (the view records those).
  const followCost = following.frame_ms.p95_ms;
  const report = {
    label, date, when: new Date().toISOString(), size: `${width}x${height}`, browser: `${channel} ${browser.version()}`, gpu_choice: gpuChoice,
    frames: unthrottled ? 'unthrottled' : 'at the display refresh', refresh_ms: fixed(refreshMs), refresh_hz: refreshMs ? Math.round(1000 / refreshMs) : null,
    gpu: gpuName, gpu_kind: drawn.gpu?.kind ?? null, frame_ms_from: drawn.timed ? 'GPU timer query' : 'interval between drawn frames',
    caption, load_s: fixed(loadSeconds, 1), budget_when_ready: drawn.budget,
    following, orbiting: orbit,
    rest_frames_per_s: restFrames,
    main_thread_self_ms: hotFunctions(profile),
    checks: {
      ...(gpuChoice === 'high' ? { nvidia: /nvidia/i.test(gpuName ?? '') } : {}),
      following_frame_p95_le_8_3: followCost !== null && followCost <= 8.3,
      ...(unthrottled ? {} : {
        following_on_time: following.raf.p95_ms !== null && following.raf.p95_ms <= 1.5 * refreshMs && following.raf.p99_ms <= 3 * refreshMs,
      }),
      no_long_task_over_50: ![...following.long_tasks_ms, ...orbit.long_tasks_ms].some((d) => d > 50),
      rests: restFrames === 0,
    },
  };
  await mkdir(path.join(root, 'vault', 'logs'), { recursive: true });
  const out = path.join(root, 'vault', 'logs', `splat-perf-${label}.json`);
  await writeFile(out, JSON.stringify(report, null, 2));
  await writeFile(path.join(root, 'vault', 'logs', `splat-cpu-${label}.cpuprofile`), JSON.stringify(profile));
  console.log(JSON.stringify(report, null, 2));
  console.log(`\nWritten to ${out}`);
} finally {
  await browser?.close();
  for (const child of children) {
    if (process.platform === 'win32') spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
    else child.kill();
  }
}
