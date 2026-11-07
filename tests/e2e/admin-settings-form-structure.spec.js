const { test, expect } = require('@playwright/test');

test('application and Telegram settings share one save button at the bottom', async ({ page }) => {
  await page.goto('/dev-login/superadmin');
  await page.goto('/admin/settings');

  const save = page.locator('button[type="submit"]:visible').filter({ hasText: 'Сохранить' });
  await expect(save).toHaveCount(1);
  await expect(save).toHaveText('Сохранить настройки');
  await expect(save).toHaveAttribute('form', 'admin-settings-form');

  const section = page.locator('section[aria-labelledby="update-notifications-title"]');
  await expect(page.locator('#admin-settings-form').locator('select[name="update_notify_recipients"]')).toHaveCount(1);
  await expect(section.locator('input[readonly]')).toHaveCount(3);
  await expect(section.getByRole('button', { name: 'Отправить тестовое сообщение' })).toHaveAttribute('form', 'update-notification-test-form');
  await expect(section.getByRole('button', { name: 'Проверить сейчас' })).toHaveAttribute('form', 'update-notification-check-form');

  for (const width of [390, 1440]) {
    await page.setViewportSize({ width, height: 900 });
    const switches = await section.locator('.admin-switch-list').boundingBox();
    const fields = await section.locator('.admin-form-grid').first().boundingBox();
    const contacts = await page.locator('.admin-contacts').boundingBox();
    const actions = await page.locator('.admin-settings-actions').boundingBox();
    expect(fields.y - switches.y - switches.height).toBeGreaterThanOrEqual(16);
    expect(actions.y - contacts.y - contacts.height).toBeGreaterThanOrEqual(16);
  }
});
