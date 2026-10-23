// Reporter lifecycle checks in a real DOM with a synthetic transport. No receiver or user vault.
import { fileURLToPath } from 'node:url';
import { expect, test } from '@playwright/test';

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
