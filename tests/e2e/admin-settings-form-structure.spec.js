const { test, expect } = require('@playwright/test');

test('Telegram notification settings save after the status fields and before separate actions', async ({ page }) => {
  await page.goto('/dev-login/superadmin');
  await page.goto('/admin/settings');

  await expect(page.locator('button[type="submit"]:visible').filter({ hasText: 'Сохранить' })).toHaveCount(2);

  const section = page.locator('section[aria-labelledby="update-notifications-title"]');
  const settingsForm = section.locator('form[action$="/update-notifications"]');
  const save = settingsForm.getByRole('button', { name: 'Сохранить уведомления', exact: true });
  await expect(save).toBeVisible();
  await expect(settingsForm.locator('input[readonly]')).toHaveCount(3);
  await expect(settingsForm.locator('input, select, textarea, button').last()).toHaveText('Сохранить уведомления');
  await expect(section.locator('form button[type="submit"]')).toHaveText([
    'Сохранить уведомления',
    'Отправить тестовое сообщение',
    'Проверить сейчас',
  ]);
});
