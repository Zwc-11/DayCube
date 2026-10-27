import { test, expect } from '@playwright/test';
import { spawnSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';

// A memory written into the test vault as ingest/recon/memory.py writes one: a photo and its
// Gaussians in the viewer's axes (here a coloured wall 3 m in front and a few nearer blobs).
const DATE = '2026-10-21';
const ID = 'mem_0123456789ab';
const folder = path.resolve(`.daycube-e2e-vault/${DATE}/memories/${ID}`);
// Two photos from a scan, standing in its world: a room of splats, as a scan's splat would be.
const WORLD_MOMENT = 'mo_scan_e2e_world';
const worldMoment = path.resolve(`.daycube-e2e-vault/${DATE}/moments/${WORLD_MOMENT}`);
const IN_WORLD = ['mem_aaaaaaaaaaaa', 'mem_bbbbbbbbbbbb'];

function records(list: number[][]): Buffer {
  const out = Buffer.alloc(list.length * 32);
  list.forEach(([x, y, z, s, r, g, b], n) => {
    const o = n * 32;
    [x, y, z, s, s, s].forEach((v, k) => out.writeFloatLE(v, o + 4 * k));
    [r, g, b, 240].forEach((v, k) => out.writeUInt8(Math.round(v), o + 24 + k));
    [255, 128, 128, 128].forEach((v, k) => out.writeUInt8(v, o + 28 + k));
  });
  return out;
}

/** A 8 m room: floor and four walls, each its own colour ramp. */
function room(): Buffer {
  const list: number[][] = [];
  for (let i = 0; i <= 80; i += 1) {
    for (let j = 0; j <= 80; j += 1) {
      const a = i / 80 * 8 - 4;
      const b = j / 80 * 4;
      list.push([a, 0, j / 80 * 8 - 4, 0.06, 90 + i, 120, 80 + j]); // floor
      list.push([a, b, -4, 0.06, 200, 60 + i * 2, 60]); // far wall
      list.push([a, b, 4, 0.06, 60, 60 + i * 2, 200]); // wall behind
      list.push([-4, b, a, 0.06, 60 + i * 2, 200, 120]); // left
      list.push([4, b, a, 0.06, 220, 120, 60 + i * 2]); // right
    }
  }
  return records(list);
}

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

function writeMemory(id: string, world: object | null): void {
  const at = path.resolve(`.daycube-e2e-vault/${DATE}/memories/${id}`);
  rmSync(at, { recursive: true, force: true });
  mkdirSync(at, { recursive: true });
  writeFileSync(path.join(at, 'memory.splat'), splats());
  const made = spawnSync('ffmpeg', ['-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc=size=640x480', '-frames:v', '1', path.join(at, 'photo.jpg')]);
  expect(made.status).toBe(0);
  writeFileSync(path.join(at, 'memory.json'), JSON.stringify({
    photo: 'photo.jpg', splat: 'memory.splat', width: 640, height: 480, fx: 500, fov_y_deg: 51.3, splats: 5200,
    depth_m: { median: 3, p10: 1.7, p90: 3 }, model: 'sharp', made: `2026-10-26T23:0${id.slice(-1) === 'a' ? 1 : 2}:00-04:00`,
    ...(world ? { world } : {}),
  }));
}

test.beforeAll(() => {
  rmSync(worldMoment, { recursive: true, force: true });
  mkdirSync(path.join(worldMoment, 'splat'), { recursive: true });
  writeFileSync(path.join(worldMoment, 'splat', 'world.splat'), room());
  writeFileSync(path.join(worldMoment, 'scene.json'), JSON.stringify({ splat: { status: 'lidar', lidar: 'splat/world.splat', bounds: null } }));
  // Standing in the room 1.5 m up, looking down -z; the second 2 m further on.
  const pose = (z: number) => [[1, 0, 0, 0], [0, 1, 0, 1.5], [0, 0, 1, z], [0, 0, 0, 1]];
  // The first filmed with the phone held upright: its frame a quarter turn round (the camera's
  // right is the world's up), as an iPhone's scan records it.
  const upright = [[0, -1, 0, 0], [1, 0, 0, 1.5], [0, 0, 1, 1], [0, 0, 0, 1]];
  writeMemory(IN_WORLD[0], { moment: WORLD_MOMENT, pose: upright, scale: 1, scale_from: 'lidar' });
  writeMemory(IN_WORLD[1], { moment: WORLD_MOMENT, pose: pose(-1), scale: 1, scale_from: 'lidar' });
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

test.afterAll(() => {
  rmSync(folder, { recursive: true, force: true });
  rmSync(worldMoment, { recursive: true, force: true });
  for (const id of IN_WORLD) rmSync(path.resolve(`.daycube-e2e-vault/${DATE}/memories/${id}`), { recursive: true, force: true });
});

test('a memory opens as its photo, fades into 3D and follows the mouse', async ({ page }) => {
  await page.goto('/?view=memories');
  const item = page.getByTestId('memory-item');
  await expect(item).toHaveCount(3);
  await page.goto(`/?view=memories&memory=${DATE}/${ID}`);
  await expect(page).toHaveURL(new RegExp(`view=memories&memory=${DATE}/${ID}`));
  const view = page.getByTestId('memory-view');
  await expect(view).toHaveAttribute('data-state', 'ready', { timeout: 60_000 });
  await expect.poll(() => page.evaluate(() => (window as any).__memory?.frames ?? 0), { timeout: 30_000 }).toBeGreaterThan(3);
  expect(await page.evaluate(() => (window as any).__memory.splats)).toBe(5200);
  // Something is drawn: the canvas is not one colour.
  const colours = () => page.evaluate(() => {
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
  await expect.poll(colours, { timeout: 30_000 }).toBeGreaterThan(20);
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

test('a photo from a scan stands in its world: look round, walk away, and the others are there', async ({ page }) => {
  test.setTimeout(180_000); // software rendering here draws about a frame a second
  await page.goto(`/?view=memories&memory=${DATE}/${IN_WORLD[0]}`);
  const view = page.getByTestId('memory-view');
  await expect(view).toHaveAttribute('data-state', 'ready', { timeout: 90_000 });
  const live = () => page.evaluate(() => (window as any).__memory);
  await expect.poll(async () => (await live())?.frames ?? 0, { timeout: 30_000 }).toBeGreaterThan(3);
  const first = await live();
  expect(first.mode).toBe('world');
  expect(first.world).toBe(81 * 81 * 5);
  expect(first.markers).toBe(1); // the other photo of the scan
  expect(first.pose.y).toBeCloseTo(1.5, 1); // where the photo was taken
  expect(first.memoryOpacity).toBeCloseTo(1, 2);
  // Shown upright, not on its side; its field of view is the photo's width (640 px at f 500).
  expect(first.roll).toBeCloseTo(0, 5);
  expect(first.fov).toBeCloseTo((2 * Math.atan(320 / 500) * 180) / Math.PI, 1);
  expect(Math.abs(first.pose.yaw)).toBeLessThan(1e-3);
  await page.goto(`/?view=memories`);
  await expect(page.getByTestId('memory-item')).toHaveCount(3);
  const turned = page.getByTestId('memory-item').locator('img');
  const turns = await turned.evaluateAll((imgs) => imgs.map((i) => (i as HTMLElement).style.getPropertyValue('--turn')));
  expect(turns.sort()).toEqual(['-90deg', '0deg', '0deg']);
  await page.goto(`/?view=memories&memory=${DATE}/${IN_WORLD[0]}`);
  await expect(view).toHaveAttribute('data-state', 'ready', { timeout: 90_000 });
  await expect.poll(async () => (await live())?.frames ?? 0, { timeout: 30_000 }).toBeGreaterThan(3);
  // Walk ahead more than 3 m: the photo's own scene fades, the world stays.
  await page.keyboard.down('w');
  await expect.poll(async () => (await live()).pose.z, { timeout: 60_000 }).toBeLessThan(1 - 3.2);
  await page.keyboard.up('w');
  await expect.poll(async () => (await live()).memoryOpacity, { timeout: 20_000 }).toBeLessThan(0.05);
  // Dragging turns all the way round, not just a little.
  const box = (await view.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 600, box.y + box.height / 2, { steps: 10 });
  await page.mouse.up();
  await expect.poll(async () => Math.abs((await live()).pose.yaw), { timeout: 30_000 }).toBeGreaterThan(2);
  // Double-click: back where the photo was taken, its scene sharp again.
  await page.mouse.dblclick(box.x + 10, box.y + box.height - 10);
  await expect.poll(async () => (await live()).memoryOpacity, { timeout: 30_000 }).toBeGreaterThan(0.95);
});
