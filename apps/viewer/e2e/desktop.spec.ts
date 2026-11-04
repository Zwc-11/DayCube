import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

// The desktop app (apps/desktop) sets window.__DAYCUBE_DESKTOP__ before the page's scripts run, as
// this init script does, so its window needs no pasting in Settings › Vault connection.
const inject = (token: string) =>
  `Object.defineProperty(window, '__DAYCUBE_DESKTOP__', { value: Object.freeze({ token: ${JSON.stringify(token)}, version: 'test' }) });`;

const bridgeToken = (page: import('@playwright/test').Page) =>
  page.evaluate(async (modulePath) => (await import(modulePath)).backendBridge.getToken(), '/src/lib/backendBridge.ts');

test('the app window starts with the vault token bound and authorized actions work', async ({ page }) => {
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  await page.addInitScript(inject(` ${token}\n`));
  await page.goto('/?date=2026-10-20');
  expect(await bridgeToken(page)).toBe(token); // trimmed, and the app's own bridge instance
  // An action that needs the token, through the app's bridge with no setToken call.
  const answer = await page.evaluate(
    async ({ modulePath }) => {
      const { backendBridge } = await import(modulePath);
      return (backendBridge as any).client.ask('2026-10-20', 'Where was I when we decided the demo order?');
    },
    { modulePath: '/src/lib/backendBridge.ts' },
  );
  expect(answer.moment_id).toBe('mo_001');
});

test('in a browser there is no injected token', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  expect(await bridgeToken(page)).toBe('');
});
