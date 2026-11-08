import { test, expect, type Page } from '@playwright/test';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';

// A FICTIONAL world in the test vault, as ingest/world writes one: an island of splats (a short
// street of coloured walls) at made-up coordinates, the open map's answer kept in vault/maps/osm
// (so no network), and, for the Mapped layer, a one-tile fixture tileset served in place of
// Google's (page.route): nothing reaches tile.googleapis.com.
const DATE = '2026-10-21';
const WID = 'wd_0000000e2e01';
const folder = path.resolve(`.daycube-e2e-vault/${DATE}/world/${WID}`);
const ORIGIN = { lat: 45.0, lon: -73.0, ground_m: 30 };
const SHIFT: [number, number, number] = [5, 0, -10];

function island(): { bytes: Buffer; count: number } {
  const list: number[][] = [];
  for (let i = 0; i <= 60; i += 1) {
    for (let j = 0; j <= 30; j += 1) {
      const z = -18 + (36 * i) / 60;
      const y = (6 * j) / 30;
      list.push([-8 + (16 * j) / 30, 0, z, 0.35, 120, 110 + i, 90]); // street
      list.push([-8, y, z, 0.3, 200, 60 + 2 * i, 60 + 4 * j]); // west wall
      list.push([8, y, z, 0.3, 60 + 4 * j, 80 + 2 * i, 210]); // east wall
    }
  }
  const out = Buffer.alloc(list.length * 32);
  list.forEach(([x, y, z, s, r, g, b], n) => {
    const o = n * 32;
    [x, y, z, s, s, s].forEach((v, k) => out.writeFloatLE(v, o + 4 * k));
    [r, g, b, 250].forEach((v, k) => out.writeUInt8(Math.round(v), o + 24 + k));
    [255, 128, 128, 128].forEach((v, k) => out.writeUInt8(v, o + 28 + k));
  });
  return { bytes: out, count: list.length };
}

// WGS84, as lib/world.ts
const A = 6378137;
const F = 1 / 298.257223563;
const E2 = F * (2 - F);
const RAD = Math.PI / 180;
function ecef(lat: number, lon: number, h: number): number[] {
  const [p, l] = [lat * RAD, lon * RAD];
  const n = A / Math.sqrt(1 - E2 * Math.sin(p) ** 2);
  return [(n + h) * Math.cos(p) * Math.cos(l), (n + h) * Math.cos(p) * Math.sin(l), (n * (1 - E2) + h) * Math.sin(p)];
}

/** An Overpass "out geom tags" answer: two blocks near the origin. */
function overpass(): string {
  const block = (id: number, north: number, east: number, w: number, d: number, height: string) => {
    const lat = (m: number) => ORIGIN.lat + m / 111_132.954;
    const lon = (m: number) => ORIGIN.lon + m / (111_319.49 * Math.cos(ORIGIN.lat * RAD));
    const ring = [[north, east], [north, east + w], [north + d, east + w], [north + d, east], [north, east]];
    return { type: 'way', id, tags: { building: 'yes', height }, geometry: ring.map(([n, e]) => ({ lat: lat(n), lon: lon(e) })) };
  };
  return JSON.stringify({ elements: [block(1, 30, -20, 40, 25, '24'), block(2, -60, 20, 30, 30, '12')] });
}

/** A glTF binary: one box, 12 m, in a loud colour, its asset carrying the tile's copyright. */
function boxGlb(copyright: string): Buffer {
  const h = 6;
  const corners = [[-h, -h, -h], [h, -h, -h], [h, h, -h], [-h, h, -h], [-h, -h, h], [h, -h, h], [h, h, h], [-h, h, h]];
  const faces = [0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 2, 3, 7, 2, 7, 6, 1, 2, 6, 1, 6, 5, 0, 4, 7, 0, 7, 3];
  const positions = Buffer.alloc(corners.length * 12);
  corners.flat().forEach((v, k) => positions.writeFloatLE(v, 4 * k));
  const indices = Buffer.alloc(faces.length * 2);
  faces.forEach((v, k) => indices.writeUInt16LE(v, 2 * k));
  const bin = Buffer.concat([positions, indices]);
  const json = JSON.stringify({
    asset: { version: '2.0', copyright },
    scenes: [{ nodes: [0] }], scene: 0, nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, material: 0 }] }],
    materials: [{ pbrMetallicRoughness: { baseColorFactor: [1, 0.35, 0.05, 1], metallicFactor: 0, roughnessFactor: 1 } }],
    buffers: [{ byteLength: bin.length }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: positions.length }, { buffer: 0, byteOffset: positions.length, byteLength: indices.length }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: corners.length, type: 'VEC3', min: [-h, -h, -h], max: [h, h, h] },
      { bufferView: 1, componentType: 5123, count: faces.length, type: 'SCALAR' },
    ],
  });
  const pad = (b: Buffer, fill: number) => Buffer.concat([b, Buffer.alloc((4 - (b.length % 4)) % 4, fill)]);
  const jsonChunk = pad(Buffer.from(json), 0x20);
  const binChunk = pad(bin, 0);
  const header = Buffer.alloc(12);
  header.writeUInt32LE(0x46546c67, 0);
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(12 + 8 + jsonChunk.length + 8 + binChunk.length, 8);
  const chunk = (b: Buffer, type: number) => { const c = Buffer.alloc(8); c.writeUInt32LE(b.length, 0); c.writeUInt32LE(type, 4); return Buffer.concat([c, b]); };
  return Buffer.concat([header, chunk(jsonChunk, 0x4e4f534a), chunk(binChunk, 0x004e4942)]);
}

