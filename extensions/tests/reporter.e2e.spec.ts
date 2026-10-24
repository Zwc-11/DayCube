// Reporter lifecycle checks in a real DOM with a synthetic transport. No receiver or user vault.
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { expect, test, type Page } from '@playwright/test';

const REPORTER = fileURLToPath(new URL('../chrome/content.js', import.meta.url));

for (const event of ['focus', 'visibilitychange']) {
  test(`returning via ${event} republishes unchanged block positions`, async ({ page }) => {
    await page.setContent('<!doctype html><p>A short synthetic paragraph for the reporter lifecycle test.</p>');
    await page.evaluate(() => {
      const messages: unknown[] = [];
      Object.assign(window, { reporterMessages: messages });
      Object.defineProperty(window, 'chrome', { value: { runtime: {
        id: 'reporter-test',
        sendMessage: async (message: unknown) => {
          messages.push(structuredClone(message));
          return { ok: true, epoch: 0 };
        },
        onMessage: { addListener: () => {} },
      } } });
    });
    await page.addScriptTag({ path: REPORTER });
    const snapshots = () => page.evaluate(() => (window as any).reporterMessages.filter((m: any) => m.kind === 'blocks'));
    await expect.poll(async () => (await snapshots()).length).toBe(1);
    const [before] = await snapshots();

    // The receiver clears its front page on focus loss. A heartbeat alone has no blocks.
    await page.evaluate(event => {
      if (event === 'focus') window.dispatchEvent(new FocusEvent('focus'));
      else document.dispatchEvent(new Event('visibilitychange'));
    }, event);
    await expect.poll(async () => (await snapshots()).length, { timeout: 2000 }).toBe(2);
    const [, after] = await snapshots();
    expect(after.doc).toBe(before.doc);
    expect(after.blocks.map(({ id, rect }: any) => ({ id, rect })))
      .toEqual(before.blocks.map(({ id, rect }: any) => ({ id, rect })));
    expect(after.blocks.every((block: any) => block.text === undefined)).toBe(true);
  });
}

test('text caches stay bounded and an evicted block returns with its original identity', async ({ page }) => {
  await page.setContent('<!doctype html><p>A synthetic paragraph retained for a later revisit.</p>');
  await page.evaluate(() => {
    Object.assign(window, { firstParagraph: document.querySelector('p'), latestBlocks: null });
    Object.defineProperty(window, 'chrome', { value: { runtime: {
      id: 'reporter-test', sendMessage: async (message: any) => {
        if (message.kind === 'blocks') (window as any).latestBlocks = structuredClone(message.blocks);
        return { ok: true, epoch: 0 };
      }, onMessage: { addListener: () => {} },
    } } });
  });
  // Small capacity makes eviction deterministic. Expose counters only in this injected test copy.
  const source = (await readFile(REPORTER, 'utf8'))
    .replace('const MAX_CACHED_BLOCKS = 5000;', 'const MAX_CACHED_BLOCKS = 4;')
    .replace('  schedule(600);', 'window.reviewReporter = {snapshot, sizes: () => [elements.size, sent.size, sentLines.size, measured.size]};');
  await page.addScriptTag({ content: source });
  const result = await page.evaluate(async () => {
    const w = window as any;
    w.reviewReporter.snapshot();
    await Promise.resolve();
    const before = w.latestBlocks[0];
    for (let i = 0; i < 12; i++) {
      document.body.innerHTML = `<p>Another synthetic paragraph ${i}.</p>`;
      w.reviewReporter.snapshot();
      await Promise.resolve();
      if (w.reviewReporter.sizes().some((n: number) => n > 4)) throw new Error('Cache capacity exceeded');
    }
    document.body.replaceChildren(w.firstParagraph);
    w.reviewReporter.snapshot();
    await Promise.resolve();
    return {before, after: w.latestBlocks[0], sizes: w.reviewReporter.sizes()};
  });
  expect(result.after.id).toBe(result.before.id);
  expect(result.after.rect).toEqual(result.before.rect);
  expect(result.after.text).toBe(result.before.text);
  expect(result.after.lines).toEqual(result.before.lines);
  expect(result.sizes.every((size: number) => size <= 4)).toBe(true);
});

