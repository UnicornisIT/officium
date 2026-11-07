const path = require('node:path');
const { test, expect } = require('@playwright/test');

test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, video: 'off' });
test.skip(({ browserName }) => browserName === 'firefox', 'Firefox does not support mobile emulation');

test('admin log filters fit without overlap in the narrow Mini App', async ({ page, baseURL, browserName }) => {
  const origin = new URL(baseURL).origin;
  await page.route(url => url.origin !== origin, route => route.abort());
  await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
    contentType: 'application/javascript',
    body: `
      const noop = () => {};
      window.Telegram = { WebApp: {
        initData: 'e2e', platform: '${browserName === 'webkit' ? 'ios' : 'android'}',
        version: '8.0', colorScheme: 'dark',
        viewportHeight: innerHeight, viewportStableHeight: innerHeight,
        ready: noop, expand: noop, onEvent: noop, offEvent: noop,
        setHeaderColor: noop, setBackgroundColor: noop, setBottomBarColor: noop,
        BackButton: {show: noop, hide: noop, onClick: noop},
      }};
    `,
  }));
  await page.request.get('/dev-login/admin');
  await page.request.get('/telegram-app');
  await page.goto('/admin/logs', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toHaveClass(/telegram-mini-app/);
  await expect(page.locator('body')).toHaveClass(/admin-page/);

  const form = page.locator('.admin-filter-grid--logs');
  const from = form.getByLabel('Дата от', { exact: true });
  const to = form.getByLabel('Дата до', { exact: true });
  const toLabel = form.getByText('Дата до', { exact: true });
  const checkbox = form.getByRole('checkbox', { name: 'Только вход от имени пользователя' });
  const fromBox = await from.boundingBox();
  const toBox = await to.boundingBox();
  const labelBox = await toLabel.boundingBox();
  const checkboxBox = await checkbox.boundingBox();
  expect(labelBox.y - (fromBox.y + fromBox.height)).toBeGreaterThanOrEqual(8);
  expect(checkboxBox.y - (toBox.y + toBox.height)).toBeGreaterThanOrEqual(8);

  const fields = await form.locator(':scope > label, :scope > .admin-filter-actions').all();
  const boxes = await Promise.all(fields.map(field => field.boundingBox()));
  for (let index = 1; index < boxes.length; index++) {
    const previous = boxes[index - 1];
    expect(boxes[index].y - (previous.y + previous.height)).toBeGreaterThanOrEqual(15);
  }
  await expect(from).toHaveCSS('height', '44px');
  await expect(to).toHaveCSS('height', '44px');

  if (browserName === 'webkit' && process.env.V7_SCREENSHOT_DIR) {
    await form.evaluate(element => window.scrollBy(0, element.getBoundingClientRect().top - 130));
    await page.screenshot({ path: path.join(process.env.V7_SCREENSHOT_DIR, 'v7-after.png'), scale: 'css' });
  }
});