/** A one-tile tileset: the box 25 m east of the origin, on the ground, in its east-north-up frame. */
function rootTileset(): string {
  const lon = ORIGIN.lon + 25 / (111_319.49 * Math.cos(ORIGIN.lat * RAD));
  const [x, y, z] = ecef(ORIGIN.lat, lon, ORIGIN.ground_m + 6);
  const [p, l] = [ORIGIN.lat * RAD, lon * RAD];
  const east = [-Math.sin(l), Math.cos(l), 0];
  const north = [-Math.sin(p) * Math.cos(l), -Math.sin(p) * Math.sin(l), Math.cos(p)];
  const up = [Math.cos(p) * Math.cos(l), Math.cos(p) * Math.sin(l), Math.sin(p)];
  return JSON.stringify({
    asset: { version: '1.1' },
    geometricError: 500,
    root: {
      transform: [...east, 0, ...north, 0, ...up, 0, x, y, z, 1], // column-major
      boundingVolume: { sphere: [0, 0, 0, 12] },
      geometricError: 0,
      refine: 'REPLACE',
      content: { uri: 'fixture.glb?session=fixture-session' },
    },
  });
}

async function withToken(page: Page): Promise<void> {
  const token = readFileSync(path.resolve('.daycube-e2e-vault/.token'), 'utf8').trim();
  await page.addInitScript(
    `Object.defineProperty(window, '__DAYCUBE_DESKTOP__', { value: Object.freeze({ token: ${JSON.stringify(token)}, version: 'test' }) });`,
  );
}

/** The scene's canvas: how many colours (64 x 40 copy), and a fingerprint of its pixels. */
const picture = (page: Page) => page.evaluate(() => {
  const canvas = document.querySelector('.world__canvas') as HTMLCanvasElement;
  const copy = document.createElement('canvas');
  [copy.width, copy.height] = [64, 40];
  const ctx = copy.getContext('2d')!;
  ctx.drawImage(canvas, 0, 0, 64, 40);
  const data = ctx.getImageData(0, 0, 64, 40).data;
  const seen = new Set<string>();
  let sum = 0;
  for (let k = 0; k < data.length; k += 4) {
    seen.add(`${data[k] >> 4},${data[k + 1] >> 4},${data[k + 2] >> 4}`);
    sum = (sum * 31 + data[k] + 7 * data[k + 1] + 13 * data[k + 2]) % 1_000_000_007;
  }
  return { colours: seen.size, sum };
});
const live = (page: Page) => page.evaluate(() => (window as any).__world);

let points = 0;
test.beforeAll(() => {
  rmSync(folder, { recursive: true, force: true });
  mkdirSync(folder, { recursive: true });
  const made = island();
  points = made.count;
  writeFileSync(path.join(folder, 'is_01.splat'), made.bytes);
  const enu = [[1, 0, 0, SHIFT[0]], [0, 1, 0, SHIFT[1]], [0, 0, 1, SHIFT[2]], [0, 0, 0, 1]];
  writeFileSync(path.join(folder, 'world.json'), JSON.stringify({
    id: WID, date: DATE, title: 'A made-up street', made: '2026-11-08T17:00:00-05:00', source: null, origin: ORIGIN,
    islands: [{
      id: 'is_01', splat: 'is_01.splat', points: made.count, enu, kind: 'seen', located_by: 'manual', error_m: null, shots: [[0, 4]],
      bounds: { min: [-8 + SHIFT[0], 0, -18 + SHIFT[2]], max: [8 + SHIFT[0], 6, 18 + SHIFT[2]] },
    }],
  }));
  const osm = path.resolve('.daycube-e2e-vault/maps/osm');
  mkdirSync(osm, { recursive: true });
  const where = `around:600,${ORIGIN.lat.toFixed(3)},${ORIGIN.lon.toFixed(3)}`;
  const key = createHash('sha256').update(`https://overpass-api.de/api/interpreter:${where}`).digest('hex');
  writeFileSync(path.join(osm, `${key}.json`), overpass()); // what ingest's Overpass adapter keeps
});

