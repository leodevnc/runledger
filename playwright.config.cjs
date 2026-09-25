const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests/browser', workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:8877', viewport: { width: 1440, height: 1050 }, headless: true },
  webServer: { command: 'python3 -m runledger --data .runledger-e2e serve --port 8877', url: 'http://127.0.0.1:8877', reuseExistingServer: false }
});
