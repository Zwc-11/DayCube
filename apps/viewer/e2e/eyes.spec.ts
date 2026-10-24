import { test, expect } from '@playwright/test';
import { mkdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';

const folder = path.resolve('.daycube-e2e-vault/calibration');
const calibrated = '2026-10-24T15:00:00+00:00';
const t0 = Date.parse(calibrated) / 1000;

/** A calibration, 120 clicks (the first 60 with it alone, then with update 1) and one fit. */
async function seed(): Promise<void> {
  await mkdir(folder, { recursive: true });
  await writeFile(path.join(folder, 'gaze.json'), JSON.stringify({ t: calibrated, model: 'head', monitor: [2560, 1440], error_px: 85, mm_per_px: 0.233 }));
  const X = [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 600, ...new Array(14).fill(0)];
  const clicks = Array.from({ length: 120 }, (_, k) => {
    const version = k < 60 ? 0 : 1;
    const base = 140 + (k % 7) * 4;
    return { t: t0 + 20 * k, x: 1000, y: 600, X, error_px: version ? base * 0.7 : base, base_px: base, v: version, used: k % 10 !== 0 };
  });
  await writeFile(path.join(folder, 'clicks.jsonl'), clicks.map((c) => JSON.stringify(c)).join('\n') + '\n');
  const fit = { event: 'fit', t: new Date((t0 + 20 * 59) * 1000).toISOString(), version: 1, accepted: true, clicks: 54,
    held_out: 11, penalty: 10, head_terms: false, error_px: { calibration: 150, in_use: 150, learned: 104, fixed_guess: 600 } };
  const applied = { event: 'applied', t: new Date((t0 + 20 * 59 + 0.04) * 1000).toISOString(), version: 1, after_ms: 33.4 };
  await writeFile(path.join(folder, 'learning.jsonl'), `${JSON.stringify(fit)}\n${JSON.stringify(applied)}\n`);
  await writeFile(path.join(folder, 'gaze-learned.json'), JSON.stringify({ calibration: calibrated, version: 1, t: fit.t, fit, pose_ref: null }));
}

test('the eye-tracking dashboard shows the live error, the loss ratio and each update', async ({ page }) => {
  await seed();
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'Your data' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Eye tracking', exact: true }).click();
    await expect(page.getByTestId('eye-tracking')).toBeVisible();
    // The newest 300 clicks: half with the calibration (ratio 1), half with update 1 (ratio 0.7).
    await expect(page.getByTestId('loss-ratio')).toHaveText(/^0\.\d\d$/);
    await expect(page.getByTestId('model-version')).toHaveText('Update 1');
    await expect(page.getByText('in the gaze 33 ms after fitting')).toBeVisible();
    const chart = page.getByTestId('error-chart');
    await expect(chart).toHaveAttribute('aria-label', /over 120 clicks/);
    await chart.hover({ position: { x: 700, y: 120 } });
    await expect(page.getByText(/Click \d+ · .* · model v1/)).toBeVisible();
    await page.getByRole('button', { name: 'Show table' }).click();
    await expect(page.getByTestId('fit-table')).toContainText('v1, live in 33 ms');
    // The next poll finds nothing new: an empty 304, not the whole report again.
    const poll = await page.waitForResponse((r) => r.url().includes('/gaze/learning'), { timeout: 8000 });
    expect(poll.status()).toBe(304);
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
});