test.afterAll(() => rmSync(folder, { recursive: true, force: true }));

test('a world opens from its list: the island in colour, the open map around it, Escape back', async ({ page }) => {
  test.setTimeout(180_000);
  await page.goto('/?view=world');
  const item = page.getByTestId('world-item').filter({ hasText: 'A made-up street' });
  await expect(item).toHaveCount(1);
  await item.click();
  await expect(page).toHaveURL(new RegExp(`view=world&date=${DATE}&world=${WID}`));
  const view = page.getByTestId('world-view');
  await expect(view).toHaveAttribute('data-state', 'ready', { timeout: 90_000 });
  await expect.poll(async () => (await live(page))?.frames ?? 0, { timeout: 30_000 }).toBeGreaterThan(0);
  const first = await live(page);
  expect(first.islands).toEqual([{ id: 'is_01', kind: 'seen', visible: true, splats: points }]);
  expect(first.buildings).toBe(2); // the two blocks from the kept Overpass answer
  expect(first.mapped).toBeNull(); // no token: no Google key asked for
  await expect.poll(async () => (await picture(page)).colours, { timeout: 30_000 }).toBeGreaterThan(20);
  await expect(page.getByText('© OpenStreetMap contributors, ODbL 1.0')).toBeVisible();
  // Mapped needs the key, which needs the vault token: off, with why.
  await expect(page.getByRole('button', { name: 'Mapped' })).toBeDisabled();
  await expect(page.getByRole('note')).toContainText('Maps API key');
  // Known off: the buildings go, and the credit with them.
  const before = await picture(page);
  await page.getByRole('button', { name: 'Known' }).click();
  await expect.poll(async () => (await live(page)).layers.known, { timeout: 10_000 }).toBe(false);
  await expect.poll(async () => (await picture(page)).sum, { timeout: 30_000 }).not.toBe(before.sum);
  await expect(page.getByText('© OpenStreetMap contributors, ODbL 1.0')).toHaveCount(0);
  await page.getByRole('button', { name: 'Known' }).click(); // as it was, for the next test
  await expect.poll(async () => (await live(page)).layers.known, { timeout: 10_000 }).toBe(true);
  await page.keyboard.press('Escape');
  await expect(page).toHaveURL(/view=world$/);
  await expect(view).toHaveCount(0);
});

test("with a key the Mapped layer streams tiles, credits Google and the tiles, and keeps none", async ({ page }) => {
  test.setTimeout(180_000);
  await withToken(page);
  await page.route('**/api/maps/config', (route) => route.fulfill({ json: { google_tiles_key: 'test-key' } }));
  const asked: string[] = [];
  await page.route('https://tile.googleapis.com/**', (route) => {
    const url = new URL(route.request().url());
    asked.push(`${url.pathname}?${url.searchParams.get('key')}|${url.searchParams.get('session')}`);
    if (url.pathname.endsWith('/root.json')) return route.fulfill({ contentType: 'application/json', body: rootTileset() });
    if (url.pathname.endsWith('/fixture.glb')) return route.fulfill({ contentType: 'model/gltf-binary', body: boxGlb('Fixture imagery') });
    return route.fulfill({ status: 404, body: '' });
  });
  await page.goto(`/?view=world&date=${DATE}&world=${WID}`);
  await expect(page.getByTestId('world-view')).toHaveAttribute('data-state', 'ready', { timeout: 90_000 });
  await expect(page.getByRole('button', { name: 'Mapped' })).toBeEnabled();
  await expect.poll(async () => (await live(page))?.mapped?.tiles ?? 0, { timeout: 60_000 }).toBeGreaterThan(0);
  const credits = page.getByTestId('google-attribution');
  await expect(credits.getByRole('img', { name: 'Google Maps' })).toBeVisible(); // Google's own logo file
  await expect(credits).toContainText('Fixture imagery');
  await expect(credits).toContainText("today's map");
  // The key goes with each request to Google's host, and the session from the root after it.
  expect(asked[0]).toBe('/v1/3dtiles/root.json?test-key|null');
  expect(asked).toContain('/v1/3dtiles/fixture.glb?test-key|fixture-session');
  // Shown only: nothing of the tiles kept in Cache Storage or IndexedDB.
  expect(await page.evaluate(async () => (await caches.keys()).length)).toBe(0);
  expect(await page.evaluate(async () => (await indexedDB.databases()).map((d) => d.name))).toEqual([]);
  // Mapped off: the tiles' credit goes.
  await page.getByRole('button', { name: 'Mapped' }).click();
  await expect(credits).toHaveCount(0);
  await page.getByRole('button', { name: 'Mapped' }).click();
});
