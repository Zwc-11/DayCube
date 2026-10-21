import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

test('binding a vault token authorizes a real reindex through the frontend bridge', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Vault connection', exact: true }).click();
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  await page.getByPlaceholder('Paste the contents of vault/.token').fill(token);
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const response = page.waitForResponse(response => response.url().endsWith('/api/ingest/2026-10-20') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Rebuild search index', exact: true }).click();
  expect((await response).status()).toBe(202);
  await expect(page.getByTestId('index-status')).toContainText('complete');
});

test('live search and empty results stay separate from prototype evidence', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  const result = await page.evaluate(async ({ token, modulePath }) => {
    const { BackendBridge } = await import(modulePath);
    const bridge = new BackendBridge();
    bridge.setToken(token);
    const day = await bridge.fetchDay('2026-10-20');
    if (day.status !== 'ok') return { synced: false };
    const again = await bridge.fetchDay('2026-10-20', day.etag);
    const answer = await bridge.ask('2026-10-20', 'Where was I when we decided the demo order?', day.layers, false);
    const empty = await bridge.ask('2026-10-20', 'unicornpancakequantum', day.layers, true);
    return { synced: true, revalidated: again.status, layers: day.layers, answer, empty };
  }, { token, modulePath: '/src/lib/backendBridge.ts' });
  expect(result.synced).toBe(true);
  expect(result.revalidated).toBe('unchanged'); // the ETag round trip answers 304
  expect(result.layers[0].moments).toEqual([]);
  expect(result.layers[0].quotes).toEqual([]);
  expect(result.answer.mode).toBe('extractive');
  expect(result.answer.layerIndex).toBe(3);
  expect(result.answer.sentences[0].evidence).toContain('ev_quote_0');
  expect(result.empty.hasLocation).toBe(false);
  expect(result.empty.sentences).toEqual([]);
  expect(result.empty.evidence).toEqual([]);
});

test('the calendar lists recorded days and opens them', async ({ page }) => {
  await page.goto('/');
  const day = page.getByRole('button', { name: /Tuesday, October 20/ });
  await expect(day).toBeVisible();
  await expect(page.locator('.cal__day--empty').first()).toBeVisible();
  await day.click();
  await expect(page).toHaveURL(/\?date=2026-10-20$/);
  await expect(page.locator('canvas')).toBeVisible();
  await page.getByRole('button', { name: 'All days', exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await page.goBack();
  await expect(page.locator('canvas')).toBeVisible();
});
