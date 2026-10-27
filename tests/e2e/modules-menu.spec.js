const { test, expect } = require('@playwright/test');

for (const width of [1440, 390]) {
  for (const theme of ['light', 'dark']) {
    test(`modules menu ${width}px ${theme}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(value => localStorage.setItem('officiumTheme', value), theme);
      await page.goto('/dev-login/user');
      await page.goto('/expenses');
      if (width < 992) await page.locator('.navbar-toggler').click();
      const toggle = page.getByRole('button', { name: 'Модули', exact: true });
      const menu = page.locator('#modulesMenu');
      await expect(menu).toBeHidden();
      const order = await page.locator('#navbarNav > ul > li').evaluateAll(items => items.map(item => {
        const link = item.querySelector(':scope > a');
        return link?.getAttribute('href') || (item.querySelector('#modulesMenuToggle') ? 'modules' : '');
      }));
      expect(order.indexOf('modules')).toBe(order.indexOf('/mortgages') + 1);
      expect(order.indexOf('/archive')).toBe(order.indexOf('modules') + 1);
      if (width >= 992) await toggle.hover();
      else await toggle.click();
      await expect(menu).toBeVisible();
      await expect(toggle).toHaveAttribute('aria-expanded', 'true');
      await expect(menu.getByRole('link', { name: 'Автомобили', exact: true })).toBeVisible();
      await expect(menu.getByRole('link', { name: 'Цели', exact: true })).toBeVisible();
      const bounds = await menu.boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await page.screenshot({ path: testInfo.outputPath('modules-menu.png'), fullPage: true });
      if (width >= 992) {
        await menu.getByRole('link', { name: 'Цели', exact: true }).hover();
        await expect(menu).toBeVisible();
        await page.keyboard.press('Escape');
        await expect(menu).toBeHidden();
        await toggle.press('ArrowDown');
        await expect(menu).toBeVisible();
      }
      await menu.getByRole('link', { name: 'Автомобили', exact: true }).click();
      await expect(page).toHaveURL(/\/vehicles$/);
      await expect(page.locator('#modulesMenuToggle')).toHaveClass(/active/);
    });
  }
}
