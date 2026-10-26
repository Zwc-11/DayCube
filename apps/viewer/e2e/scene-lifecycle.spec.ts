import { test, expect } from '@playwright/test';
import { captureSceneLifecycle } from './fixtures/scene-lifecycle.mjs';

for (const scenario of ['mesh_failure', 'mesh_closed_while_loading', 'splat_failure']) {
  test(`${scenario}: release owned resources and stop drawing`, async ({ page }) => {
    const result = await captureSceneLifecycle(page, scenario);
    expect(result.observed.created).toBe(1);
    expect(result.observed.disposed).toBe(1);
    expect(result.closed.disposed).toBe(1); // closing a failed view must not dispose it twice
    expect(result.draws_after_settling).toBe(0);
    if (scenario === 'mesh_closed_while_loading') expect(result.closed.geometriesDisposed).toBe(1);
  });
}
