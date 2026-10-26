import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import ts from 'typescript';

const source = await readFile(new URL('../apps/viewer/src/lib/sceneResources.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { SceneResources, disposeMaterials, disposeObject } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

test('scene releases run in reverse order, once, including an early release and a late arrival', () => {
  const owned = new SceneResources();
  const calls = [];
  owned.own(() => calls.push('renderer'));
  const early = owned.own(() => calls.push('first load'));
  early();
  early();
  owned.own(() => calls.push('animation'));
  owned.dispose();
  owned.dispose();
  owned.own(() => calls.push('late model'));
  assert.deepEqual(calls, ['first load', 'animation', 'renderer', 'late model']);
});

test('a failing release is reported and does not prevent the other resources from closing', () => {
  const owned = new SceneResources();
  const calls = [];
  const warnings = [];
  const warn = console.warn;
  console.warn = (...args) => warnings.push(args);
  try {
    owned.own(() => calls.push('renderer'));
    owned.own(() => { throw new Error('synthetic cleanup failure'); });
    owned.own(() => calls.push('animation'));
    owned.dispose();
    assert.deepEqual(calls, ['animation', 'renderer']);
    assert.equal(warnings.length, 1);
    assert.match(warnings[0][1].message, /synthetic/);
  } finally { console.warn = warn; }
});

test('shared model geometry, material arrays and textures are each released once', () => {
  const calls = [];
  const texture = { isTexture: true, dispose: () => calls.push('texture') };
  const a = { map: texture, dispose: () => calls.push('a') };
  const b = { map: texture, normalMap: texture, dispose: () => calls.push('b') };
  const geometry = { dispose: () => calls.push('geometry') };
  const nodes = [{ isMesh: true, geometry, material: [a, b] }, { isMesh: true, geometry, material: a }, {}];
  disposeObject({ traverse: visit => nodes.forEach(visit) });
  assert.deepEqual(calls, ['geometry', 'texture', 'a', 'b']);
  const old = new Set([a, b]);
  disposeMaterials(old);
  assert.equal(old.size, 0); // released source materials must not keep textures alive in the owner
});
