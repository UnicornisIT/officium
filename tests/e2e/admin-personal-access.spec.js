const { test, expect } = require('@playwright/test');

// Administrators switch between the panel and their own cabinet without signing in again.

for (const role of ['admin', 'superadmin']) {
  test(`${role} switches between the admin panel and the personal cabinet`, async ({ page }) => {
    await page.goto(`/dev-login/${role}`);
    await expect(page).toHaveURL(/\/admin$/);

    await page.locator('[data-admin-personal-link]').click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('.navbar')).toBeVisible();

    await page.goto('/incomes');
    await expect(page).toHaveURL(/\/incomes/);

    await page.locator('.navbar [data-admin-panel-link]').click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.locator('.admin-shell')).toBeVisible();
  });
}

test('plain user sees no admin panel link', async ({ page }) => {
  await page.goto('/dev-login/user');
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator('[data-admin-panel-link]')).toHaveCount(0);
});
