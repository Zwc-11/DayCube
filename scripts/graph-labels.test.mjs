import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../apps/viewer/src/lib/graphLabels.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { GraphLabels } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('reused candidates exactly match the old priority, degree and local-index ordering', () => {
  const degree = Uint32Array.from({ length: 128 }, (_, i) => (i * 13) % 7);
  const local = Int32Array.from({ length: 97 }, (_, i) => (i * 17) % 128);
  const labels = new GraphLabels(local, degree);
  const items = labels.items;
  const alpha = labels.alpha;
  for (let frame = 0; frame < 200; frame++) {
    labels.reset();
    const reference = [];
    for (let i = 0; i < local.length; i++) {
      if ((i + frame) % 3 === 0) continue;
      const rank = [0, 2, 3, 4][(i * 3 + frame) % 4];
      reference.push({ l: i, rank, alpha: (i + .123456789) / 128 });
    }
    reference.sort((a, b) => b.rank - a.rank || degree[local[b.l]] - degree[local[a.l]] || a.l - b.l);
    const candidates = new Map(reference.map((item) => [item.l, item]));
    for (const i of labels.order) {
      const item = candidates.get(i);
      if (item) labels.add(i, item.rank, item.alpha);
    }
    const actual = [];
    for (let group = 0; group < 4; group++) for (let k = 0; k < labels.counts[group]; k++) {
      const i = labels.items[group * labels.capacity + k];
      actual.push({ l: i, rank: labels.rank[i], alpha: labels.alpha[i] });
    }
    assert.deepEqual(actual, reference);
    assert.equal(labels.items, items);
    assert.equal(labels.alpha, alpha);
  }
});

test('empty and replaced visible models own separate bounded storage', () => {
  const empty = new GraphLabels(new Int32Array(), new Uint32Array());
  assert.equal(empty.items.length, 0);
  assert.deepEqual([...empty.counts], [0, 0, 0, 0]);
  const visible = new GraphLabels(Int32Array.of(2, 0), Uint32Array.of(1, 7, 3));
  assert.deepEqual([...visible.order], [0, 1]);
  assert.equal(visible.items.length, 8);
  visible.add(0, 4, .5);
  visible.reset();
  assert.deepEqual([...visible.counts], [0, 0, 0, 0]);
});
