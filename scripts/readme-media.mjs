// The README's screenshots and demo video (docs/media), made from the fictional sample data in
// fixtures/ in a throwaway vault, never from a real one: `npm run build && npm run media`.
// Needs uv, FFmpeg and Playwright's Chromium. The eye-tracking clicks and the training curves are
// sample data written here; the README says so.
import { chromium } from '@playwright/test';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const media = path.join(root, 'docs/media');
const dist = path.join(root, 'apps/viewer/dist');
if (!existsSync(path.join(dist, 'index.html'))) throw new Error('build the viewer first: npm run build');
const vault = mkdtempSync(path.join(tmpdir(), 'daycube-media-'));
const env = { ...process.env, DAYCUBE_VAULT_ROOT: vault, DAYCUBE_VIEWER_DIST: dist };
const port = 7851;
const base = `http://127.0.0.1:${port}`;

function run(command, args) {
  const result = spawnSync(command, args, { cwd: root, env, stdio: 'inherit' });
  if (result.status !== 0) throw new Error(`${command} ${args.join(' ')} failed`);
}

// The sample vault the browser tests use (scripts/start-api-test.mjs).
const phone = ['12 Example Ave-2026-10-21_18-00-00.zip', 'Example Ave 2.m4a', 'Example Ave 2.json', 'scan'].map((n) => `fixtures/phone-day/${n}`);
for (const args of [
  ['init'], ['seed', '--capture'], ['index', '--date', '2026-10-20'],
  ['import-phone', ...phone, '--no-transcribe'], ['index', '--date', '2026-10-21'],
]) run('uv', ['run', 'daycube', ...args]);

// Sample eye-tracker learning: a calibration, 300 clicks before and after one update.
const calibration = path.join(vault, 'calibration');
mkdirSync(calibration, { recursive: true });
const calibrated = '2026-10-24T15:00:00+00:00';
const t0 = Date.parse(calibrated) / 1000;
writeFileSync(path.join(calibration, 'gaze.json'), JSON.stringify({ t: calibrated, model: 'head', monitor: [2560, 1440], error_px: 85, mm_per_px: 0.233 }));
const X = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 600, ...new Array(14).fill(0)];
const clicks = Array.from({ length: 300 }, (_, k) => {
  const v = k < 150 ? 0 : 1;
  const base_px = 120 + 40 * Math.abs(Math.sin(k * 1.7)) + (k % 9) * 3;
  return { t: t0 + 20 * k, x: 1000, y: 600, X, error_px: v ? base_px * 0.68 : base_px, base_px, v, used: k % 10 !== 0 };
});
writeFileSync(path.join(calibration, 'clicks.jsonl'), clicks.map((c) => JSON.stringify(c)).join('\n') + '\n');
const fitAt = new Date((t0 + 20 * 149) * 1000).toISOString();
const fit = { event: 'fit', t: fitAt, version: 1, accepted: true, clicks: 140, held_out: 28, penalty: 10, head_terms: false,
  error_px: { calibration: 142, in_use: 142, learned: 97, fixed_guess: 600 } };
writeFileSync(path.join(calibration, 'learning.jsonl'),
  [fit, { event: 'applied', t: fitAt, version: 1, after_ms: 31.2 }].map((e) => JSON.stringify(e)).join('\n') + '\n');
writeFileSync(path.join(calibration, 'gaze-learned.json'), JSON.stringify({ calibration: calibrated, version: 1, t: fitAt, fit, pose_ref: null }));

// Sample training runs, written as a trainer writes them (ingest/training.py).
run('uv', ['run', 'python', '-c', `
import math
from pathlib import Path
from ingest.training import Run
vault = Path(${JSON.stringify(vault)})
with Run(vault, "splat", "3D of the scan at 12 Example Ave", steps_total=30000, device="GPU (Brush 0.3.0)",
         metrics={"psnr_db": ("dB", "higher"), "splats": ("splats", None)}) as run:
    for step in range(0, 30001, 250):
        run.log(step, splats=40000 + 900000 * (1 - math.exp(-step / 9000)))
        if step and step % 3000 == 0:
            run.log(step, psnr_db=11 + 4.4 * math.log1p(step / 3000))
    run.result.update(psnr_db=21.6, minutes=36)
run = Run(vault, "revisit-predictor", "Revisit predictor, sites 1-4", steps_total=20000, device="RTX 5070",
          metrics={"loss": ("L1", "lower"), "val_loss": ("L1", "lower")})
for step in range(0, 12001, 100):
    run.log(step, loss=0.9 * math.exp(-step / 4000) + 0.12 + 0.02 * math.sin(step / 70) ** 2)
    if step % 1000 == 0:
        run.log(step, val_loss=0.95 * math.exp(-step / 5000) + 0.16)
`]);

const server = spawn('uv', ['run', 'uvicorn', 'ingest.desktop:create_desktop_app', '--factory', '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning'],
  { cwd: root, env, stdio: 'inherit' });
