import { test, expect, type Page } from '@playwright/test';

async function countRendering(page: Page) {
  await page.addInitScript(() => {
    const counters = { gpu: 0, canvas2d: 0 };
    (window as any).__renderCounters = counters;
    for (const context of [WebGLRenderingContext, WebGL2RenderingContext]) {
      for (const method of ['drawElements', 'drawArrays', 'drawElementsInstanced', 'drawArraysInstanced']) {
        const prototype = context.prototype as any;
        const original = prototype[method];
        if (original) prototype[method] = function (...args: any[]) {
          counters.gpu++;
          return original.apply(this, args);
        };
      }
    }
    const drawImage = CanvasRenderingContext2D.prototype.drawImage;
    (CanvasRenderingContext2D.prototype as any).drawImage = function (...args: any[]) {
      counters.canvas2d++;
      return (drawImage as any).apply(this, args);
    };
  });
}

async function reset(page: Page) {
  await page.evaluate(() => {
    (window as any).__renderCounters.gpu = 0;
    (window as any).__renderCounters.canvas2d = 0;
  });
}

test('the 3D view rests when idle and still responds to orbiting', async ({ page }) => {
  await countRendering(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/');
  await expect(page.locator('canvas')).toBeVisible();
  await page.waitForTimeout(2200);
  const nameplate = await page.getByText('L4', { exact: true }).first().boundingBox();
  expect(nameplate).not.toBeNull();
  expect(nameplate!.width).toBeLessThan(120);
  expect(nameplate!.height).toBeLessThan(80);
  await reset(page);
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => (window as any).__renderCounters.gpu)).toBe(0);
  await page.mouse.move(760, 420);
  await page.mouse.down();
  await page.mouse.move(850, 470, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(300);
  expect(await page.evaluate(() => (window as any).__renderCounters.gpu)).toBeGreaterThan(0);
  expect(errors).toEqual([]);
  await page.screenshot({ path: 'test-results/performance-after.png' });
});

test('playback renders but pauses behind a modal', async ({ page }) => {
  await countRendering(page);
  await page.goto('/');
  await page.waitForTimeout(1800);
  await reset(page);
  await page.getByRole('button', { name: 'Play day playback', exact: true }).click();
  await page.waitForTimeout(400);
  expect(await page.evaluate(() => (window as any).__renderCounters.gpu)).toBeGreaterThan(0);
  await page.getByRole('button', { name: 'API Contract', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Bind Token', exact: true })).toBeVisible();
  await page.waitForTimeout(200);
  await reset(page);
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => (window as any).__renderCounters.gpu)).toBe(0);
});

test('the 2D fallback rests when idle and redraws when scrubbing', async ({ page }) => {
  await countRendering(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Canvas 2D Ref', exact: true }).click();
  await page.waitForTimeout(800);
  await reset(page);
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => (window as any).__renderCounters.canvas2d)).toBe(0);
  await page.getByRole('slider').fill('16');
  await page.waitForTimeout(200);
  expect(await page.evaluate(() => (window as any).__renderCounters.canvas2d)).toBeGreaterThan(0);
});
