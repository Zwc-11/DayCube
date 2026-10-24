import assert from 'node:assert/strict';
import test from 'node:test';
import { consumeSse } from '../src/sse.mjs';

const encode = text => new TextEncoder().encode(text);

test('an event arrives before EOF, and done cancels the reader', { timeout: 2000 }, async () => {
  let controller, cancelled = 0;
  const body = new ReadableStream({ start(c) { controller = c; }, cancel() { cancelled++; } });
  let arrived;
  const early = new Promise(resolve => { arrived = resolve; });
  const events = [];
  const reading = consumeSse(new Response(body), event => { events.push(event); arrived(); });
  controller.enqueue(encode('event: location\ndata: {"t":3600}\n\n'));
  await early;
  assert.deepEqual(events, [{ event: 'location', data: '{"t":3600}' }]);
  controller.enqueue(encode('event: done\ndata: {}\n\n'));
  await reading;
  assert.equal(cancelled, 1);
  assert.equal(body.locked, false);
});

test('UTF-8, CRLF and multiline data survive every byte boundary', async () => {
  const bytes = encode(': comment\r\nevent: answer\r\ndata: café\r\ndata: 東京\r\n\r\n');
  const expected = [{ event: 'answer', data: 'café\n東京' }];
  for (let split = 1; split < bytes.length; split++) {
    const body = new ReadableStream({ start(c) {
      c.enqueue(bytes.slice(0, split)); c.enqueue(bytes.slice(split)); c.close();
    } });
    const events = [];
    await consumeSse(new Response(body), event => events.push(event));
    assert.deepEqual(events, expected);
    assert.equal(body.locked, false);
  }
});

test('CR-only separators and default message events are supported', async () => {
  const events = [];
  await consumeSse(new Response('data: one\r\revent: next\rdata: two\r\r'), e => events.push(e));
  assert.deepEqual(events, [{ event: 'message', data: 'one' }, { event: 'next', data: 'two' }]);
});

test('truncated final events are not dispatched', async () => {
  const events = [];
  await consumeSse(new Response('data: incomplete\n'), e => events.push(e));
  assert.deepEqual(events, []);
});

test('oversized lines and multiline events fail and release the reader', async () => {
  for (const text of ['data: ' + 'x'.repeat(100), 'data: 1234567890\ndata: 1234567890\n\n']) {
    const response = new Response(text);
    await assert.rejects(consumeSse(response, () => {}, 20), /buffer limit/);
    assert.equal(response.body.locked, false);
  }
});

test('callback failures cancel the stream and preserve the original failure', async () => {
  let cancelled = 0;
  const body = new ReadableStream({
    start(c) { c.enqueue(encode('data: value\n\n')); },
    cancel() { cancelled++; throw new Error('cleanup failure'); },
  });
  await assert.rejects(consumeSse(new Response(body), () => { throw new Error('consumer failure'); }), /consumer failure/);
  assert.equal(cancelled, 1);
  assert.equal(body.locked, false);
});

test('invalid UTF-8 fails instead of altering evidence text', async () => {
  const response = new Response(new Uint8Array([0xff]));
  await assert.rejects(consumeSse(response, () => {}), TypeError);
  assert.equal(response.body.locked, false);
});

test('a stream read failure propagates and releases its lock', async () => {
  const body = new ReadableStream({ start(c) { c.error(new Error('network failure')); } });
  await assert.rejects(consumeSse(new Response(body), () => {}), /network failure/);
  assert.equal(body.locked, false);
});

test('missing body and invalid limits fail explicitly', async () => {
  await assert.rejects(consumeSse(new Response(null), () => {}), /no event stream/);
  await assert.rejects(consumeSse(new Response(''), () => {}, 0), /Invalid SSE event limit/);
});
