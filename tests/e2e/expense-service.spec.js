const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

// BEFORE/AFTER captures use the same disposable database and fictional data.
for (const width of [1440, 390]) {
  for (const theme of ['light', 'dark']) {
    test(`expense service preserves ${width}px ${theme} screens`, async ({ page }, testInfo) => {
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
      if (width === 390) await expect(page.locator('body')).toHaveClass(/telegram-mini-app/);
      const form = page.locator('form[action$="/expenses"]');
      await form.locator('[name="category"]').selectOption('products');
      const suffix = testInfo.project.name === 'chromium' ? '' : ` ${testInfo.project.name}`;
      const title = `Вымышленная покупка ${width} ${theme}${suffix}`;
      await form.locator('[name="title"]').fill(title);
      await form.locator('[name="amount"]').fill('1250.25');
      await form.locator('[name="expense_date"]').fill('2026-10-15');
      await form.locator('[name="payment_method"]').selectOption('card');
      await form.locator('[name="comment"]').fill('Тестовые данные');
      const phase = process.env.EXPENSE_CAPTURE_PHASE || 'after';
      const root = process.env.EXPENSE_CAPTURE_DIR || testInfo.outputPath('captures');
      fs.mkdirSync(root, { recursive: true });
      const capture = async name => {
        await page.evaluate(() => document.fonts.ready);
        // Filling a low field scrolls the page; normalize fixed header placement.
        await page.evaluate(() => {
          document.activeElement.blur();
          document.documentElement.style.scrollBehavior = 'auto';
          window.scrollTo(0, 0);
        });
        await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
        const file = path.join(root, `${width}-${theme}-${name}-${phase}.png`);
        await page.screenshot({ path: file, fullPage: true, animations: 'disabled' });
        await testInfo.attach(`${name}-${phase}`, { path: file, contentType: 'image/png' });
        const before = file.replace(/-after\.png$/, '-before.png');
        if (phase === 'after' && fs.existsSync(before)) {
          expect(fs.readFileSync(file).equals(fs.readFileSync(before)), `${name} unchanged`).toBe(true);
        }
      };
      await capture('add-form');
      await form.getByRole('button', { name: 'Сохранить расход' }).click();
      await page.waitForURL(/month=2026-10/);
      await expect(page.locator('.alert-success')).toContainText('Расход сохранён');
      await capture('expenses');
      const row = page.locator('tr', { hasText: title });
      await expect(row).toBeVisible();
    });
  }
}

for (const monthly of [false, true]) {
  test(`web CRUD and private audit for ${monthly ? 'monthly' : 'regular'} expense`, async ({ page }) => {
    await page.goto('/dev-login/user');
    await page.goto('/expenses');
    const title = `Вымышленная проверка ${monthly ? 'ежемесячного' : 'обычного'} расхода`;
    const form = page.locator('form[action$="/expenses"]');
    await form.locator('[name="category"]').selectOption('transport');
    await form.locator('[name="title"]').fill(title);
    await form.locator('[name="amount"]').fill('76543.21');
    await form.locator('[name="expense_date"]').fill('2099-01-31');
    await form.locator('[name="comment"]').fill('Приватный вымышленный комментарий');
    await form.locator('[name="is_monthly"]').setChecked(monthly);
    await form.getByRole('button', { name: 'Сохранить расход' }).click();
    await page.waitForURL(/month=2099-01/);
    let row = page.locator('tr', { hasText: title });
    await row.getByRole('link', { name: 'Редактировать', exact: true }).click();
    const editForm = page.locator('form[action*="/expenses/edit/"]');
    await expect(editForm.locator('[name="is_monthly"]')).toBeChecked({ checked: monthly });
    await editForm.locator('[name="amount"]').fill('87654.32');
    await editForm.getByRole('button', { name: 'Сохранить изменения' }).click();
    await page.waitForURL(/month=2099-01/);
    row = page.locator('tr', { hasText: title });
    await expect(row.locator('.history-amount')).toContainText('87');
    page.once('dialog', dialog => dialog.accept());
    await row.getByRole('button', { name: 'Удалить', exact: true }).click();
    await expect(page.locator('.alert-success')).toContainText('Расход удалён');
    await expect(page.locator('tr', { hasText: title })).toHaveCount(0);
    await page.goto('/dev-login/superadmin');
    await page.goto('/admin/logs');
    await expect(page.locator('body')).toContainText('Добавлен расход');
    await expect(page.locator('body')).toContainText('Изменён расход');
    await expect(page.locator('body')).toContainText('Удалён расход');
    for (const secret of [title, '76543', '87654', '76 543', '87 654', 'Приватный вымышленный комментарий']) {
      await expect(page.locator('body')).not.toContainText(secret);
    }
  });
}
