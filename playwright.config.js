const { defineConfig, devices } = require('@playwright/test');

const stagingUpdateURL = process.env.STAGING_UPDATE_URL;

module.exports = defineConfig({
  testDir: './tests/e2e',
  testIgnore: stagingUpdateURL ? [] : ['staging-update.spec.js'],
  timeout: 60_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ['line'],
    ['junit', { outputFile: 'test-results/playwright-junit.xml' }],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  use: {
    baseURL: stagingUpdateURL || 'http://127.0.0.1:5010',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    locale: 'ru-RU',
    ignoreHTTPSErrors: process.env.STAGING_ALLOW_SELF_SIGNED === 'true',
  },
  webServer: stagingUpdateURL ? undefined : {
    command: 'python scripts/e2e_server.py',
    url: 'http://127.0.0.1:5010/login',
    reuseExistingServer: false,
    timeout: 60_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
  ],
});
