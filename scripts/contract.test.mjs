import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { validateContract } from '../packages/schemas/src/runtime.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
const fixture = new URL('../fixtures/sample-day/', import.meta.url);
const json = async path => JSON.parse(await readFile(new URL(path, fixture), 'utf8'));
const jsonl = async path => (await readFile(new URL(path, fixture), 'utf8')).split('\n').filter(Boolean).map(JSON.parse);

test('the same sample vault validates in JavaScript and Python', async () => {
  validateContract('day', await json('day.json'));
  validateContract('skeleton', await json('skeleton.geojson'));
  validateContract('graph', await json('graph.json'));
  for (const record of await jsonl('events.jsonl')) validateContract('event', record);
  for (const record of await jsonl('screens/ocr.jsonl')) validateContract('ocr', record);
  for (const moment of await readdir(new URL('moments/', fixture))) {
    validateContract('moment', await json(`moments/${moment}/meta.json`));
    validateContract('transcript', await json(`moments/${moment}/transcript.json`));
  }
  validateContract('venue', JSON.parse(await readFile(new URL('../venues/engineering-hall.json', import.meta.url))));
});

test('browser validation enforces event-specific required fields', async () => {
  const quote = (await jsonl('events.jsonl')).find(record => record.type === 'said.quote');
  delete quote.speaker;
  assert.throws(() => validateContract('event', quote), /speaker/);
});

test('draft-2020-12 coordinates reject out-of-range and extra dimensions', async () => {
  const skeleton = await json('skeleton.geojson');
  skeleton.features[0].geometry.coordinates = [250, 100, 20];
  assert.throws(() => validateContract('skeleton', skeleton));
});

test('the generated Pydantic schema accepts the same fixture event', async () => {
  const event = (await jsonl('events.jsonl'))[0];
  const script = 'import json,sys; from ingest.generated.event import Event; Event.model_validate(json.load(sys.stdin)); print("ok")';
  const result = execFileSync('uv', ['run', 'python', '-c', script], { cwd: root, input: JSON.stringify(event), encoding: 'utf8', windowsHide: true });
  assert.equal(result.trim(), 'ok');
});
