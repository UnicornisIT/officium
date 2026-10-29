const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

// Only fictional data, in the disposable database from scripts/e2e_server.py.
for (const width of [1440, 390]) {
  for (const theme of ['light', 'dark']) {
    test(`vehicle core ${width}px ${theme}`, async ({ page }, testInfo) => {
      test.setTimeout(300_000);
      await page.setViewportSize({ width, height: 1000 });
      await page.addInitScript(value => localStorage.setItem('officiumTheme', value), theme);
      if (width === 390) {
        await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
          contentType: 'application/javascript', body: `
            const noop = () => {};
            window.Telegram = { WebApp: {
              initData: 'e2e', initDataUnsafe: { user: { id: 1 } },
              platform: 'android', version: '8.0', colorScheme: '${theme}', themeParams: {},
              viewportHeight: window.innerHeight, viewportStableHeight: window.innerHeight,
              ready: noop, expand: noop, onEvent: noop, offEvent: noop,
              setHeaderColor: noop, setBackgroundColor: noop, setBottomBarColor: noop,
              BackButton: { show: noop, hide: noop, onClick: noop },
              HapticFeedback: { impactOccurred: noop },
            } };
          `,
        }));
      }
      await page.goto('/dev-login/user');
      if (width === 390) await page.goto('/telegram-app');
      await page.goto('/expenses');
      await expect(page.locator('#expenseVehicle')).toHaveCount(0);
      const prefix = `Учебный ${width} ${theme} ${testInfo.project.name} попытка ${testInfo.retry}`;
      const root = process.env.VEHICLE_CAPTURE_DIR || testInfo.outputPath('captures');
      fs.mkdirSync(root, { recursive: true });
      const capture = async name => {
        await page.evaluate(() => document.fonts.ready);
        await page.evaluate(() => { document.activeElement.blur(); document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0); });
        await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
        await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
        const file = path.join(root, `${width}-${theme}-${name}.png`);
        await page.screenshot({ path: file, fullPage: true, animations: 'disabled' });
        await testInfo.attach(name, { path: file, contentType: 'image/png' });
      };
      const saveVehicle = async () => {
        await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
        await page.waitForURL(/\/vehicles\/\d+$/);
        return page.url();
      };

      await page.goto('/vehicles/new');
      await page.locator('.vehicle-color-toggle').click();
      const preview = page.locator('#vehiclePreview');
      await page.getByLabel('Цвет', { exact: true }).fill('#dc3545');
      await expect(preview).toHaveCSS('color', 'rgb(220, 53, 69)');
      const redPreview = await preview.screenshot({ animations: 'disabled' });
      await page.getByLabel('Цвет', { exact: true }).fill('#2e9f62');
      await expect(preview).toHaveCSS('color', 'rgb(46, 159, 98)');
      await expect.poll(async () => (await preview.screenshot({ animations: 'disabled' })).equals(redPreview)).toBe(false);
      await page.getByLabel('Цвет', { exact: true }).fill('#5276ba');
      await page.getByLabel('Название', { exact: true }).fill(`${prefix} · Седан`);
      await page.getByLabel('Марка', { exact: true }).fill('Пример');
      await page.getByLabel('Модель', { exact: true }).fill('Север');
      await page.getByLabel('Страна учёта', { exact: true }).selectOption({ label: 'Россия (RU)' });
      await page.getByLabel('Госномер', { exact: true }).fill('d123bc77');
      await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
      await expect(page.locator('.alert-danger:visible')).toContainText('Номер РФ');
      await page.getByLabel('Госномер', { exact: true }).fill('a123bc77');
      const carURL = await saveVehicle();
      await expect(page.locator('.vehicle-visual text').first()).toHaveText('А123ВС77');
      await page.getByRole('link', { name: 'Пробег', exact: true }).click();
      await page.getByLabel('Дата замера', { exact: true }).fill('2020-01-01');
      await page.getByLabel('Пробег (км)', { exact: true }).fill('12300');
      await page.getByRole('button', { name: 'Добавить замер', exact: true }).click();
      await expect(page.locator('tbody')).toContainText('12 300');
      await page.getByLabel('Дата замера', { exact: true }).fill('2020-01-02');
      await page.getByLabel('Пробег (км)', { exact: true }).fill('12200');
      await page.getByRole('button', { name: 'Добавить замер', exact: true }).click();
      await expect(page.locator('.alert-danger:visible')).toContainText('меньше замера');
      await capture('mileage');
      await page.goto(carURL);
      await page.getByRole('link', { name: 'Добавить расход на авто', exact: true }).click();
      const expenseForm = page.locator('form[action$="/expenses"]');
      await expect(expenseForm.locator('[name="vehicle_id"]')).not.toHaveValue('');
      await expect(expenseForm.locator('[name="category"]')).toHaveValue('transport');
      const expenseTitle = `Тестовое обслуживание ${prefix}`;
      await expenseForm.locator('[name="title"]').fill(expenseTitle);
      await expenseForm.locator('[name="amount"]').fill('1234.50');
      await expenseForm.locator('[name="expense_date"]').fill('2026-10-15');
      await capture('expense-form');
      await expenseForm.getByRole('button', { name: 'Сохранить расход', exact: true }).click();
      await page.waitForURL(/month=2026-10/);
      const row = page.locator('tr', { hasText: expenseTitle });
      await expect(row).toContainText('Транспорт');
      const editExpenseURL = await row.getByRole('link', { name: 'Редактировать', exact: true }).getAttribute('href');
      await page.goto(carURL);
      await expect(page.locator('tbody')).toContainText(expenseTitle);
      await capture('passenger-ru');

      await page.goto('/vehicles/new');
      await page.getByLabel('Название', { exact: true }).fill(`${prefix} · Тягач`);
      await page.getByLabel('Тип', { exact: true }).selectOption('tractor_unit');
      await page.getByLabel('Название прицепа', { exact: true }).fill(`${prefix} · Прицеп`);
      await page.getByLabel('Страна учёта', { exact: true }).selectOption({ label: 'Абхазия (AB)' });
      await page.getByLabel('Госномер', { exact: true }).fill('ab 123');
      await page.locator('.vehicle-color-toggle').click();
      await page.getByLabel('Цвет', { exact: true }).fill('#d59038');
      const tractorURL = await saveVehicle();
      await expect(page.locator('.vehicle-trailer')).toContainText(`${prefix} · Прицеп`);
      await expect(page.locator('.vehicle-visual text').first()).toHaveText('AB 123');
      await capture('tractor-ab-trailer');

      await page.goto('/vehicles?q=' + encodeURIComponent(prefix));
      await expect(page.locator('.vehicle-card')).toHaveCount(3);
      await capture('list');
      const firstVehicleCard = page.locator('.vehicle-card').first();
      const detailPath = await firstVehicleCard.locator('h2 a').getAttribute('href');
      await firstVehicleCard.locator('.vehicle-image-link').click();
      await expect(page).toHaveURL(new URL(detailPath, carURL).href);
      await page.goto('/');
      await expect(page.locator('.mileage-reminder')).toContainText('Обновите пробег');
      await capture('reminder');

      await page.goto(carURL);
      await page.getByText('Архивировать автомобиль', { exact: true }).click();
      await page.getByRole('button', { name: 'В архив', exact: true }).click();
      await expect(page.locator('body')).toContainText('Только просмотр');
      await expect(page.getByRole('link', { name: 'Редактировать', exact: true })).toHaveCount(0);
      await capture('archived');
      await page.goto(editExpenseURL);
      await page.locator('[name="amount"]').fill('9999');
      await page.getByRole('button', { name: 'Сохранить изменения', exact: true }).click();
      await expect(page.locator('.alert-danger:visible')).toContainText('Автомобиль в архиве');
      await page.goto(carURL);
      await page.getByRole('button', { name: 'Восстановить автомобиль', exact: true }).click();
      await expect(page.getByRole('link', { name: 'Редактировать', exact: true })).toBeVisible();
      await page.goto('/vehicles');
      await page.getByRole('button', { name: 'Напомнить через 3 дня', exact: true }).click();
      await expect(page.locator('.mileage-reminder')).toHaveCount(0);
      await page.goto(tractorURL);
    });
  }
}
