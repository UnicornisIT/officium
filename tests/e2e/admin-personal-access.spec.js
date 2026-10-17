const { test, expect } = require('@playwright/test');

// Administrators switch between the panel and their own cabinet without signing in again.
// The panel link lives in the avatar menu, not in the main navigation.

for (const role of ['admin', 'superadmin']) {
  test(`${role} switches between the admin panel and the personal cabinet`, async ({ page }) => {
    await page.goto(`/dev-login/${role}`);
    await expect(page).toHaveURL(/\/admin$/);

    await page.locator('[data-admin-personal-link]').click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('.navbar')).toBeVisible();

    await page.goto('/incomes');
    await expect(page).toHaveURL(/\/incomes/);
    // Only the avatar menu (itself a nav-item) may contain the panel link.
    await expect(page.locator('.navbar-nav > .nav-item:not(.user-menu)', { hasText: 'Панель администратора' })).toHaveCount(0);
    await expect(page.locator('#userMenu [data-admin-panel-link]')).toBeHidden();

    const toggle = page.locator('#userMenuToggle');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await page.locator('.navbar [data-admin-panel-link]').click();
    await expect(page).toHaveURL(/\/admin$/);
    await expect(page.locator('.admin-shell')).toBeVisible();
  });
}

test('plain user sees no admin panel link', async ({ page }) => {
  await page.goto('/dev-login/user');
  await expect(page).toHaveURL(/\/$/);
  await page.locator('#userMenuToggle').click();
  await expect(page.locator('#userMenu')).toBeVisible();
  await expect(page.locator('[data-admin-panel-link]')).toHaveCount(0);
});

test('avatar menu works from the keyboard', async ({ page }) => {
  await page.goto('/dev-login/user');
  const toggle = page.locator('#userMenuToggle');
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#userMenu')).toBeVisible();

  await page.keyboard.press('ArrowDown');
  await expect(page.locator('#userMenu a[href="/profile"]')).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#userMenu')).toBeHidden();
  await expect(toggle).toBeFocused();

  // The theme switch stays in the menu and keeps it open.
  await toggle.click();
  const before = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  await page.locator('#themeToggleBtn').click();
  await expect(page.locator('#userMenu')).toBeVisible();
  const after = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  expect(after).not.toBe(before);

  await page.locator('#userMenu a[href="/profile"]').click();
  await expect(page).toHaveURL(/\/profile$/);
  await expect(page.getByText('Профиль в разработке')).toBeVisible();
});

test('avatar menu opens inside the mobile navigation', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/dev-login/user');
  await page.locator('.navbar-toggler').click();
  const toggle = page.locator('#userMenuToggle');
  await expect(toggle).toBeVisible();
  await toggle.click();
  await expect(page.locator('#userMenu a[href="/logout"]')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

for (const viewport of [{ width: 1280, height: 800 }, { width: 375, height: 812 }]) {
  test(`admin header shows the avatar menu at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.goto('/dev-login/superadmin');
    await expect(page).toHaveURL(/\/admin$/);

    const toggle = page.locator('.admin-topbar #userMenuToggle');
    await expect(toggle.locator('.user-avatar')).toBeVisible();
    await expect(toggle.locator('.user-menu__toggle-name')).toBeHidden();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const menu = page.locator('#userMenu');
    await expect(menu.getByText('Супер-администратор')).toBeVisible();
    await expect(menu.locator('a[href="/logout"]')).toBeVisible();

    const before = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
    await page.locator('#themeToggleBtn').click();
    await expect(menu).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme'))).not.toBe(before);

    const box = await menu.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);

    await menu.locator('[data-admin-menu-personal-link]').click();
    await expect(page).toHaveURL(/\/$/);
  });
}
