import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// apps/viewer/src/lib/memories.ts: a scan's photo turned upright, and its scale in the scan.
const source = await readFile(new URL('../apps/viewer/src/lib/memories.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { photoRoll, quarterTurns, uprightFovY, worldScale } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

/** A camera rolled by `deg` about its viewing axis (OpenGL: looking down -z), as a 4 x 4 pose. */
function rolled(deg) {
  const a = (deg * Math.PI) / 180;
  return [[Math.cos(a), -Math.sin(a), 0, 0], [Math.sin(a), Math.cos(a), 0, 1.5], [0, 0, 1, 0], [0, 0, 0, 1]];
}

const memory = (id, world) => ({ id, date: '2026-10-24', width: 1920, height: 1440, fx: 1333.7, fov_y_deg: 56.7, world });

test('an iPhone frame filmed upright is turned back a quarter, its field of view its width', () => {
  assert.equal(quarterTurns(memory('a', null)), 0);
  assert.equal(uprightFovY(memory('a', null)), 56.7);
  for (const [deg, turns] of [[0, 0], [-91, -1], [-97, -1], [88, 1], [180, 2], [-180, 2], [30, 0]]) {
    const m = memory('a', { pose: rolled(deg) });
    assert.ok(Math.abs(photoRoll(m) - deg) < 1e-9 || Math.abs(Math.abs(photoRoll(m)) - 180) < 1e-9, `${deg}`);
    assert.equal(quarterTurns(m), turns, `${deg}`);
  }
  // Turned a quarter: the upright height is the frame's width, 2 atan(960 / 1333.7) = 71.5 degrees.
  assert.ok(Math.abs(uprightFovY(memory('a', { pose: rolled(-91) })) - 71.5) < 0.05);
});

test('a frame without its own LiDAR scale takes the median of its scan', () => {
  const w = (moment, scale, from) => ({ moment, pose: rolled(0), scale, scale_from: from });
  const all = [
    memory('a', w('mo_1', 1, 'none')),
    memory('b', w('mo_1', 0.7, 'lidar')),
    memory('c', w('mo_1', 2.0, 'lidar')),
    memory('d', w('mo_1', 2.6, 'lidar')),
    memory('e', w('mo_2', 9, 'lidar')), // another scan's: not counted
  ];
  assert.equal(worldScale(all[0], all), 2.0);
  assert.equal(worldScale(all[1], all), 0.7); // its own
  assert.equal(worldScale(memory('x', w('mo_3', 1, 'none')), all), 1); // nothing to go by
  assert.equal(worldScale(memory('y', null), all), 1);
});
