import { test, expect } from '@playwright/test';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

// The Training page (components/training/TrainingPage.tsx) over a run written into the end-to-end
// vault as ingest/training.py writes one: run.json (the training contract) and metrics.jsonl.

const id = 'splat-20261103T120000-000001';
const folder = path.resolve(`.daycube-e2e-vault/training/${id}`);

test.beforeAll(async () => {
  await mkdir(folder, { recursive: true });
  const run = {
    schema: 'daycube.training/1', id, model: 'splat', title: '3D of mo_scan_001, 2026-10-20',
    started: '2026-11-03T12:00:00+00:00', ended: '2026-11-03T12:36:00+00:00', status: 'done', steps_total: 9000,
    metrics: { psnr_db: { unit: 'dB', better: 'higher' }, splats: { unit: 'splats', better: null } },
    device: 'GPU (Brush 0.3.0)', source: '2026-10-20/mo_scan_001', result: { psnr_db: 22.5, minutes: 36 }, error: null,
  };
  await writeFile(path.join(folder, 'run.json'), JSON.stringify(run));
  const lines = [
    { step: 500, splats: 40000 }, { step: 3000, psnr_db: 18.25, splats: 90000 },
    { step: 6000, psnr_db: 21.0, splats: 120000 }, { step: 9000, psnr_db: 22.5, splats: 130000 },
  ];
  await writeFile(path.join(folder, 'metrics.jsonl'), lines.map((l) => JSON.stringify({ t: '2026-11-03T12:10:00+00:00', ...l })).join('\n') + '\n');
});

test.afterAll(async () => {
  await rm(folder, { recursive: true, force: true });
});

test('training runs are listed and the one open is charted per metric', async ({ page }) => {
  await page.goto('/');
  await page.getByRole('button', { name: 'Training', exact: true }).click();
  await expect(page).toHaveURL(/view=training/);
  const list = page.getByRole('navigation', { name: 'Training runs' });
  const item = list.getByRole('button', { name: /3D of mo_scan_001/ });
  await expect(item).toContainText('done');
  await expect(item).toContainText('22.5 dB');
  // The newest run opens by itself; each metric gets its chart, skipping steps it was not measured at.
  const detail = page.getByRole('region', { name: '3D of mo_scan_001, 2026-10-20' });
  await expect(detail).toContainText('9,000 / 9,000 steps');
  await expect(detail.getByRole('figure', { name: 'psnr_db: 22.5 dB at step 9000' })).toBeVisible();
  await expect(detail.getByRole('figure', { name: 'splats: 130,000 splats at step 9000' })).toBeVisible();
  await expect(detail.locator('.training__result')).toContainText('minutes');
});
