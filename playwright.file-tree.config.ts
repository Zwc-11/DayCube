import { defineConfig } from '@playwright/test';

// The FileTree fixture is synthetic and requires neither the API nor a vault.
export default defineConfig({
  testDir: './apps/viewer/e2e',
  testMatch: 'file-tree-selection.spec.ts',
  outputDir: './test-results/file-tree',
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:13112', headless: true },
  webServer: {
    command: 'npm run dev --workspace @daycube/viewer -- --port 13112 --strictPort',
    url: 'http://127.0.0.1:13112/e2e/fixtures/file-tree-profile.html',
    reuseExistingServer: false,
    timeout: 60000,
  },
});
