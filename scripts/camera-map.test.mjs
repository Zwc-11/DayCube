import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../apps/viewer/src/lib/cameraMap.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { prepareCameraMap, cameraMapIndex } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

function original(camera) {
  const points = camera.points.filter(p => p.lat !== undefined && p.lon !== undefined);
  if (points.length < 2) return null;
  const lat0 = points[0].lat, lon0 = points[0].lon, k = Math.cos(lat0 * Math.PI / 180);
  const xy = points.map(p => [(p.lon - lon0) * 111320 * k, (p.lat - lat0) * 110574]);
  const xs = xy.map(p => p[0]), ys = xy.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const span = Math.max(x1 - x0, y1 - y0, 10);
  const screen = xy.map(p => [12 + (p[0] - x0) / span * 176, 12 + (y1 - p[1]) / span * 176]);
  return { points, screen, line: screen.map(p => p.join(',')).join(' '), metres: Math.round(span),
    height: Math.max(60, Math.round(24 + 176 * Math.max(y1 - y0, 10) / span)) };
}

test('prepared map coordinates and SVG strings preserve the original projection', () => {
  for (let n = 0; n < 120; n++) {
    const camera = { points: Array.from({ length: n }, (_, i) => ({ t: i / 10, xyz: [0, 0, 0],
      ...(i % 11 ? { lat: 45 + Math.cos(i) / 1000, lon: -73 + Math.sin(i * 3) / 1000 } : {}) })) };
    const expected = original(camera);
    const result = prepareCameraMap(camera);
    if (!expected) { assert.equal(result, null); continue; }
    assert.equal(result.line, expected.line);
    assert.equal(result.metres, expected.metres);
    assert.equal(result.height, expected.height);
    assert.deepEqual([...result.xy], expected.screen.flat());
    assert.deepEqual(result.points, expected.points);
  }
});

test('indexed cursor lookup preserves first-match, duplicate, unordered and missing-time behavior', () => {
  for (const times of [[0, 1, 1, 2, 5], [4, 0, 2, 1], [NaN, 0, 2]]) {
    const camera = { points: times.map(t => ({ t, xyz: [0, 0, 0], lat: 45, lon: -73 })) };
    const view = prepareCameraMap(camera);
    for (const t of [-Infinity, -1, 0, .1, 1, 1.1, 2, 9, Infinity, NaN]) {
      const at = camera.points.findIndex(p => p.t >= t);
      assert.equal(cameraMapIndex(view, t), at < 0 ? times.length - 1 : at);
    }
  }
});

test('long paths use bounded coordinate storage without a spread-argument limit', () => {
  const n = 150000;
  const camera = { points: Array.from({ length: n }, (_, i) => ({ t: i, xyz: [0, 0, 0], lat: 45, lon: -73 })) };
  const view = prepareCameraMap(camera);
  assert.equal(view.xy.byteLength, n * 2 * Float64Array.BYTES_PER_ELEMENT);
  assert.equal(cameraMapIndex(view, n - 1.5), n - 1);
  assert.equal(view.metres, 10);
  assert.equal(view.line.split(' ').length, n);
});
