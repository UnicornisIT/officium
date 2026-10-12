const { test, expect } = require('@playwright/test');

test('superadmin updates through the rendered button with NoNewPrivileges enabled', async ({ page }) => {
  const password = process.env.STAGING_ADMIN_PASSWORD;
  const expectedTag = process.env.STAGING_TARGET_TAG;
  if (!password || !expectedTag) throw new Error('Staging credentials/tag were not supplied');

  await page.goto('/admin/login');
  await page.getByLabel('Пароль').fill(password);
  await page.getByRole('button', { name: /Войти/ }).click();
  await expect(page).toHaveURL(/\/admin/);

  await page.goto('/admin/server-update');
  await expect(page.getByText('IPC из Flask')).toBeVisible();
  await expect(page.getByText('Готов', { exact: true })).toHaveCount(2);
  await page.getByRole('button', { name: new RegExp(`Обновить до ${expectedTag.replaceAll('.', '\\.')}`) }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('button', { name: 'Подтвердить' }).click();

  await expect.poll(async () => {
    const response = await page.request.get('/admin/server-update/status');
    const payload = await response.json();
    if (payload.state === 'failed') throw new Error(`Update failed: ${payload.error_code} operation=${payload.operation_id}`);
    return payload.state;
  }, { timeout: 20 * 60 * 1000, intervals: [2000, 5000, 10000] }).toBe('success');

  await page.reload();
  await expect(page.getByText(expectedTag, { exact: true }).first()).toBeVisible();
});
