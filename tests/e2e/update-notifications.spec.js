const { test, expect } = require('@playwright/test');

for (const width of [1440, 390]) {
  for (const theme of ['light', 'dark']) {
    test(`update notifications ${width} ${theme}`, async ({ page }, info) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(value => localStorage.setItem('officiumTheme', value), theme);
      await page.goto('/dev-login/superadmin');
      await page.goto('/admin/settings');
      const section = page.getByRole('region', { name: 'Уведомления об обновлениях', exact: true });
      await expect(section).toBeVisible();
      await section.getByRole('checkbox', { name: /Включить уведомления об обновлениях/ }).check();
      await section.getByRole('combobox', { name: /Получатели/ }).selectOption('admins');
      await section.getByRole('spinbutton', { name: /Интервал проверки, минут/ }).fill('15');
      await section.getByRole('button', { name: 'Сохранить уведомления', exact: true }).click();
      await expect(page.getByText('Настройки уведомлений сохранены.', { exact: true })).toBeVisible();
      await expect(section.getByRole('checkbox', { name: /Включить уведомления об обновлениях/ })).toBeChecked();
      await expect(section.getByRole('combobox', { name: /Получатели/ })).toHaveValue('admins');
      await expect(section.getByRole('spinbutton', { name: /Интервал проверки, минут/ })).toHaveValue('15');
      await expect(section.locator('input[readonly]')).toHaveCount(3);
      await expect(section.getByRole('button', { name: 'Отправить тестовое сообщение', exact: true })).toBeVisible();
      await expect(section.getByRole('button', { name: 'Проверить сейчас', exact: true })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await section.screenshot({ path: info.outputPath('update-notifications.png') });
    });
  }
}
