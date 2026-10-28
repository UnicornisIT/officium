const { test, expect } = require('@playwright/test');

for (const width of [1440, 390]) {
  test(`vehicle field explanations ${width}px`, async ({ page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/dev-login/user');
    await page.goto('/vehicles/new');
    await page.getByLabel('Название', { exact: true }).fill('Учебное авто подсказок');
    await expect(page.locator('.vehicle-form [data-info-hint]:visible')).toHaveCount(4);
    await expect(page.getByRole('button', { name: 'Подсказка: Название прицепа', exact: true })).toBeHidden();
    for (const name of ['VIN', 'Госномер', 'Напоминание о пробеге', 'Плательщик по умолчанию']) {
      await expect(page.getByRole('button', { name: `Подсказка: ${name}`, exact: true })).toBeVisible();
    }
    for (const hint of await page.locator('.vehicle-form [data-info-hint]:visible').all()) {
      const button = hint.getByRole('button');
      const bubble = hint.getByRole('tooltip', { includeHidden: true });
      await expect(bubble).toBeHidden();
      await button.click();
      await expect(bubble).toBeVisible();
      const bounds = await bubble.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await button.press('Escape');
      await expect(bubble).toBeHidden();
    }
    await expect(page).toHaveURL(/\/vehicles\/new$/);
    await expect(page.getByLabel('Название', { exact: true })).toHaveValue('Учебное авто подсказок');
    const company = page.getByRole('switch', { name: 'Служебный автомобиль', exact: true });
    await expect(company).not.toBeChecked();
    await page.getByLabel('Тип', { exact: true }).selectOption('trailer');
    await expect(page.getByLabel('Топливо', { exact: true })).toBeHidden();
    await expect(page.locator('[name="mileage_reminder_days"]')).toBeHidden();
    await expect(page.getByRole('button', { name: 'Подсказка: Напоминание о пробеге', exact: true })).toBeHidden();
    await page.getByLabel('Тип', { exact: true }).selectOption('tractor_unit');
    await expect(page.locator('.vehicle-form [data-info-hint]:visible')).toHaveCount(5);
    await page.getByRole('button', { name: 'Подсказка: Название прицепа', exact: true }).click();
    await expect(page.locator('#vehicle-hint-trailer_name')).toContainText('создан вместе с тягачом');
    await page.keyboard.press('Escape');
    await page.getByLabel('Тип', { exact: true }).selectOption('passenger');
    await expect(page.getByRole('button', { name: 'Подсказка: Название прицепа', exact: true })).toBeHidden();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
