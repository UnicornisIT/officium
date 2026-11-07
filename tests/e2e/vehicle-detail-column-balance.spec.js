const { test, expect } = require('@playwright/test');
const { openVehicleTab } = require('./helpers/vehicle-tabs');

test.use({ video: 'off' });

test('vehicle specs fit their content and stay visible beside a long document form', async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route('https://telegram.org/js/telegram-web-app.js', route =>
    route.fulfill({ body: '', contentType: 'application/javascript' }));
  await page.goto('/dev-login/user', { waitUntil: 'domcontentloaded' });
  await page.goto('/vehicles/new', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Название', { exact: true }).fill(`Карточка автомобиля ${Date.now()}`);
  await page.getByRole('button', { name: 'Сохранить автомобиль' }).click();
  await expect(page).toHaveURL(/\/vehicles\/\d+$/);
  await openVehicleTab(page, 'Документы');
  await page.getByText('Добавить документ', { exact: true }).click();

  const specs = page.locator('.vehicle-detail-grid > section').first();
  const unusedSpace = await specs.evaluate(card =>
    card.getBoundingClientRect().bottom - card.lastElementChild.getBoundingClientRect().bottom);
  expect(unusedSpace).toBeLessThanOrEqual(200);
  const scrollTarget = await page.evaluate(() =>
    Math.min(400, document.documentElement.scrollHeight - innerHeight));
  expect(scrollTarget).toBeGreaterThan(0);
  await page.evaluate(target => window.scrollTo(0, target), scrollTarget);
  await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThanOrEqual(scrollTarget - 1);
  const clearance = await specs.evaluate(card =>
    card.getBoundingClientRect().top - document.querySelector('.navbar').getBoundingClientRect().bottom);
  expect(clearance).toBeGreaterThanOrEqual(0);
  await expect(specs).toBeInViewport({ ratio: 1 });
  await page.screenshot({ path: testInfo.outputPath('v3-after.png') });

  await page.setViewportSize({ width: 1440, height: 500 });
  await expect(specs).toHaveCSS('overflow-y', 'auto');
  const scrollable = await specs.evaluate(card => card.scrollHeight > card.clientHeight);
  expect(scrollable).toBe(true);

  await page.setViewportSize({ width: 390, height: 800 });
  await expect(specs).toHaveCSS('position', 'static');
  await expect(specs).toHaveCSS('max-height', 'none');
  await expect(specs).toHaveCSS('overflow-y', 'visible');
});
