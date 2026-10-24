import { test, expect, type Page } from '@playwright/test';

async function countRendering(page: Page) {
  await page.addInitScript(() => {
    const counters = { gpu: 0, canvas2d: 0, linkProgram: 0, bufferData: 0 };
    (window as any).__renderCounters = counters;
    const counted: Record<string, keyof typeof counters> = {
      drawElements: 'gpu',
      drawArrays: 'gpu',
      drawElementsInstanced: 'gpu',
      drawArraysInstanced: 'gpu',
      linkProgram: 'linkProgram',
      bufferData: 'bufferData',
    };
    for (const context of [WebGLRenderingContext, WebGL2RenderingContext]) {
      for (const [method, counter] of Object.entries(counted)) {
        const prototype = context.prototype as any;
        const original = prototype[method];
        if (original) prototype[method] = function (...args: any[]) {
          counters[counter]++;
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
    const counters = (window as any).__renderCounters;
    for (const key of Object.keys(counters)) counters[key] = 0;
  });
}

const counters = (page: Page) => page.evaluate(() => ({ ...(window as any).__renderCounters }));

test('the 3D view rests when idle and still responds to orbiting', async ({ page }) => {
  await countRendering(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto('/?date=2026-10-20');
  await expect(page.locator('canvas')).toBeVisible();
  await page.waitForTimeout(2200);
  // Scene labels are DOM overlays; a distance factor on the orthographic camera once scaled
  // them several hundred times. Every pin and label must stay label-sized.
  const labels = await page.locator('.pin, .scene-label, .bubble').evaluateAll(els =>
    els.map(el => el.getBoundingClientRect()).map(r => ({ width: r.width, height: r.height })));
  expect(labels.length).toBeGreaterThan(0);
  for (const box of labels) {
    expect(box.width).toBeLessThan(320);
    expect(box.height).toBeLessThan(200);
  }
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
  await page.goto('/?date=2026-10-20');
  await page.waitForTimeout(1800);
  await reset(page);
  await page.getByRole('button', { name: 'Play the day', exact: true }).click();
  await page.waitForTimeout(400);
  expect(await page.evaluate(() => (window as any).__renderCounters.gpu)).toBeGreaterThan(0);
  // Advancing the playhead moves uniforms and draw ranges only. A shader link or buffer
  // allocation per tick means a material or geometry is being rebuilt from changed props.
  await reset(page);
  await page.waitForTimeout(800);
  const playing = await counters(page);
  expect(playing.gpu).toBeGreaterThan(0);
  expect(playing.linkProgram).toBe(0);
  expect(playing.bufferData).toBe(0);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.waitForTimeout(200);
  await reset(page);
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => (window as any).__renderCounters.gpu)).toBe(0);
});

test('the scene stays within its draw-call budget', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  await expect(page.locator('canvas')).toBeVisible();
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Performance', exact: true }).click();
  const drawCalls = page.locator('.stat', { hasText: 'Draw calls' }).locator('.stat__value');
  await expect(drawCalls).toHaveText(/^\d+$/);
  expect(Number(await drawCalls.textContent())).toBeLessThanOrEqual(8);
});

test('the 2D fallback rests when idle and redraws when scrubbing', async ({ page }) => {
  await countRendering(page);
  await page.goto('/?date=2026-10-20');
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await page.getByRole('button', { name: 'Performance', exact: true }).click();
  await page.getByRole('button', { name: '2D', exact: true }).click();
  await page.getByRole('button', { name: 'Close settings', exact: true }).click();
  await page.waitForTimeout(800);
  await reset(page);
  await page.waitForTimeout(600);
  expect(await page.evaluate(() => (window as any).__renderCounters.canvas2d)).toBe(0);
  await page.getByRole('slider', { name: 'Time of day' }).press('PageUp');
  await page.waitForTimeout(200);
  expect(await page.evaluate(() => (window as any).__renderCounters.canvas2d)).toBeGreaterThan(0);
});

test('a failed-write dialog suspends playback rendering until dismissed', async ({ page }) => {
  await countRendering(page);
  await page.goto('/?date=2026-10-20');
  await expect(page.locator('canvas')).toBeVisible();
  await page.waitForTimeout(1800);
  await page.getByRole('button', { name: 'Play the day', exact: true }).click();
  // No token: the write fails before transport and the place remains unchanged.
  await page.getByRole('button', { name: 'Rename place', exact: true }).click();
  await page.getByRole('textbox', { name: 'Place name', exact: true }).fill('Unsaved name');
  await page.getByRole('button', { name: 'Save name', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('vault token');
  await page.waitForTimeout(300);
  await reset(page);
  await page.waitForTimeout(600);
  expect((await counters(page)).gpu).toBe(0);
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await page.waitForTimeout(300);
  expect((await counters(page)).gpu).toBeGreaterThan(0);
});