async function backgroundHarness(page: Page, mode: 'hung' | 'malformed') {
  await page.clock.install();
  await page.setContent('<!doctype html><title>Synthetic worker transport</title>');
  await page.evaluate(mode => {
    const noop = () => {};
    const event = () => ({ addListener: noop });
    const state: Record<string, any> = { enabled: true, token: 'synthetic-test', paused: false, queue: [] };
    const w = window as any;
    w.workerState = state;
    w.workerMode = mode;
    w.sentBatches = [];
    w.fetch = async (_url: string, init: RequestInit) => {
      w.sentBatches.push(JSON.parse(String(init.body)));
      if (w.workerMode === 'hung') return new Promise((_, reject) => {
        init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true });
      });
      if (w.workerMode === 'malformed') return new Response('not JSON');
      return Response.json({ accepted: 1, rejected: [], signals: [] });
    };
    Object.defineProperty(window, 'chrome', { value: {
      storage: { local: {
        get: async (keys: any) => {
          if (typeof keys === 'string') return { [keys]: state[keys] };
          if (Array.isArray(keys)) return Object.fromEntries(keys.map(k => [k, state[k]]));
          return { ...keys, ...state };
        },
        set: async (patch: any) => Object.assign(state, structuredClone(patch)),
      }, onChanged: event() },
      action: { onClicked: event(), setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {}, setTitle: async () => {} },
      commands: { onCommand: event() },
      alarms: { get: async () => ({}), create: noop, onAlarm: event() },
      webNavigation: { onCommitted: event(), onHistoryStateUpdated: event() },
      tabs: { onUpdated: event(), onRemoved: event(), onActivated: event() },
      windows: { onFocusChanged: event() },
      idle: { setDetectionInterval: noop, onStateChanged: event() },
      runtime: { id: 'synthetic-worker', onMessage: event(), onInstalled: event(), onStartup: event() },
    } });
  }, mode);
  const library = (await readFile(fileURLToPath(new URL('../chrome/lib.js', import.meta.url)), 'utf8')).replace(/\bexport /g, '');
  const worker = (await readFile(fileURLToPath(new URL('../chrome/background.js', import.meta.url)), 'utf8')).replace(/^import .*;\r?\n/m, '');
  await page.addScriptTag({ content: `(() => {${library}\n${worker}\nwindow.reviewWorker = {enqueue, flush};})();` });
}

for (const mode of ['hung', 'malformed'] as const) {
  test(`a ${mode} delivery keeps durable records and retries once acknowledged`, async ({ page }) => {
    await backgroundHarness(page, mode);
    const shared = await page.evaluate(() => {
      const w = window as any;
      w.reviewWorker.enqueue({ id: 'synthetic-record', kind: 'page', t: '2026-07-01T08:00:00Z' });
      w.firstDelivery = w.reviewWorker.flush();
      return w.firstDelivery === w.reviewWorker.flush();
    });
    expect(shared).toBe(true);
    await expect.poll(() => page.evaluate(() => (window as any).sentBatches.length)).toBe(1);
    await expect.poll(() => page.evaluate(() => (window as any).workerState.queue.length)).toBe(1);
    if (mode === 'hung') await page.clock.fastForward(5001);
    await page.evaluate(() => (window as any).firstDelivery);
    expect(await page.evaluate(() => (window as any).workerState.queue[0].id)).toBe('synthetic-record');
    await page.evaluate(async () => {
      const w = window as any;
      w.workerMode = 'ok';
      await w.reviewWorker.flush();
    });
    expect(await page.evaluate(() => (window as any).workerState.queue.length)).toBe(0);
    expect(await page.evaluate(() => (window as any).sentBatches.map((b: any[]) => b.map(r => r.id))))
      .toEqual([['synthetic-record'], ['synthetic-record']]);
  });
}
