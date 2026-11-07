const { test, expect } = require('@playwright/test');
const { openVehicleTab } = require('./helpers/vehicle-tabs');

test('documents and vehicle tabs clear the sticky navbar when scrolled into view', async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 600 });
  await page.route('https://telegram.org/js/telegram-web-app.js', route =>
    route.fulfill({ body: '', contentType: 'application/javascript' }));
  await page.goto('/dev-login/user');
  await page.goto('/vehicles/new');
  await page.getByLabel('Название', { exact: true }).fill(`Прокрутка вкладок ${Date.now()}`);
  await page.getByRole('button', { name: 'Сохранить автомобиль' }).click();
  await expect(page).toHaveURL(/\/vehicles\/\d+$/);

  await openVehicleTab(page, 'Документы');
  await expect(page).toHaveURL(/tab=documents$/);
  const section = page.locator('#documents');
  await expect(section.getByRole('heading', { name: 'Документы автомобиля' })).toBeVisible();
  const navbar = page.locator('.navbar');
  await expect(navbar).toHaveCSS('position', 'sticky');

  async function expectBelowNavbar(target) {
    await expect.poll(async () => {
      const bar = await navbar.boundingBox();
      const box = await target.boundingBox();
      return box.y - (bar.y + bar.height);
    }).toBeGreaterThanOrEqual(-1);
    expect((await target.boundingBox()).y).toBeLessThan(600);
  }

  await expectBelowNavbar(section);
  // The expanded form leaves enough room for the browser to align the fragment.
  await section.getByText('Добавить документ', { exact: true }).click();
  await page.evaluate(() => {
    window.scrollTo(0, 0);
    location.hash = 'documents';
  });
  await expect(page).toHaveURL(/#documents$/);
  await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(0);
  await expectBelowNavbar(section);
  await page.screenshot({ path: testInfo.outputPath('v1-after.png') });

  const tabs = section.getByRole('navigation', { name: 'Вкладки автомобиля' });
  await tabs.evaluate(element => element.scrollIntoView({ block: 'start' }));
  await expectBelowNavbar(tabs);
});
