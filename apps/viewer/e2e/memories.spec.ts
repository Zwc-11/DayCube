import { test, expect } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';

// A memory written into the test vault as ingest/recon/memory.py writes one: a photo and its
// Gaussians in the viewer's axes (here a coloured wall 3 m in front and a few nearer blobs).
const DATE = '2026-10-21';
const ID = 'mem_0123456789ab';
const folder = path.resolve(`.daycube-e2e-vault/${DATE}/memories/${ID}`);

function splats(): Buffer {
  const records: number[][] = [];
  const add = (x: number, y: number, z: number, s: number, r: number, g: number, b: number) => records.push([x, y, z, s, r, g, b]);
  for (let i = 0; i < 80; i += 1) {
    for (let j = 0; j < 60; j += 1) {
      const x = (i / 79 - 0.5) * 4.2;
      const y = (0.5 - j / 59) * 3.1;
      add(x, y, -3, 0.035, 40 + 2.6 * i, 60 + 3 * j, 160 - i);
    }
  }
  for (let k = 0; k < 400; k += 1) add(Math.sin(k) * 0.6, Math.cos(k * 1.3) * 0.4, -1.6 - (k % 7) * 0.05, 0.03, 230, 180, 60);
  const out = Buffer.alloc(records.length * 32);
  records.forEach(([x, y, z, s, r, g, b], n) => {
    const o = n * 32;
    [x, y, z, s, s, s].forEach((v, k) => out.writeFloatLE(v, o + 4 * k));
    [r, g, b, 240].forEach((v, k) => out.writeUInt8(Math.round(v), o + 24 + k));
    [255, 128, 128, 128].forEach((v, k) => out.writeUInt8(v, o + 28 + k)); // w = 1
  });
  return out;
}

test.beforeAll(() => {
  rmSync(folder, { recursive: true, force: true });
  mkdirSync(folder, { recursive: true });
  writeFileSync(path.join(folder, 'memory.splat'), splats());
  const made = spawnSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=640x480', '-frames:v', '1', path.join(folder, 'photo.jpg')]);
  expect(made.status).toBe(0);
  writeFileSync(path.join(folder, 'memory.json'), JSON.stringify({
    photo: 'photo.jpg', splat: 'memory.splat', width: 640, height: 480, fx: 500, fov_y_deg: 51.3, splats: 5200,
    depth_m: { median: 3, p10: 1.7, p90: 3 }, model: 'sharp', made: '2026-10-26T23:00:00-04:00',
  }));
});

test.afterAll(() => rmSync(folder, { recursive: true, force: true }));

test('a memory opens as its photo, fades into 3D and follows the mouse', async ({ page }) => {
  await page.goto('/?view=memories');
  const item = page.getByTestId('memory-item');
  await expect(item).toHaveCount(1);
  await item.click();
  await expect(page).toHaveURL(new RegExp(`view=memories&memory=${DATE}/${ID}`));
  const view = page.getByTestId('memory-view');
  await expect(view).toHaveAttribute('data-state', 'ready', { timeout: 60_000 });
  await expect.poll(() => page.evaluate(() => (window as any).__memory?.frames ?? 0), { timeout: 30_000 }).toBeGreaterThan(3);
  expect(await page.evaluate(() => (window as any).__memory.splats)).toBe(5200);
  // Something is drawn: the canvas is not one colour.
  const colours = await page.evaluate(() => {
    const canvas = document.querySelector('.memory__canvas') as HTMLCanvasElement;
    const copy = document.createElement('canvas');
    copy.width = 64;
    copy.height = 40;
    const ctx = copy.getContext('2d')!;
    ctx.drawImage(canvas, 0, 0, 64, 40);
    const data = ctx.getImageData(0, 0, 64, 40).data;
    const seen = new Set<string>();
    for (let k = 0; k < data.length; k += 4) seen.add(`${data[k] >> 4},${data[k + 1] >> 4},${data[k + 2] >> 4}`);
    return seen.size;
  });
  expect(colours).toBeGreaterThan(20);
  // The mouse to the right: the view moves right, within its bound (12% of 3 m).
  const box = (await view.boundingBox())!;
  await page.mouse.move(box.x + box.width * 0.95, box.y + box.height / 2);
  await expect.poll(() => page.evaluate(() => (window as any).__memory.pose.x), { timeout: 20_000 }).toBeGreaterThan(0.15);
  expect(await page.evaluate(() => (window as any).__memory.pose.x)).toBeLessThanOrEqual(0.36 + 1e-6);
  // Stepped in, never past its bound (45% of 3 m).
  const z = await page.evaluate(() => (window as any).__memory.pose.z);
  expect(z).toBeLessThan(0);
  expect(z).toBeGreaterThanOrEqual(-1.35 - 1e-6);
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/view=memories$/);
  await expect(view).toHaveCount(0);
});
