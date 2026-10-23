import { defineConfig } from '@playwright/test';

// capture.e2e.spec.ts uses a receiver in a scratch vault and needs port 7700 free.
// reporter.e2e.spec.ts uses a synthetic transport and never connects to a receiver.
// Set DAYCUBE_CHROMIUM to use a Chromium other than Playwright's own.
export default defineConfig({
  testDir: './extensions/tests',
  testMatch: '*.e2e.spec.ts',
  outputDir: './test-results/extension',
  timeout: 120000,
  workers: 1,
  use: { trace: 'off', screenshot: 'off' },
});
