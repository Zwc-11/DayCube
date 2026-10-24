import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './services/ingest/browser-tests',
  outputDir: './test-results/api',
  timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:17701', headless: true, trace: 'off', screenshot: 'off' },
  // Never seed or mutate the user's actual vault in browser tests.
  webServer: {
    command: 'node scripts/start-api-test.mjs',
    url: 'http://127.0.0.1:17701/health',
    reuseExistingServer: false,
    timeout: 60000,
    env: { DAYCUBE_VAULT_ROOT: '.daycube-e2e-vault' },
  },
});
