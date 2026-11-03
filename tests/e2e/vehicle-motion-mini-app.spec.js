const { test, expect } = require('@playwright/test');

// Exercises the actual Mini App session and touch UI with a local Telegram SDK
// stub. This covers Chromium/WebKit, not native Telegram clients on devices.
test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

for (const theme of ['light', 'dark']) {
  test(`Telegram vehicle and Modules motion ${theme}`, async ({ page, browserName }) => {
    test.setTimeout(120_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const settings = { platform: browserName === 'webkit' ? 'ios' : 'android', colorScheme: theme,
      themeParams: { bg_color: theme === 'dark' ? '#17212b' : '#ffffff',
        secondary_bg_color: theme === 'dark' ? '#232e3c' : '#f1f1f4',
        text_color: theme === 'dark' ? '#f5f5f5' : '#111111', button_color: '#5288c1' } };
    await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
      contentType: 'application/javascript', body: `
        const noop = () => {};
        window.Telegram = { WebApp: { ...${JSON.stringify(settings)},
          initData: 'e2e', initDataUnsafe: {user: {id: 1}}, version: '8.0',
          viewportHeight: innerHeight, viewportStableHeight: innerHeight,
          ready: noop, expand: noop, onEvent: noop, offEvent: noop,
          setHeaderColor: noop, setBackgroundColor: noop, setBottomBarColor: noop,
          BackButton: {show: noop, hide: noop, onClick: noop},
          HapticFeedback: {impactOccurred: noop}
        }};`,
    }));
    await page.goto('/dev-login/user');
    await page.goto('/telegram-app');
    await page.goto('/vehicles');
    await expect(page.locator('body')).toHaveClass(/telegram-mini-app/);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await page.getByRole('button', { name: 'Открыть меню', exact: true }).tap();
    const sheet = page.locator('#telegramMoreMenu');
    await expect(sheet).toHaveClass(/show/);
    const modules = sheet.locator('[aria-controls="telegramModules"]');
    const modulePanel = page.locator('#telegramModules');
    await modulePanel.evaluate(panel => {
      for (const event of ['show.bs.collapse', 'hide.bs.collapse']) {
        panel.addEventListener(event, () => requestAnimationFrame(() => {
          panel.dataset[event.startsWith('show') ? 'openDuration' : 'closeDuration'] =
            getComputedStyle(panel).transitionDuration;
        }));
      }
    });
    await modules.tap();
    await expect(modulePanel).toHaveAttribute('data-open-duration', '0.26s');
    await expect(modulePanel).toHaveClass(/show/);
    await expect(modulePanel.getByRole('link', { name: 'Автомобили', exact: true })).toBeVisible();
    await modules.tap();
    await expect(modulePanel).toHaveAttribute('data-close-duration', '0.26s');
    await expect(modulePanel).toBeHidden();
    await modules.tap();
    await expect(modulePanel).toHaveClass(/show/);
    await modulePanel.getByRole('link', { name: 'Автомобили', exact: true }).tap();
    await page.getByRole('link', { name: 'Добавить автомобиль', exact: true }).tap();
    const palette = page.locator('.vehicle-color-toggle');
    await palette.tap();
    await expect(page.locator('.vehicle-color-panel')).toBeVisible();
    await palette.tap();
    await expect(page.locator('.vehicle-color-panel')).toBeHidden();
    await page.getByLabel('Название', { exact: true }).fill(`Mini App ${theme} ${Date.now()}`);
    await page.getByRole('button', { name: 'Сохранить автомобиль' }).tap();
    const nav = page.getByRole('navigation', { name: 'Вкладки автомобиля' });
    const more = nav.getByRole('button', { name: 'Ещё', exact: true });
    await more.evaluate(button => {
      for (const event of ['shown.bs.dropdown', 'hide.bs.dropdown']) {
        button.addEventListener(event, () => {
          button.dataset[event.startsWith('shown') ? 'openAnimations' : 'closeAnimations'] =
            document.getElementById('vehicleMoreMenu').getAnimations().length;
        });
      }
    });
    await more.tap();
    await expect(more).toHaveAttribute('data-open-animations', '1');
    await page.locator('h1').tap();
    await expect(more).toHaveAttribute('data-close-animations', '1');
    await expect(more).toHaveAttribute('aria-expanded', 'false');
    await more.tap();
    await nav.getByRole('link', { name: 'Документы', exact: true }).tap();
    await expect(page).toHaveURL(/tab=documents$/);
    await expect(more).toHaveClass(/active/);
    await expect(page.getByRole('heading', { name: 'Документы автомобиля' })).toBeVisible();
    for (const width of [360, 430]) {
      await page.setViewportSize({ width, height: 844 });
      await more.tap();
      const bounds = await page.locator('#vehicleMoreMenu').boundingBox();
      expect(bounds.x).toBeGreaterThanOrEqual(0);
      expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
      await page.locator('h1').tap();
      await expect(more).toHaveAttribute('aria-expanded', 'false');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    }
    const summary = page.locator('.vehicle-archive-disclosure > summary');
    await summary.tap();
    await expect(summary).toHaveAttribute('aria-expanded', 'true');
    await expect(page.locator('.vehicle-archive-disclosure')).not.toHaveClass(/vehicle-disclosure-moving/);
    await page.locator('#archiveReasonToggle').tap();
    await page.locator('#archiveReasonMenu').getByRole('button', { name: 'Другое', exact: true }).tap();
    await expect(page.locator('#archiveReason')).toHaveValue('other');
    await summary.tap();
    await expect(page.locator('.vehicle-archive-disclosure > form')).toBeHidden();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await summary.tap();
    expect(await page.locator('.vehicle-archive-disclosure').evaluate(element => element.getAnimations().length)).toBe(0);
    await page.locator('#archiveReasonToggle').tap();
    expect(await page.locator('#archiveReasonMenu').evaluate(element => element.getAnimations().length)).toBe(0);
    // Dismiss via an uncovered area; the open popup intentionally overlays the note.
    await page.locator('h1').tap();
    await expect(page.locator('#archiveReasonToggle')).toHaveAttribute('aria-expanded', 'false');
    await more.tap();
    expect(await page.locator('#vehicleMoreMenu').evaluate(element => element.getAnimations().length)).toBe(0);
    await page.locator('h1').tap();
    await expect(more).toHaveAttribute('aria-expanded', 'false');
    expect(errors).toEqual([]);
  });
}
