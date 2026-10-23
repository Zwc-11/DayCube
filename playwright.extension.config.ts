import { defineConfig } from '@playwright/test';

// The browser extension against a receiver in a scratch vault (never the real one).
// Needs port 7700 free; set DAYCUBE_CHROMIUM to use a Chromium other than Playwright's own.
export default defineConfig({
  testDir: './extensions/tests',
  testMatch: '*.e2e.spec.ts',
  timeout: 120000,
  workers: 1,
  use: { trace: 'off', screenshot: 'off' },
});
