const path = require('node:path');
const { test, expect } = require('@playwright/test');

test.use({ viewport: { width: 1280, height: 600 }, reducedMotion: 'reduce', video: 'off' });

test('dashboard and financial plan anchors clear the sticky navbar', async ({ page, baseURL }, info) => {
  const origin = new URL(baseURL).origin;
  await page.route(url => url.origin !== origin, route => route.abort());
  await page.request.get('/dev-login/user');
  await page.goto('/', { waitUntil: 'domcontentloaded' });

  const navbar = page.locator('.navbar');
  await expect(navbar).toHaveCSS('position', 'sticky');
  async function expectBelowNavbar(target) {
    await expect.poll(async () => {
      const bar = await navbar.boundingBox();
      const section = await target.boundingBox();
      return section.y - (bar.y + bar.height);
    }).toBeGreaterThanOrEqual(-1);
    expect((await target.boundingBox()).y).toBeLessThan(600);
  }

  await page.getByRole('link', { name: 'Перейти к долгам' }).click();
  await expect(page).toHaveURL(/#debtsSection$/);
  await expectBelowNavbar(page.locator('#debtsSection'));
  await expect(page.locator('html')).toHaveCSS('scroll-padding-top', '80px');
  await expect(page.locator('.app-main')).toHaveCSS('padding-top', '80px');

  await page.goto('/goals', { waitUntil: 'domcontentloaded' });
  await page.locator('a[href$="#goals"]').click();
  await expect(page).toHaveURL(/\/financial-plan#goals$/);
  const goals = page.locator('section#goals');
  await expect(goals).toHaveCSS('scroll-margin-top', '80px');
  await expectBelowNavbar(goals);

  if (info.project.name === 'chromium' && process.env.V6_SCREENSHOT_DIR) {
    await page.screenshot({ path: path.join(process.env.V6_SCREENSHOT_DIR, 'v6-after.png') });
  }
});
