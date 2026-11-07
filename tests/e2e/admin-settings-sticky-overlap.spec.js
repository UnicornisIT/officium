const path = require('node:path');
const { test, expect } = require('@playwright/test');

test.use({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce', video: 'off' });

test('save settings scrolls with the page and leaves the update heading clear', async ({ page, baseURL }, info) => {
  const origin = new URL(baseURL).origin;
  await page.route(url => url.origin !== origin, route => route.abort());
  await page.request.get('/dev-login/superadmin');
  await page.goto('/admin/settings', { waitUntil: 'domcontentloaded' });

  const heading = page.getByRole('heading', { name: 'Уведомления об обновлениях', exact: true });
  const save = page.getByRole('button', { name: 'Сохранить настройки', exact: true });
  const actions = save.locator('..');
  const panel = page.locator('[aria-labelledby="update-notifications-title"]');
  await heading.evaluate(element => window.scrollBy(0, element.getBoundingClientRect().top - 110));

  await expect(heading).toBeInViewport({ ratio: 1 });
  await expect(actions).toHaveCSS('position', 'static');
  await expect(panel).toHaveCSS('margin-top', '32px');
  const headingBox = await heading.boundingBox();
  const saveBox = await save.boundingBox();
  const gapBeforeHeading = headingBox.y - (saveBox.y + saveBox.height);
  const gapAfterHeading = saveBox.y - (headingBox.y + headingBox.height);
  expect(Math.max(gapBeforeHeading, gapAfterHeading)).toBeGreaterThanOrEqual(4);

  if (info.project.name === 'chromium' && process.env.V8_SCREENSHOT_DIR) {
    await page.screenshot({ path: path.join(process.env.V8_SCREENSHOT_DIR, 'v8-after.png') });
  }
});
