import { test, expect } from '@playwright/test';
import { readFile } from 'node:fs/promises';
import path from 'node:path';

test('real browser can read a day and retrieve its cited location', async ({ page }) => {
  await page.goto('/health');
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  const result = await page.evaluate(async token => {
    const days = await (await fetch('/days')).json();
    const day = await (await fetch('/day/2026-10-20')).json();
    const response = await fetch('/ask', { method: 'POST', headers: {
      Authorization: `Bearer ${token}`, 'Content-Type': 'application/json',
    }, body: JSON.stringify({ date: '2026-10-20', question: 'Where was I when we decided the demo order?' }) });
    return { days, day, answer: await response.json(), status: response.status };
  }, token);
  expect(result.status).toBe(200);
  expect(result.days[0]).toMatchObject({ date: '2026-10-22', capture: true });
  expect(result.days.some((day: { date: string }) => day.date === '2026-10-20')).toBe(true);
  expect(result.day.layers).toHaveLength(5);
  expect(result.day.layers[0].t0).toBe(28800);
  expect(result.answer.layer_index).toBe(3);
  expect(result.answer.t).toBe(45605);
  expect(result.answer.sentences[0].evidence).toContain('ev_quote_0');
});

test('real browser receives no-match and unauthorized responses correctly', async ({ page }) => {
  await page.goto('/health');
  const token = (await readFile(path.resolve('.daycube-e2e-vault/.token'), 'utf8')).trim();
  const result = await page.evaluate(async token => {
    const query = { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: '2026-10-20', question: 'unicornpancakequantum' }) };
    const unauthorized = await fetch('/ask', query);
    const empty = await fetch('/ask', { ...query, headers: { ...query.headers, Authorization: `Bearer ${token}` } });
    return { unauthorized: unauthorized.status, empty: await empty.json() };
  }, token);
  expect(result.unauthorized).toBe(401);
  expect(result.empty.evidence).toEqual([]);
  expect(result.empty.layer_index).toBeNull();
});
