import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

// Settings › Privacy and capture › Delete a time range, against the test vault's API. The span is
// one the sample day holds nothing in (other tests read this vault); what a deletion removes is
// tested in services/ingest/tests/test_forget.py.
test('a time range deleted in Privacy is removed by the API and listed', async ({ page }) => {
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  await page.addInitScript(
    `Object.defineProperty(window, '__DAYCUBE_DESKTOP__', { value: Object.freeze({ token: ${JSON.stringify(token)}, version: 'test' }) });`,
  );
  await page.goto('/?date=2026-10-21');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Privacy and capture' }).click();
  const settings = page.getByRole('dialog');
  await settings.getByLabel('From', { exact: true }).fill('21:00');
  await settings.getByLabel('To', { exact: true }).fill('21:15');
  await settings.getByLabel('Reason (optional)').fill('e2e check');
  const deleted = page.waitForResponse((r) => r.url().includes('/api/day/2026-10-21/hours') && r.request().method() === 'DELETE');
  await settings.getByRole('button', { name: /^Delete 21:00/ }).click();
  await settings.getByRole('button', { name: 'Delete', exact: true }).click();
  expect((await deleted).status()).toBe(200);
  const row = settings.getByRole('row', { name: /e2e check/ });
  await expect(row).toContainText('21:00');
  const listed = await page.evaluate(() => fetch('/api/day/2026-10-21/deletions').then((r) => r.json()));
  expect(listed.some((d: { reason: string }) => d.reason === 'e2e check')).toBe(true);
});
