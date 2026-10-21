import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

test('binding a vault token authorizes a real reindex through the frontend bridge', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'API Contract', exact: true }).click();
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  await page.getByPlaceholder('Paste vault/.token bearer secret...').fill(token);
  await page.getByRole('button', { name: 'Bind Token', exact: true }).click();
  const response = page.waitForResponse(response => response.url().endsWith('/api/ingest/2026-10-20') && response.request().method() === 'POST');
  await page.getByRole('button', { name: /POST \/api\/ingest\/2026-10-20/ }).click();
  expect((await response).status()).toBe(202);
  await expect(page.getByRole('button', { name: /POST \/api\/ingest\/2026-10-20/ })).toContainText('complete');
});

test('live search and empty results stay separate from prototype evidence', async ({ page }) => {
  await page.goto('/');
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  const result = await page.evaluate(async ({ token, modulePath }) => {
    const { BackendBridge } = await import(modulePath);
    const bridge = new BackendBridge();
    bridge.setToken(token);
    const day = await bridge.loadDayLayers('2026-10-20');
    const answer = await bridge.ask('2026-10-20', 'Where was I when we decided the demo order?', day.layers, false);
    const empty = await bridge.ask('2026-10-20', 'unicornpancakequantum', day.layers, true);
    return { synced: day.fromBackend, layers: day.layers, answer, empty };
  }, { token, modulePath: '/src/lib/backendBridge.ts' });
  expect(result.synced).toBe(true);
  expect(result.layers[0].moments).toEqual([]);
  expect(result.layers[0].quotes).toEqual([]);
  expect(result.answer.mode).toBe('extractive');
  expect(result.answer.layerIndex).toBe(3);
  expect(result.answer.sentences[0].evidence).toContain('ev_quote_0');
  expect(result.empty.hasLocation).toBe(false);
  expect(result.empty.sentences).toEqual([]);
  expect(result.empty.evidence).toEqual([]);
});
