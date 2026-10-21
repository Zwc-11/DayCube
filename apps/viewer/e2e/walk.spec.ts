import { test, expect } from '@playwright/test';

test('the walker routes around furniture, climbs the stairs and slows playback while walking', async ({ page }) => {
  await page.goto('/?date=2026-10-20');
  const result = await page.evaluate(async () => {
    const { SAMPLE_DAY_LAYERS } = await import('/src/data/sampleDay.ts');
    const { routeFor, daySchedule, advancePlayhead } = await import('/src/lib/walkRoute.ts');
    const collisions: number[] = [];
    let climb: number[] = [];
    for (const layer of SAMPLE_DAY_LAYERS) {
      const r = routeFor(layer);
      for (let k = 0; k < r.n; k++) {
        if (r.stair[k]) continue;
        const blocked = layer.boxes.some(
          (b: any) =>
            !b.opts?.stair &&
            b.z < r.z[k] + 2 &&
            b.z + b.h > r.z[k] + 0.35 &&
            r.x[k] > b.x - 0.15 &&
            r.x[k] < b.x + b.w + 0.15 &&
            r.y[k] > b.y - 0.15 &&
            r.y[k] < b.y + b.d + 0.15
        );
        if (blocked) collisions.push(layer.index);
      }
      if (layer.placeId === 'hall') {
        const first = r.stair.indexOf(1);
        let end = first;
        while (r.stair[end]) end++;
        climb = Array.from(r.z.slice(first, end));
      }
    }
    const walks = daySchedule(SAMPLE_DAY_LAYERS);
    const w = walks[0];
    const idle = advancePlayhead(walks, w.start - 0.5, 0.1, 1) - (w.start - 0.5);
    const walking = advancePlayhead(walks, w.start, 0.1, 1) - w.start;
    return { collisions, climb, idle, walking };
  });
  expect(result.collisions).toEqual([]);
  expect(result.climb.length).toBeGreaterThan(20);
  for (let k = 1; k < result.climb.length; k++) expect(result.climb[k]).toBeGreaterThanOrEqual(result.climb[k - 1]);
  expect(result.climb[result.climb.length - 1] - result.climb[0]).toBeGreaterThan(3.5);
  expect(result.walking).toBeLessThan(result.idle);
});
