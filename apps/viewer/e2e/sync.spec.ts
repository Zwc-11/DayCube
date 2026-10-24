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

test('asking and rebuilding use the open day rather than the bundled sample date', async ({ page }) => {
  await page.route('**/api/day/2026-10-21', async route => {
    const response = await route.fetch({ url: route.request().url().replace('2026-10-21', '2026-10-20') });
    await route.fulfill({ response, body: (await response.text()).replaceAll('2026-10-20', '2026-10-21') });
  });
  await page.route('**/api/ingest/2026-10-21', route => route.fulfill({ json: { job_id: 'synthetic-date-job' } }));
  await page.route('**/api/jobs/synthetic-date-job', route => route.fulfill({ json: { status: 'complete' } }));
  await page.goto('/?date=2026-10-21');
  await expect(page.locator('canvas')).toBeVisible();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Vault connection', exact: true }).click();
  await page.getByPlaceholder('Paste the contents of vault/.token').fill('synthetic-date-token');
  await page.getByRole('button', { name: 'Connect', exact: true }).click();
  const reindex = page.waitForRequest(request => request.url().endsWith('/api/ingest/2026-10-21'));
  await page.getByRole('button', { name: 'Rebuild search index', exact: true }).click();
  await reindex;
  await expect(page.getByTestId('index-status')).toContainText('complete');
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await page.route('**/api/ask?stream=1', route => route.abort());
  await page.keyboard.press('/');
  await page.getByRole('textbox', { name: 'Question', exact: true }).fill('What happened today?');
  const ask = page.waitForRequest(request => request.url().endsWith('/api/ask?stream=1'));
  await page.getByRole('textbox', { name: 'Question', exact: true }).press('Enter');
  expect((await ask).postDataJSON().date).toBe('2026-10-21');
});

test('failed live name writes remain failures and leave the displayed place unchanged', async ({ page }) => {
  await page.route('**/api/places/rename', route => route.fulfill({ status: 500, json: { detail: 'Synthetic write failure' } }));
  await page.route('**/api/speakers/confirm', route => route.fulfill({ status: 409, json: { detail: 'Synthetic conflict' } }));
  await page.goto('/?date=2026-10-20');
  await expect(page.locator('canvas')).toBeVisible();
  const result = await page.evaluate(async modulePath => {
    const { backendBridge: bridge } = await import(modulePath);
    bridge.setToken('synthetic-write-token');
    const logs: { status: number; mode: string }[] = [];
    const stop = bridge.onLog((log: { status: number; mode: string }) => logs.push({ status: log.status, mode: log.mode }));
    const outcomes = await Promise.allSettled([
      bridge.renamePlace('home2', 'Unsaved name'),
      bridge.confirmSpeaker('2026-10-20', 'mo_001', 'SPEAKER_00', 'person:test'),
    ]);
    stop();
    return { outcomes: outcomes.map(r => r.status), logs };
  }, '/src/lib/backendBridge.ts');
  expect(result.outcomes).toEqual(['rejected', 'rejected']);
  expect(result.logs).toEqual(expect.arrayContaining([
    { status: 500, mode: 'live-backend' }, { status: 409, mode: 'live-backend' },
  ]));
  const heading = page.locator('.insp-title');
  const name = await heading.innerText();
  await page.getByRole('button', { name: 'Rename place', exact: true }).click();
  await page.getByRole('textbox', { name: 'Place name', exact: true }).fill('Unsaved name');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Synthetic write failure');
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(heading).toHaveText(name);
});

test('unsupported deletion does not hide evidence or create a success audit', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await expect(page.locator('canvas')).toBeVisible();
  const before = await page.locator('.track__tick').count();
  expect(before).toBeGreaterThan(0);
  const rejected = await page.evaluate(async () => {
    const response = await fetch('/api/delete-hours', { method: 'POST', body: '{}' });
    return { status: response.status, body: await response.json() };
  });
  expect(rejected.status).toBe(501);
  expect(rejected.body.detail).toContain('No records were deleted');
  let requests = 0;
  await page.route('**/api/delete-hours', route => { requests++; return route.abort(); });
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByLabel('From', { exact: true }).fill('10:00');
  await page.getByRole('button', { name: /^Delete \d/ }).click();
  await page.getByRole('button', { name: 'Delete', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('No records were deleted');
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await expect(page.getByText('Nothing has been deleted from this day.')).toBeVisible();
  expect(requests).toBe(0);
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  expect(await page.locator('.track__tick').count()).toBe(before);
});
