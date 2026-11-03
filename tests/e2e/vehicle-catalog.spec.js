const { test, expect } = require('@playwright/test');

for (const width of [1440, 390]) {
  test(`vehicle make, dependent models and body identifier ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto('/dev-login/user');
    await page.goto('/vehicles/new');
    const make = page.getByLabel('Марка', { exact: true });
    const model = page.getByLabel('Модель', { exact: true });
    await make.fill('Toy');
    await expect(page.locator('#vehicle-make-options option[value="Toyota"]')).toHaveCount(1);
    await make.fill('Toyota');
    await make.press('Tab');
    await expect(page.locator('#vehicle-model-options option[value="Mark X"]')).toHaveCount(1);
    await model.fill('Mark X');
    await make.fill('LADA');
    await make.press('Tab');
    await expect(model).toHaveValue('');
    await expect(page.locator('#vehicle-model-options option[value="Granta"]')).toHaveCount(1);
    await expect(page.locator('#vehicle-model-options option[value="Mark X"]')).toHaveCount(0);
    await model.fill('Granta');
    await make.fill('ВАЗ');
    await make.press('Tab');
    await expect(model).toHaveValue('Granta');
    await make.fill('УАЗ');
    await make.press('Tab');
    await expect(page.locator('#vehicle-model-options option[value="Патриот"]')).toHaveCount(1);
    await make.fill('Toyota');
    await make.press('Tab');
    await model.fill('Mark X');
    const name = `Японский кузов ${width} ${Date.now()}`;
    await page.getByLabel('Название', { exact: true }).fill(name);
    await page.getByLabel('VIN', { exact: true }).fill('GRX130-1234567');
    await page.getByRole('button', { name: 'Сохранить автомобиль' }).click();
    await expect(page).toHaveURL(/\/vehicles\/\d+$/);
    await expect(page.locator('.vehicle-facts')).toContainText('GRX130-1234567');
    await page.goto(`${page.url()}/edit`);
    await expect(make).toHaveValue('Toyota');
    await expect(model).toHaveValue('Mark X');
    await expect(page.locator('#vehicle-model-options option[value="Mark X"]')).toHaveCount(1);
    await make.fill('Своя марка');
    await make.press('Tab');
    await expect(page.locator('#vehicle-model-options option')).toHaveCount(0);
    await model.fill('Своя модель');
    await page.getByRole('button', { name: 'Сохранить автомобиль' }).click();
    await expect(page).toHaveURL(/\/vehicles\/\d+$/);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
