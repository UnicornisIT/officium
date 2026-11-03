const { test, expect } = require('@playwright/test');

for (const scenario of [
  { width: 1440, theme: 'light', mini: false },
  { width: 1440, theme: 'dark', mini: false },
  { width: 390, theme: 'light', mini: false },
  { width: 390, theme: 'dark', mini: true },
]) {
  test(`current documentation ${scenario.width} ${scenario.theme} mini=${scenario.mini}`, async ({ page }, info) => {
    test.setTimeout(120_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize({ width: scenario.width, height: 1000 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.addInitScript(theme => localStorage.setItem('officiumTheme', theme), scenario.theme);
    if (scenario.mini) {
      await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
        contentType: 'application/javascript', body: `
          const noop=()=>{};window.Telegram={WebApp:{initData:'e2e',initDataUnsafe:{user:{id:1}},
          colorScheme:'dark',themeParams:{},platform:'android',version:'8.0',
          viewportHeight:innerHeight,viewportStableHeight:innerHeight,ready:noop,expand:noop,
          onEvent:noop,offEvent:noop,setHeaderColor:noop,setBackgroundColor:noop,setBottomBarColor:noop,
          BackButton:{show:noop,hide:noop,onClick:noop},HapticFeedback:{impactOccurred:noop}}};`,
      }));
      await page.goto('/dev-login/user');
      await page.goto('/telegram-app');
    }
    const response = await page.goto('/documentation');
    expect(response.status()).toBe(200);
    await expect(page.locator('html')).toHaveAttribute('data-theme', scenario.theme);
    await expect(page.locator('[data-docs-section]')).toHaveCount(12);
    await expect(page.locator('.docs-toc > a')).toHaveCount(12);
    await expect(page.locator('#docsSectionSelect option')).toHaveCount(12);
    const images = page.locator('.docs-screenshot img');
    await images.evaluateAll(nodes => nodes.forEach(image => { image.loading = 'eager'; }));
    await expect.poll(() => images.evaluateAll(nodes => nodes.every(image => image.complete && image.naturalWidth > 0))).toBe(true);
    expect(await images.evaluateAll(nodes => nodes.every(image => {
      const bounds = image.getBoundingClientRect();
      return image.getAttribute('width') && image.getAttribute('height') &&
        Math.abs(bounds.width / bounds.height - image.naturalWidth / image.naturalHeight) < .02;
    }))).toBe(true);
    const links = page.locator('.docs-screenshot > a');
    await expect(links).toHaveCount(await images.count());
    for (const href of await links.evaluateAll(nodes => nodes.map(node => node.href))) {
      expect(href).toContain('guide-20261103');
      expect((await page.request.get(href)).status()).toBe(200);
    }
    if (scenario.width >= 1024) {
      const search = page.getByRole('searchbox', { name: 'Поиск по документации' });
      await search.fill('номера кузова');
      await expect(page.locator('#vehicles')).toBeVisible();
      await expect(page.locator('#dashboard')).toBeHidden();
      await search.fill('нет_такого_раздела_123');
      await expect(page.locator('#docsEmptySearch')).toBeVisible();
      await search.press('Escape');
      await expect(page.locator('#dashboard')).toBeVisible();
      await page.locator('.docs-toc a[href="#profile"]').click();
    } else await page.locator('#docsSectionSelect').selectOption('#profile');
    await expect(page.locator('#profile')).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('button', { name: 'Почему вместо сумм показаны ••••?' }).click();
    await expect(page.locator('#faqPrivacy')).toBeVisible();
    await expect(page.locator('#faqPrivacy')).toContainText('Показать суммы');
    // WebKit limits raster dimensions; image and section geometry are checked above.
    await page.screenshot({ path: info.outputPath('documentation-current.png'), fullPage: false });
    expect(errors).toEqual([]);
  });
}
