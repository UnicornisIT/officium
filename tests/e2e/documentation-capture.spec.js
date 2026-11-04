const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');

// Explicit opt-in: normal CI never overwrites the user-guide illustrations.
test('refresh all user documentation screenshots', async ({ page, browserName }) => {
  test.skip(process.env.UPDATE_DOCUMENTATION_SCREENSHOTS !== '1', 'Documentation capture requires explicit opt-in');
  test.skip(browserName !== 'chromium', 'One renderer keeps the published screenshot set consistent');
  test.setTimeout(300_000);
  const output = path.resolve('static/images/documentation');
  fs.mkdirSync(output, { recursive: true });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'light' });
  await page.addInitScript(() => localStorage.setItem('officiumTheme', 'light'));
  await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
    contentType: 'application/javascript', body: `
      const noop = () => {};
      window.Telegram = {WebApp:{initData:'e2e',initDataUnsafe:{user:{id:1}},
        platform:'android',version:'8.0',colorScheme:'dark',themeParams:{},
        viewportHeight:innerHeight,viewportStableHeight:innerHeight,
        ready:noop,expand:noop,onEvent:noop,offEvent:noop,
        setHeaderColor:noop,setBackgroundColor:noop,setBottomBarColor:noop,
        BackButton:{show:noop,hide:noop,onClick:noop},HapticFeedback:{impactOccurred:noop}}};`,
  }));
  await page.goto('/dev-login/user');
  const database = process.env.OFFICIUM_E2E_DATABASE || path.join(os.tmpdir(), 'officium-playwright.sqlite');
  const localPython = path.resolve('.venv/Scripts/python.exe');
  const python = process.env.PYTHON || (fs.existsSync(localPython) ? localPython : 'python');
  const fixture = JSON.parse(execFileSync(python, ['scripts/seed_documentation_demo.py', database], { encoding: 'utf8' }).trim());
  const images = [];
  async function capture(filename, route, { prepare, fullPage = false, mode = 'browser', selector } = {}) {
    if (route) {
      const response = await page.goto(route, { waitUntil: 'networkidle' });
      expect(response.status()).toBe(200);
    }
    await page.evaluate(() => document.fonts.ready);
    if (prepare) await prepare();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const options = { path: path.join(output, filename), animations: 'disabled' };
    const png = selector
      ? await page.locator(selector).screenshot(options)
      : await page.screenshot({ ...options, fullPage });
    images.push({ file: filename, route: new URL(page.url()).pathname + new URL(page.url()).search,
      mode, theme: await page.locator('html').getAttribute('data-theme'),
      viewport: page.viewportSize(), fullPage, ...(selector ? { selector } : {}),
      width: png.readUInt32BE(16), height: png.readUInt32BE(20) });
  }
  for (const [filename, route] of [
    ['dashboard.png', '/'], ['expenses.png', '/expenses'], ['incomes.png', '/incomes'],
    ['analytics.png', '/finance'], ['financial-plan.png', '/financial-plan'],
    ['goals.png', '/goals'], ['import.png', '/expenses/import'],
    ['vehicles.png', '/vehicles'], ['vehicle-detail.png', `/vehicles/${fixture.vehicle_id}`],
    ['vehicle-form.png', `/vehicles/${fixture.vehicle_id}/edit`],
  ]) await capture(filename, route);
  await capture('debts.png', '/', { prepare: () => page.evaluate(() => openAddModal()) });
  await capture('vehicle-menu.png', `/vehicles/${fixture.vehicle_id}`, {
    prepare: () => page.getByRole('navigation', { name: 'Вкладки автомобиля' }).getByRole('button', { name: 'Ещё', exact: true }).click(),
  });
  await capture('vehicle-documents.png', `/vehicles/${fixture.vehicle_id}?tab=documents`, {
    prepare: () => page.getByText('Добавить документ', { exact: true }).click(), fullPage: true,
  });
  await page.goto('/profile');
  const privacy = page.getByRole('region', { name: 'Приватность', exact: true });
  await privacy.getByRole('checkbox').check();
  await privacy.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(privacy.getByRole('status')).toHaveText('Сохранено');
  await capture('profile.png', null, { fullPage: true });
  await capture('privacy-hidden.png', '/finance');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/telegram-app');
  await capture('telegram-mini-app.png', '/profile', { mode: 'telegram-sdk-stub' });
  await expect(page.locator('body')).toHaveClass(/telegram-mini-app/);
  await page.setViewportSize({ width: 390, height: 2400 });
  await page.evaluate(() => {
    window.Telegram.WebApp.viewportHeight = innerHeight;
    window.Telegram.WebApp.viewportStableHeight = innerHeight;
    window.dispatchEvent(new Event('resize'));
  });
  await capture('profile-mobile.png', null, {
    mode: 'telegram-sdk-stub', selector: '[aria-labelledby="profileNotificationsTitle"]',
  });
  // A clean anonymous page previews the guide, independently of the demo account's privacy setting.
  await page.goto('/logout');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await capture('documentation-preview.png', '/documentation');
  await expect(page.locator('[data-docs-section]')).toHaveCount(12);
  const imageSources = await page.locator('.docs-screenshot img').evaluateAll(nodes => nodes.map(node => node.src));
  for (const source of imageSources) {
    const response = await page.request.get(source);
    expect(response.status()).toBe(200);
  }
  expect(errors).toEqual([]);
  fs.writeFileSync(path.join(output, 'manifest.json'), JSON.stringify({
    capturedAt: new Date().toISOString(), data: 'Fictional, isolated Playwright database',
    telegram: 'SDK stub; not a native client screenshot or delivery confirmation',
    command: 'UPDATE_DOCUMENTATION_SCREENSHOTS=1 npx playwright test tests/e2e/documentation-capture.spec.js --project=chromium',
    images,
  }, null, 2) + '\n');
});
