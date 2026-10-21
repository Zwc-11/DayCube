import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './apps/viewer/e2e',
  timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:13000', headless: true,
    viewport: { width: 1600, height: 1000 }, trace: 'off', screenshot: 'off' },
  webServer: [
    { command: 'node scripts/start-api-test.mjs', url: 'http://127.0.0.1:17701/health',
      reuseExistingServer: false, timeout: 60000 },
    { command: 'npm run dev --workspace @daycube/viewer -- --port 13000',
      url: 'http://127.0.0.1:13000', reuseExistingServer: false, timeout: 60000,
      env: { DAYCUBE_API_TARGET: 'http://127.0.0.1:17701' } },
  ],
});
