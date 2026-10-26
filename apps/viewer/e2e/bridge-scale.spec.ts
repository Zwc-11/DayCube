import { expect, test } from '@playwright/test';

test('a long path with unavailable positions keeps the day available and preserves confidence', async ({ page }) => {
  const response = await page.request.get('/api/day/2026-10-20');
  expect(response.ok()).toBe(true);
  const day = await response.json();
  const layer = day.layers[0];
  const count = 200000;
  layer.path = Array.from({ length: count }, (_, i) => ({
    t: layer.t0 + (layer.t1 - layer.t0) * i / (count - 1), x: null, y: null, floor: null,
    pos_conf: i === count - 1 ? .125 : .75, fidelity: 'estimated',
  }));
  await page.route('**/api/day/2026-10-20', route => route.fulfill({ json: day }));
  await page.goto('/');
  const result = await page.evaluate(async modulePath => {
    const { backendBridge } = await import(modulePath);
    const loaded = await backendBridge.fetchDay('2026-10-20');
    return loaded.status === 'ok' ? { status: loaded.status, confidence: loaded.layers[0].path[0].conf,
      fidelity: loaded.layers[0].path[0].fidelity } : { status: loaded.status };
  }, '/src/lib/backendBridge.ts');
  expect(result).toEqual({ status: 'ok', confidence: .125, fidelity: 'illustrative' });
});
