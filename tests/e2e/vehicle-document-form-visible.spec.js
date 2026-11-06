const { test, expect } = require('@playwright/test');
const { openVehicleTab } = require('./helpers/vehicle-tabs');

test.use({ video: 'off' });

test('document save stays visible and opening a clipped form reveals its first field', async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.route('https://telegram.org/js/telegram-web-app.js', route =>
    route.fulfill({ body: '', contentType: 'application/javascript' }));
  await page.goto('/dev-login/user', { waitUntil: 'domcontentloaded' });
  await page.goto('/vehicles/new', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Название', { exact: true }).fill(`Форма документа ${Date.now()}`);
  await page.getByRole('button', { name: 'Сохранить автомобиль' }).click();
  await expect(page).toHaveURL(/\/vehicles\/\d+$/);
  await openVehicleTab(page, 'Документы');

  const summary = page.getByText('Добавить документ', { exact: true });
  const form = page.locator('.vehicle-document-form');
  const save = form.getByRole('button', { name: 'Сохранить документ' });
  const startScroll = await page.evaluate(() => scrollY);
  await summary.click();
  await expect(save).toBeInViewport({ ratio: 1 });
  const box = await save.boundingBox();
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(800);
  expect(await page.evaluate(() => scrollY)).toBe(startScroll);
  await page.screenshot({ path: testInfo.outputPath('v2-after.png') });

  await summary.click();
  await expect(form).toBeHidden();
  const clippedTop = await form.evaluate(element => {
    element.parentElement.open = true;
    window.scrollBy(0, element.getBoundingClientRect().top + 48);
    return element.getBoundingClientRect().top;
  });
  expect(clippedTop).toBeLessThan(0);
  const firstField = form.getByLabel('Тип документа');
  await expect(firstField).toBeInViewport({ ratio: 1 });
  await expect.poll(async () => (await form.boundingBox()).y).toBeGreaterThanOrEqual(0);
  await expect(save).toBeInViewport({ ratio: 1 });

  await page.setViewportSize({ width: 390, height: 800 });
  await form.evaluate(element => element.scrollIntoView({ block: 'start' }));
  const bottomNav = page.locator('.browser-bottom-nav');
  await expect(bottomNav).toBeVisible();
  await expect.poll(() => save.evaluate(button => {
    const nav = document.querySelector('.browser-bottom-nav');
    return nav.getBoundingClientRect().top - button.getBoundingClientRect().bottom;
  })).toBeGreaterThanOrEqual(0);
  await expect(save).toBeInViewport({ ratio: 1 });
});