try {
  for (let i = 0; ; i++) {
    if ((await fetch(`${base}/api/health`).catch(() => null))?.ok) break;
    if (i > 120) throw new Error('the server did not start');
    await new Promise((r) => setTimeout(r, 500));
  }
  const token = readFileSync(path.join(vault, '.token'), 'utf8').trim();
  const browser = await chromium.launch({ args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
  // The token as the Windows app hands it to its window, so Summarize and Ask work.
  const desktop = `window.__DAYCUBE_DESKTOP__ = Object.freeze({ token: ${JSON.stringify(token)} });`;
  const viewport = { width: 1440, height: 900 };

  const summarize = async (page) => {
    const card = page.getByTestId('day-review').first();
    await card.getByRole('button', { name: 'Summarize now' }).click();
    await card.locator('.dr-headline').waitFor();
  };
  const ask = async (page, question, delay = 0) => {
    await page.keyboard.press('Control+K');
    await page.getByLabel('Question').pressSequentially(question, { delay });
    await page.keyboard.press('Enter');
    await page.locator('.ask__group-title', { hasText: 'Sources' }).waitFor({ timeout: 20000 });
  };
  const scene = async (page) => {
    await page.waitForSelector('canvas', { timeout: 60000 });
    await page.waitForTimeout(4000); // models and textures settle
  };

  // Screenshots, at twice the pixels for sharp text on high-density screens.
  rmSync(media, { recursive: true, force: true });
  mkdirSync(media, { recursive: true });
  const shots = await browser.newContext({ viewport, deviceScaleFactor: 2 });
  await shots.addInitScript(desktop);
  const page = await shots.newPage();
  const shot = async (name) => {
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(media, `${name}.jpg`), type: 'jpeg', quality: 88 });
  };
  await page.goto(`${base}/?date=2026-10-20`);
  await scene(page);
  await summarize(page);
  await shot('day-3d');
  await ask(page, 'Where was I when we decided the demo order?');
  await shot('ask');
  await page.goto(`${base}/?date=2026-10-21`);
  await scene(page);
  await summarize(page);
  await shot('phone-day');
  await page.goto(`${base}/?date=2026-10-22&view=notes`);
  await summarize(page);
  await shot('notes');
  await page.goto(`${base}/`);
  await page.getByRole('button', { name: 'Your data' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Eye tracking', exact: true }).click();
  await page.getByTestId('error-chart').waitFor();
  await shot('eye-tracking');
  await page.goto(`${base}/?view=training`);
  await page.locator('.training__chart').first().waitFor();
  await shot('training');
  await shots.close();

  // The demo: a day in 3D played from 10:00 (into Engineering Hall and up the stairs), a question
  // answered with its sources, the day's notes and summary, the Training page.
  const recording = path.join(vault, 'video');
  const recorded = Date.now(); // the video starts with the context; it is cut where the tour starts
  const tour = await browser.newContext({ viewport, recordVideo: { dir: recording, size: viewport } });
  await tour.addInitScript(desktop);
  const t = await tour.newPage();
  await t.goto(`${base}/?date=2026-10-20`);
  await scene(t);
  await t.getByText('Engineering Hall', { exact: true }).first().click();
  await t.waitForTimeout(1500);
  const cut = (Date.now() - recorded) / 1000 - 0.5;
  await t.getByRole('button', { name: 'Play the day' }).click();
  await t.waitForTimeout(9000);
  await t.getByRole('button', { name: 'Pause playback' }).click();
  await ask(t, 'Where was I when we decided the demo order?', 45);
  await t.waitForTimeout(3500);
  await t.goto(`${base}/?date=2026-10-22&view=notes`);
  await t.getByTestId('day-review').locator('.dr-headline').waitFor();
  await t.waitForTimeout(2500);
  await t.getByTestId('note').hover();
  for (let i = 0; i < 10; i++) await t.mouse.wheel(0, 60).then(() => t.waitForTimeout(60));
  await t.waitForTimeout(1500);
  await t.goto(`${base}/?view=training`);
  await t.locator('.training__chart').first().waitFor();
  await t.waitForTimeout(3000);
  await tour.close();
  await browser.close();

  const webm = path.join(recording, readdirSync(recording).find((f) => f.endsWith('.webm')));
  const ffmpeg = (...args) => run('ffmpeg', ['-v', 'error', '-y', '-ss', cut.toFixed(2), '-i', webm, ...args]);
  ffmpeg('-c:v', 'libx264', '-preset', 'veryfast', '-crf', '24', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', path.join(media, 'daycube-demo.mp4'));
  ffmpeg('-vf', 'fps=8,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle',
    path.join(media, 'daycube-demo.gif'));
} finally {
  server.kill('SIGTERM');
  rmSync(vault, { recursive: true, force: true });
}
