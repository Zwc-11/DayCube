import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';
const source = await readFile(new URL('../apps/viewer/src/lib/cameraPose.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { createCameraPose, poseAt } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

// Reference from SplatView at 2ffd223; preserve settling, arithmetic and ties.
function original(cams, at) {
  const ts = cams.t, t = Math.max(at, ts[0] + .5);
  let lo = 0, hi = ts.length - 1;
  if (t <= ts[0]) hi = 0;
  else if (t >= ts[hi]) lo = hi;
  else while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (ts[mid] <= t) lo = mid; else hi = mid;
  }
  const a = Math.min(lo, hi), b = Math.max(lo, hi);
  const f = b > a ? (t - ts[a]) / (ts[b] - ts[a]) : 0;
  const mix = (p, q) => p.map((v, k) => v + (q[k] - v) * f);
  const position = mix(cams.position[a], cams.position[b]), forward = mix(cams.forward[a], cams.forward[b]);
  const n = Math.hypot(forward[0], forward[1], forward[2]) || 1;
  return { position, forward: forward.map(v => v / n) };
}

test('poses preserve settling, endpoints, ties and nonfinite query behavior', () => {
  for (const times of [[0], [0, .1, .2], [0, 1, 1, 2, 4], [5, 5, 5], [-4, -2, 0, 9]]) {
    for (const zero of [true, false]) {
      const cams = { t: times, position: times.map((_, i) => [i * .3, -i, i * .7]),
        forward: times.map((_, i) => zero ? [0, 0, 0] : [Math.sin(i), .5, Math.cos(i)]) };
      const unchanged = structuredClone(cams), output = createCameraPose();
      for (const t of [-Infinity, -5, 0, .1, .5, 1, 1.5, 2, 3, 5, 9, Infinity, NaN]) {
        assert.deepEqual(poseAt(cams, t, output), original(cams, t));
        assert.deepEqual(poseAt(cams, t), original(cams, t));
      }
      assert.deepEqual(cams, unchanged);
    }
  }
});

test('viewers reuse private output buffers while snapshots remain independent', () => {
  const cams = { t: [0, 1, 2], position: [[0, 0, 0], [1, 2, 3], [2, 4, 6]], forward: [[1, 0, 0], [0, 0, 1], [-1, 0, 0]] };
  const a = createCameraPose(), b = createCameraPose(), position = a.position, forward = a.forward;
  const snapshot = poseAt(cams, .75), expected = structuredClone(snapshot);
  for (let i = 0; i < 100; i++) {
    assert.equal(poseAt(cams, i / 100, a), a);
    assert.equal(a.position, position); assert.equal(a.forward, forward);
  }
  assert.deepEqual(snapshot, expected);
  assert.notEqual(a.position, b.position); assert.notEqual(a.forward, b.forward);
});
