import { expect, test } from '@playwright/test';

test('streamed search delivers location before the answer stream closes', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await page.evaluate(async modulePath => {
    const { BackendBridge } = await import(modulePath);
    const bridge = new BackendBridge();
    bridge.setToken('synthetic-test-token');
    const encoder = new TextEncoder();
    let controller: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
    const original = globalThis.fetch;
    globalThis.fetch = async (input, init) => String(input).includes('/api/ask?stream=1')
      ? new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
      : original(input, init);
    const state = window as any;
    state.locationEvents = [];
    state.streamAnswer = bridge.ask('2026-10-20', 'synthetic query', [], true,
      (layer: number, hours: number) => state.locationEvents.push({ layer, hours }));
    state.finishAnswer = () => {
      const answer = { mode: 'extractive', question: 'synthetic query', day: '2026-10-20',
        layer_index: 0, t: 3600, floor: 0, x: null, y: null, moment_id: null, offset_s: null,
        sentences: [], evidence: [], message: null };
      controller.enqueue(encoder.encode(`event: answer\ndata: ${JSON.stringify(answer)}\n\nevent: done\ndata: {}\n\n`));
      controller.close();
    };
    controller!.enqueue(encoder.encode('event: location\ndata: {"layer_index":0,"t":3600}\n\n'));
  }, '/src/lib/backendBridge.ts');
  // EOF is deliberately withheld. Buffering the full response makes this assertion fail.
  await expect.poll(() => page.evaluate(() => (window as any).locationEvents), { timeout: 2000 })
    .toEqual([{ layer: 0, hours: 1 }]);
  const answer = await page.evaluate(async () => {
    (window as any).finishAnswer();
    return (window as any).streamAnswer;
  });
  expect(answer.hasLocation).toBe(true);
  expect(answer.t).toBe(1);
  expect(answer.evidence).toEqual([]);
});

for (const failure of ['unauthorized', 'invalid answer']) {
  test(`a streamed ${failure} fails explicitly without a duplicate search`, async ({ page }) => {
    await page.goto('/?date=2026-10-20');
    const result = await page.evaluate(async ({ modulePath, failure }) => {
      const { BackendBridge } = await import(modulePath);
      const bridge = new BackendBridge();
      bridge.setToken('synthetic-test-token');
      const original = globalThis.fetch;
      let requests = 0;
      globalThis.fetch = async (input, init) => {
        if (!String(input).includes('/api/ask')) return original(input, init);
        requests++;
        return failure === 'unauthorized'
          ? new Response('{"detail":"Unauthorized"}', { status: 401, headers: { 'Content-Type': 'application/json' } })
          : new Response('event: answer\ndata: {"evidence":[]}\n\nevent: done\ndata: {}\n\n',
            { headers: { 'Content-Type': 'text/event-stream' } });
      };
      try {
        await bridge.ask('2026-10-20', 'synthetic query', [], true);
        return { failed: false, requests };
      } catch {
        return { failed: true, requests };
      }
    }, { modulePath: '/src/lib/backendBridge.ts', failure });
    expect(result).toEqual({ failed: true, requests: 1 });
  });
}
