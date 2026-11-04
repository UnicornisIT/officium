const { test, expect } = require('@playwright/test');
const { seedPersonalAccount } = require('./helpers/personal-account');

for (const mode of [
  { width: 1440, theme: 'light', mini: false },
  { width: 390, theme: 'dark', mini: false },
  { width: 390, theme: 'light', mini: true },
  { width: 390, theme: 'dark', mini: true },
]) {
  test(`personal settings ${mode.width} ${mode.theme} mini=${mode.mini}`, async ({ page }, info) => {
    test.setTimeout(120_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize({ width: mode.width, height: 1000 });
    await page.addInitScript(theme => localStorage.setItem('officiumTheme', theme), mode.theme);
    await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
      contentType: 'application/javascript', body: mode.mini ? `
        const noop = () => {};
        window.Telegram = { WebApp: {
          initData:'e2e', initDataUnsafe:{user:{id:1}}, platform:'android', version:'8.0',
          colorScheme:${JSON.stringify(mode.theme)}, themeParams:{},
          viewportHeight:innerHeight, viewportStableHeight:innerHeight,
          ready:noop, expand:noop, onEvent:noop, offEvent:noop,
          setHeaderColor:noop, setBackgroundColor:noop, setBottomBarColor:noop,
          BackButton:{show:noop,hide:noop,onClick:noop}, HapticFeedback:{impactOccurred:noop}
        }};` : '',
    }));
    await page.goto('/dev-login/user');
    seedPersonalAccount();
    if (mode.mini) await page.goto('/telegram-app');
    await page.goto('/profile');
    if (mode.mini) await expect(page.locator('body')).toHaveClass(/telegram-mini-app/);
    const notificationPanel = page.getByRole('region', { name: 'Уведомления', exact: true });
    const notifications = notificationPanel.getByRole('region', { name: 'Напоминания об автомобилях', exact: true });
    const privacy = page.getByRole('region', { name: 'Приватность', exact: true });
    await expect(page.getByRole('region', { name: 'Аккаунт', exact: true })).toContainText('Вход через Telegram');
    await expect(page.locator('.profile-settings-card')).toHaveCount(3);
    const monthly = notificationPanel.getByRole('region', { name: 'Напоминания о ежемесячных расходах', exact: true });
    expect(await notificationPanel.locator(':scope > section').evaluateAll(elements => elements.map(element => element.getAttribute('aria-labelledby')))).toEqual(['monthlyRemindersTitle', 'vehicleRemindersTitle']);
    await expect(notificationPanel.locator(':scope > hr')).toHaveCount(1);
    await monthly.getByLabel('Напоминать', { exact: true }).selectOption('3');
    await monthly.getByLabel('Время напоминания', { exact: true }).fill('11:25');
    await monthly.getByLabel('Часовой пояс', { exact: true }).selectOption('Europe/Moscow');
    await monthly.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(monthly.getByRole('status')).toHaveText('Сохранено');
    await page.reload();
    await expect(monthly.getByLabel('Напоминать', { exact: true })).toHaveValue('3');
    await expect(monthly.getByLabel('Время напоминания', { exact: true })).toHaveValue('11:25');
    await notifications.getByLabel('Часовой пояс', { exact: true }).selectOption('Asia/Vladivostok');
    await notifications.getByLabel('Время доставки', { exact: true }).fill('08:35');
    await notifications.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(notifications.locator('.alert-success')).toHaveText('Сохранено');
    await privacy.getByRole('checkbox', { name: 'Скрывать суммы при открытии' }).check();
    await privacy.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(privacy.getByRole('status')).toHaveText('Сохранено');
    await expect(notifications.getByLabel('Время доставки', { exact: true })).toHaveValue('08:35');
    await expect(notifications.getByLabel('Часовой пояс', { exact: true })).toHaveValue('Asia/Vladivostok');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: info.outputPath('profile.png'), fullPage: true });
    await page.setViewportSize({ width: mode.width, height: 2400 });
    if (mode.mini) await page.evaluate(() => {
      window.Telegram.WebApp.viewportHeight = innerHeight;
      window.Telegram.WebApp.viewportStableHeight = innerHeight;
      window.dispatchEvent(new Event('resize'));
    });
    await notificationPanel.screenshot({ path: info.outputPath('notifications.png') });
    await page.setViewportSize({ width: mode.width, height: 1000 });

    const response = await page.goto('/expenses');
    expect(await response.text()).toContain('class="amounts-hidden amounts-pending"');
    await expect(page.locator('html')).not.toHaveClass(/amounts-pending/);
    const value = page.locator('[data-private-money] .money-value').filter({ hasText: /987/ }).first();
    await expect(value).toBeHidden();
    const amountInput = page.locator('input[name="amount"]').first();
    await amountInput.fill('123,45');
    await page.evaluate(() => {
      const example = document.createElement('div');
      example.id = 'dynamic-money-example';
      example.textContent = 'Динамическая сумма 12 345 ₽';
      example.title = 'Подсказка 12 345 ₽';
      example.setAttribute('aria-label', 'Сумма 12 345 ₽');
      document.querySelector('main').append(example);
    });
    const dynamic = page.locator('#dynamic-money-example');
    await expect(dynamic.locator('.money-value')).toBeHidden();
    await expect(dynamic).toHaveAttribute('title', 'Подсказка ••••');
    await expect(dynamic).toHaveAttribute('aria-label', 'Сумма ••••');
    await dynamic.evaluate(element => {
      bootstrap.Tooltip.getOrCreateInstance(element, { trigger: 'manual', animation: false }).show();
    });
    await expect(page.locator('.tooltip-inner')).toHaveText('Подсказка ••••', { useInnerText: true });
    const reveal = page.locator('#amountPrivacyToggle');
    await reveal.focus();
    await page.keyboard.press('Enter');
    await expect(value).toBeVisible();
    await expect(amountInput).toHaveValue('123,45');
    await expect(dynamic).toHaveAttribute('data-bs-original-title', 'Подсказка 12 345 ₽');
    await dynamic.evaluate(element => bootstrap.Tooltip.getInstance(element).show());
    await expect(page.locator('.tooltip-inner')).toHaveText('Подсказка 12 345 ₽', { useInnerText: true });
    await page.keyboard.press('Enter');
    await expect(value).toBeHidden();
    await dynamic.evaluate(element => bootstrap.Tooltip.getInstance(element).dispose());
    // New content while hidden must be masked, including a newly added canvas.
    await page.evaluate(() => {
      const canvas = document.createElement('canvas');
      canvas.id = 'dynamic-finance-chart';
      document.querySelector('main').append(canvas);
    });
    await expect(page.locator('#dynamic-finance-chart')).toBeHidden();
    await expect(page.locator('#dynamic-finance-chart')).toHaveAttribute('aria-hidden', 'true');
    await reveal.click();
    await expect(page.locator('#dynamic-finance-chart')).toBeVisible();
    await page.reload();
    await expect(value).toBeHidden();
    await page.goto('/vehicles/tco');
    const canvas = page.locator('canvas').first();
    await expect(canvas).toBeHidden();
    const csv = await page.request.get('/vehicles/tco?format=csv');
    expect(csv.ok()).toBe(true);
    expect(await csv.text()).not.toMatch(/••••|money-value|data-private-money/);
    await page.locator('#amountPrivacyToggle').click();
    await expect(canvas).toBeVisible();
    await page.goto('/profile');
    await privacy.getByRole('checkbox', { name: 'Скрывать суммы при открытии' }).uncheck();
    await privacy.getByRole('button', { name: 'Сохранить', exact: true }).click();
    await expect(privacy.getByRole('status')).toHaveText('Сохранено');
    await expect(privacy.getByRole('checkbox')).not.toBeChecked();
    await page.goto('/expenses');
    await expect(value).toBeVisible();
    expect(errors).toEqual([]);
  });
}
