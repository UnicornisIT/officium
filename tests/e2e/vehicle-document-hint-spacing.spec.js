const path = require('node:path');
const { test, expect } = require('@playwright/test');

test.use({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce', video: 'off' });

test('expiry hint is separated from its input and the amount label', async ({ page, baseURL }, info) => {
  const origin = new URL(baseURL).origin;
  await page.route(url => url.origin !== origin, route => route.abort());
  await page.goto('/dev-login/user', { waitUntil: 'domcontentloaded' });
  const csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content');
  const vehicle = await page.request.post('/vehicles/new', {
    form: { csrf_token: csrf, name: `Подсказка документа ${info.project.name} ${Date.now()}` },
  });
  const vehiclePath = new URL(vehicle.url()).pathname;
  expect(vehiclePath).toMatch(/^\/vehicles\/\d+$/);
  await page.goto(`${vehiclePath}?tab=documents`, { waitUntil: 'domcontentloaded' });
  await page.getByText('Добавить документ', { exact: true }).click();

  const form = page.locator('.vehicle-document-form');
  const expiry = form.locator('[name="expires_at"]');
  const hint = form.getByText('Оставьте пустой для бессрочного документа', { exact: true });
  const amountLabel = form.locator('label').filter({ has: page.locator('[name="amount"]') });
  await expect(hint).toBeVisible();
  await expect(hint).toHaveCSS('display', 'block');
  const expiryBox = await expiry.boundingBox();
  const hintBox = await hint.boundingBox();
  const amountBox = await amountLabel.boundingBox();
  expect(amountBox.y - (hintBox.y + hintBox.height)).toBeGreaterThanOrEqual(6);
  expect(hintBox.y - (expiryBox.y + expiryBox.height)).toBeGreaterThanOrEqual(4);

  if (info.project.name === 'chromium' && process.env.V5_SCREENSHOT_DIR) {
    await expiry.evaluate(input => input.scrollIntoView({ block: 'center' }));
    await page.screenshot({ path: path.join(process.env.V5_SCREENSHOT_DIR, 'v5-after.png') });
  }
});
