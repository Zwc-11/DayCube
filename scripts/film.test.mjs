import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

// apps/viewer/src/lib/film.ts: a memory's film, its points read from a .splat and its timing.
const source = await readFile(new URL('../apps/viewer/src/lib/film.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { FPS, at, cardRect, dateAndTime, particles, script, split, timecode } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

/** The viewer's 32-byte splat records: [x, y, z, scale, r, g, b, alpha]. */
function splat(rows) {
  const out = new ArrayBuffer(rows.length * 32);
  const f = new Float32Array(out);
  const b = new Uint8Array(out);
  rows.forEach(([x, y, z, s, r, g, bl, a], i) => {
    f.set([x, y, z, s, s, s], i * 8);
    b.set([r, g, bl, a, 255, 128, 128, 128], i * 32 + 24);
  });
  return out;
}

test('points are read from a splat file far to near, faint ones left out, a sample sized to cover', () => {
  const rows = [[0, 0, -1, 0.01, 10, 0, 0, 255], [0, 1, -5, 0.01, 50, 0, 0, 255], [0, 2, -3, 0.01, 30, 0, 0, 255], [0, 3, -9, 0.01, 90, 0, 0, 20]];
  const p = particles(splat(rows), 10, true);
  assert.equal(p.count, 3); // the fourth is 8% opaque
  assert.deepEqual([...p.position].filter((_, k) => k % 3 === 2), [-5, -3, -1]); // farthest first
  assert.deepEqual([...p.color].filter((_, k) => k % 4 === 0), [50, 30, 10]); // each colour with its point
  assert.ok(Math.abs(p.size[0] - 0.02) < 1e-7); // twice the mean scale
  const many = splat(Array.from({ length: 1000 }, (_, i) => [i, 0, -1, 0.01, 0, 0, 0, 255]));
  const sampled = particles(many, 250, false);
  assert.equal(sampled.count, 250); // every 4th
  assert.ok(Math.abs(sampled.size[0] - 0.02 * Math.sqrt(4)) < 1e-7); // a sample of a quarter covers as the whole
});

test('a near subject and its background are split; distances that run on are not', () => {
  const near = Array.from({ length: 300 }, (_, i) => 1.2 + 0.3 * (i / 300));
  const far = Array.from({ length: 2700 }, (_, i) => 3 + 0.5 * (i / 2700));
  const cut = split(Float32Array.from([...near, ...far]));
  assert.ok(cut > 1.5 && cut < 3, `${cut}`);
  assert.equal(split(Float32Array.from({ length: 3000 }, (_, i) => 1 + (9 * i) / 3000)), null); // a wall going away
  const speck = Array.from({ length: 60 }, () => 1.2); // 2%: no subject
  assert.equal(split(Float32Array.from([...speck, ...far])), null);
  assert.equal(split(Float32Array.from([2, 2, 2])), null);
});

test('the card sits left of the scene in 16:9 and under it in 9:16, never wider than its share', () => {
  const wide = cardRect(1920, 1080, 0.75);
  assert.deepEqual([wide.w, wide.h].map(Math.round), [486, 648]);
  assert.ok(Math.abs(wide.x + wide.w / 2 - 0.27 * 1920) < 1e-9 && Math.abs(wide.y + wide.h / 2 - 0.42 * 1080) < 1e-9);
  const tall = cardRect(1080, 1920, 0.75);
  assert.ok(Math.abs(tall.h - 0.28 * 1920) < 1e-9 && tall.y > 1920 / 2);
  const panorama = cardRect(1920, 1080, 3); // a wide photo is held to its width
  assert.ok(Math.abs(panorama.w - 0.34 * 1920) < 1e-9);
});

test('beats run in order inside the film; time is written as the reference film writes it', () => {
  for (const s of [script(false), script(true)]) {
    assert.ok(s.open[1] <= s.recall[0] && s.recall[0] < s.card[0] && s.card[1] < s.end);
    assert.equal(at(s.open[0] - 1, s.open), 0);
    assert.equal(at(s.open[1] + 1, s.open), 1);
  }
  assert.equal(script(false).grow, null);
  assert.equal(FPS, 30);
  assert.equal(timecode(7.4), '00:00:07:12');
  assert.equal(timecode(61), '00:01:01:00');
  assert.deepEqual(dateAndTime(new Date(2026, 9, 1, 7, 48)), ['2026.10.1', '07:48']);
  assert.deepEqual(dateAndTime(null), ['', '']);
});
