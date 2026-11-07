const { test, expect } = require('@playwright/test');
let contactId;

test.beforeAll(async ({ browser, baseURL }) => {
  const page = await browser.newPage({ baseURL });
  await page.goto('/dev-login/superadmin');
  await page.goto('/admin/settings');
  const csrf = await page.locator('[name="csrf_token"]').first().inputValue();
  const response = await page.request.post('/admin/settings/contacts', { form: {
    csrf_token: csrf, action: 'save', type: 'email', label: 'Идеи', address: 'ideas@example.org',
    icon: 'envelope', published: '1', locations: 'footer', order: '0',
  } });
  expect(response.ok()).toBe(true);
  await page.goto('/admin/settings');
  contactId = await page.locator('.admin-contact-card').filter({ hasText: 'ideas@example.org' })
    .last().locator('[name="contact_id"]').inputValue();
  await page.close();
});

test.afterAll(async ({ browser, baseURL }) => {
  if (!contactId) return;
  const page = await browser.newPage({ baseURL });
  await page.goto('/dev-login/superadmin');
  await page.goto('/admin/settings');
  const csrf = await page.locator('[name="csrf_token"]').first().inputValue();
  await page.request.post('/admin/settings/contacts', { form: {
    csrf_token: csrf, action: 'delete', contact_id: contactId,
  } });
  await page.close();
});

for (const mode of ['desktop', 'mobile', 'mini-app', 'mini-app-reduced']) {
  test(`wishlist development dialog ${mode}`, async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: mode === 'desktop' ? 1440 : 390, height: 844 });
    await page.emulateMedia({ reducedMotion: mode.endsWith('reduced') ? 'reduce' : 'no-preference' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
      contentType: 'application/javascript', body: `
        const noop = () => {};
        window.Telegram = { WebApp: {
          platform: 'android', colorScheme: 'dark', themeParams: {},
          initData: 'e2e', initDataUnsafe: {user: {id: 1}}, version: '8.0',
          viewportHeight: innerHeight, viewportStableHeight: innerHeight,
          ready: noop, expand: noop, onEvent: noop, offEvent: noop,
          setHeaderColor: noop, setBackgroundColor: noop, setBottomBarColor: noop,
          BackButton: {show: noop, hide: noop, onClick: noop},
          HapticFeedback: {impactOccurred: noop}
        }};`,
    }));
    await page.goto('/dev-login/user');
    const mini = mode.startsWith('mini-app');
    if (mini) await page.goto('/telegram-app');
    await page.goto('/');
    const originalURL = page.url();
    if (mini) {
      await page.getByRole('button', { name: 'Открыть меню', exact: true }).click();
      await page.locator('#telegramMoreMenu').getByRole('button', { name: 'Модули', exact: true }).click();
    } else {
      if (mode === 'mobile') await page.locator('.navbar-toggler').click();
      const modules = page.getByRole('button', { name: 'Модули', exact: true });
      if (mode === 'desktop') await modules.hover();
      else await modules.click();
    }
    await page.getByRole('button', { name: 'Список желаний', exact: true }).click();
    const modal = page.getByRole('dialog', { name: 'Список желаний', exact: true });
    await expect(modal).toBeVisible();
    await expect(modal).toContainText('Сервис в разработке.');
    await expect(modal.getByRole('link', { name: 'Предложить идею', exact: true }))
      .toHaveAttribute('href', /^mailto:ideas@example\.org\?subject=/);
    await expect(page).toHaveURL(originalURL);
    if (mini) await expect(page.locator('#telegramMoreMenu')).toBeHidden();
    await page.screenshot({ path: testInfo.outputPath('wishlist.png') });
    await modal.getByRole('button', { name: 'Закрыть', exact: true }).click();
    await expect(modal).toBeHidden();
    await expect(page.locator('.modal-backdrop, .offcanvas-backdrop')).toHaveCount(0);
    await expect(page.locator(mini ? '.telegram-app-bar__menu' : '#modulesMenuToggle')).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(errors).toEqual([]);
  });
}
