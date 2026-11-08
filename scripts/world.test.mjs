import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
import { Object3D, Vector3 } from 'three';
import { WGS84_ELLIPSOID } from '3d-tiles-renderer/three';
import { ReorientationPlugin } from '3d-tiles-renderer/three/plugins';

// apps/viewer/src/lib/world.ts: a world's local frame (x east, y up, z south), the open map's
// footprints in it, and the frame Google's tiles are reoriented into, which must be the same.
const source = await readFile(new URL('../apps/viewer/src/lib/world.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { DEFAULT_LAYERS, cropBoxes, focus, footprints, reach, savedLayers, toLocal } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

const origin = { lat: 43.6447, lon: -79.3801, ground_m: 40 };
const RAD = Math.PI / 180;
const NORTH_100 = 100 / 111_132.954; // degrees of latitude in 100 m, near 43.6 degrees
const close = (a, b, eps, what) => assert.ok(Math.abs(a - b) < eps, `${what}: ${a} vs ${b}`);

/** The viewer's Mapped layer: tiles reoriented to the origin by the plugin, then turned half round. */
function mappedFrame() {
  const tiles = { group: new Object3D(), ellipsoid: WGS84_ELLIPSOID, root: null, addEventListener() {}, removeEventListener() {} };
  const plugin = new ReorientationPlugin({ lat: origin.lat * RAD, lon: origin.lon * RAD, height: origin.ground_m });
  plugin.init(tiles);
  plugin.transformLatLonHeightToOrigin(origin.lat * RAD, origin.lon * RAD, origin.ground_m);
  const turned = new Object3D();
  turned.rotation.y = Math.PI;
  turned.add(tiles.group);
  turned.updateMatrixWorld(true);
  return (lat, lon, h) => WGS84_ELLIPSOID.getCartographicToPosition(lat * RAD, lon * RAD, h, new Vector3()).applyMatrix4(tiles.group.matrixWorld);
}

test('100 m north is z = -100, east is +x, up is +y, in the world frame and in the tiles', () => {
  const [x, y, z] = toLocal(origin.lat + NORTH_100, origin.lon, origin.ground_m, origin);
  close(z, -100, 0.1, 'north z');
  close(x, 0, 1e-6, 'north x');
  close(y, -0.0008, 0.01, 'north y (the earth curves away 0.8 mm in 100 m)');
  const tile = mappedFrame();
  for (const [lat, lon, h] of [[origin.lat + NORTH_100, origin.lon, origin.ground_m], [origin.lat, origin.lon + 0.002, origin.ground_m + 30], [origin.lat - 0.003, origin.lon - 0.004, origin.ground_m + 5]]) {
    const ours = toLocal(lat, lon, h, origin);
    const theirs = tile(lat, lon, h);
    for (const [k, axis] of ['x', 'y', 'z'].entries()) close(ours[k], theirs[axis], 0.01, `${axis} at ${lat},${lon}`); // within a centimetre
  }
  const [ex, ey] = toLocal(origin.lat, origin.lon + 0.002, origin.ground_m + 30, origin);
  assert.ok(ex > 160 && ex < 162, `${ex}`); // 0.002 degrees of longitude at 43.6 N is 161 m
  close(ey, 30, 0.01, 'up');
});

test('open footprints become rings in (x, north) with a base below the top', () => {
  const square = (lat, lon, d) => [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]];
  const prints = footprints({
    type: 'FeatureCollection',
    features: [
      { type: 'Feature', geometry: { type: 'Polygon', coordinates: [square(origin.lat, origin.lon, 0.0005)] }, properties: { id: 1, height_m: 42, min_height_m: 4 } },
      { type: 'Feature', geometry: { type: 'Polygon', coordinates: [square(origin.lat, origin.lon, 0.0005)] }, properties: { id: 2, height_m: 0 } },
      { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[[0, 0], [1, 1]]] }, properties: { id: 3, height_m: 9 } },
    ],
  }, origin);
  assert.equal(prints.length, 2); // a ring of two points is no building
  assert.equal(prints[0].ring.length, 4); // closed rings lose their repeated last point
  assert.deepEqual([prints[0].base, prints[0].top], [4, 42]);
  assert.deepEqual([prints[1].base, prints[1].top], [0, 2.5]); // no height: still a low block
  const [x, n] = prints[0].ring[2];
  assert.ok(x > 39 && x < 41 && n > 55 && n < 56, `${x}, ${n}`); // 0.0005 degrees: 40 m east, 55.6 m north
});

test('the map is hidden only inside seen islands; the view starts on the first island', () => {
  const island = (id, kind, min, max) => ({ id, kind, splat: `${id}.splat`, points: 1, enu: [], located_by: 'test', error_m: null, bounds: { min, max } });
  const world = { id: 'wd_1', date: '2026-11-05', title: 't', origin, islands: [island('a', 'seen', [-10, 0, -20], [10, 8, 20]), island('b', 'imagined', [0, 0, 0], [1, 1, 1])] };
  assert.deepEqual(cropBoxes(world), [{ min: [-10, 0, -20], max: [10, 8, 20] }]);
  assert.deepEqual(focus(world), [0, 4, 0]);
  assert.ok(Math.abs(reach(world) - 1.2 * Math.hypot(20, 8, 40)) < 1e-9);
  assert.deepEqual(focus({ ...world, islands: [] }), [0, 0, 0]);
  assert.equal(reach({ ...world, islands: [] }), 120);
});

test('the layers come back as chosen, and storage that throws or holds junk is survived', () => {
  globalThis.localStorage = { getItem: () => JSON.stringify({ known: false, mapped: 'yes', bogus: true }), setItem() {} };
  assert.deepEqual(savedLayers(), { ...DEFAULT_LAYERS, known: false });
  globalThis.localStorage = { getItem: () => { throw new Error('blocked'); }, setItem() {} };
  assert.deepEqual(savedLayers(), DEFAULT_LAYERS);
  delete globalThis.localStorage;
  assert.deepEqual(savedLayers(), DEFAULT_LAYERS);
});
