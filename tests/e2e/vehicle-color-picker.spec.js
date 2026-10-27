const { test, expect } = require('@playwright/test');

test('compact vehicle color picker: presets, custom color, keyboard and persistence', async ({ page }, testInfo) => {
  test.setTimeout(240_000);
  await page.goto('/dev-login/user');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const theme of ['light', 'dark']) {
      await page.addInitScript(value => localStorage.setItem('officiumTheme', value), theme);
      await page.goto('/vehicles/new');
      const picker = page.locator('.vehicle-color-picker');
      const panel = page.locator('.vehicle-color-panel');
      const preview = page.locator('#vehiclePreview');
      await expect(panel).toBeHidden();
      expect((await page.locator('.vehicle-form-preview').boundingBox()).height).toBeLessThan(250);
      await page.getByText('Изменить цвет', { exact: true }).click();
      await expect(panel).toBeVisible();
      await expect(picker.locator('[data-vehicle-color]')).toHaveCount(12);
      await picker.getByRole('button', { name: 'Красный', exact: true }).click();
      await expect(page.getByLabel('Цвет', { exact: true })).toHaveValue('#dc3545');
      await expect(preview).toHaveCSS('color', 'rgb(220, 53, 69)');
      await expect(picker.getByRole('button', { name: 'Красный', exact: true })).toHaveAttribute('aria-pressed', 'true');
      await page.getByLabel('Цвет', { exact: true }).fill('#123abc');
      await expect(preview).toHaveCSS('color', 'rgb(18, 58, 188)');
      await expect(picker.locator('[aria-pressed="true"]')).toHaveCount(0);
      const supportsEyedropper = await page.evaluate(() => typeof window.EyeDropper === 'function');
      await expect(picker.locator('.vehicle-eyedropper')).toBeVisible({ visible: supportsEyedropper });
      await page.screenshot({ path: testInfo.outputPath(`color-picker-${width}-${theme}.png`), fullPage: true });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.getByLabel('Цвет', { exact: true }).press('Escape');
      await expect(panel).toBeHidden();
      await expect(page.locator('.vehicle-color-toggle')).toBeFocused();
    }
  }
  await page.getByLabel('Название', { exact: true }).fill(`Учебная палитра ${testInfo.project.name}`);
  await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
  await page.waitForURL(/\/vehicles\/\d+$/);
  await page.getByRole('link', { name: 'Редактировать', exact: true }).click();
  await expect(page.getByLabel('Цвет', { exact: true })).toHaveValue('#123abc');
  await expect(page.locator('#vehiclePreview')).toHaveCSS('color', 'rgb(18, 58, 188)');
});

test('vehicle eyedropper applies a sampled color', async ({ page }) => {
  // Native screen sampling needs OS interaction; verify the API result here.
  await page.addInitScript(() => {
    window.EyeDropper = class { async open() { return { sRGBHex: '#aabbcc' }; } };
  });
  await page.goto('/dev-login/user');
  await page.goto('/vehicles/new');
  await page.getByText('Изменить цвет', { exact: true }).click();
  await page.getByRole('button', { name: 'Пипетка', exact: true }).click();
  await expect(page.getByLabel('Цвет', { exact: true })).toHaveValue('#aabbcc');
  await expect(page.locator('#vehiclePreview')).toHaveCSS('color', 'rgb(170, 187, 204)');
});
