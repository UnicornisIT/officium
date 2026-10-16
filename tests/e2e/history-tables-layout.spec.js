const { test, expect, devices } = require('@playwright/test');

// Regression for history tables that wrapped dates and headers mid-word:
// "28.10.202" / "6" in the payment history modal, "КОММЕНТАР" / "ИЙ" in the
// header, and "Накопления и цели" split over two lines in the expense groups.

const TELEGRAM_SCRIPT = 'https://telegram.org/js/telegram-web-app.js';
const telegramStub = `
  const noop = () => {};
  window.Telegram = { WebApp: {
    initData: 'query_id=e2e&user=%7B%22id%22%3A1%7D&auth_date=1&hash=e2e',
    initDataUnsafe: { user: { id: 1, first_name: 'E2E' } },
    platform: 'android', version: '8.0', colorScheme: 'dark', themeParams: {},
    viewportHeight: window.innerHeight, viewportStableHeight: window.innerHeight, isExpanded: true,
    ready: noop, expand: noop, onEvent: noop, offEvent: noop,
    setHeaderColor: noop, setBackgroundColor: noop, setBottomBarColor: noop,
    BackButton: { show: noop, hide: noop, onClick: noop },
    HapticFeedback: { impactOccurred: noop },
  } };
`;

const LONG_COMMENT = 'Очень длинный комментарий к платежу, который не должен растягивать таблицу и ломать вёрстку модалки';

async function startSession(page, { miniApp, theme }) {
  if (miniApp) {
    await page.route(TELEGRAM_SCRIPT, route => route.fulfill({
      status: 200, contentType: 'application/javascript', body: telegramStub,
    }));
  }
  await page.goto('/dev-login/user');
  if (miniApp) await page.goto('/telegram-app');
  await page.goto('/');
  await page.evaluate(value => document.documentElement.setAttribute('data-theme', value), theme);
}

async function api(page, url, body) {
  return page.evaluate(async ({ url, body }) => {
    const token = document.querySelector('meta[name="csrf-token"]').content;
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': token },
      body: JSON.stringify(body),
    });
    return response.json();
  }, { url, body });
}

// Every cell keeps its text on one line when its rendered height is about
// one line-height (allowing for padding).
async function singleLine(locator) {
  return locator.evaluate(element => {
    const style = getComputedStyle(element);
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.3;
    const range = document.createRange();
    range.selectNodeContents(element);
    const rects = [...range.getClientRects()].filter(rect => rect.width > 0);
    const lines = new Set(rects.map(rect => Math.round(rect.top)));
    return { lines: lines.size, lineHeight };
  });
}

const { defaultBrowserType: _iphoneBrowser, ...iPhone13 } = devices['iPhone 13'];
const phone375 = { ...iPhone13, viewport: { width: 375, height: 812 } };

const scenarios = [
  { name: 'desktop light', device: null, miniApp: false, theme: 'light' },
  { name: 'desktop dark', device: null, miniApp: false, theme: 'dark' },
  { name: '375px light', device: phone375, miniApp: false, theme: 'light' },
  { name: 'Telegram Mini App 375px dark', device: phone375, miniApp: true, theme: 'dark' },
];

for (const scenario of scenarios) {
  test.describe(scenario.name, () => {
    if (scenario.device) test.use(scenario.device);

    test('payment history and expense groups do not wrap mid-word', async ({ page, browserName }, testInfo) => {
      test.skip(Boolean(scenario.device) && browserName === 'firefox', 'Firefox has no mobile emulation');
      await startSession(page, scenario);
      const suffix = `${testInfo.project.name}-${Date.now()}`;

      const created = await api(page, '/api/debts', {
        bank_name: 'E2E Банк', debt_type: 'credit_card', product_name: `Карта ${suffix}`,
        total_amount: '150000', remaining_amount: '125000.55',
      });
      expect(created.success, JSON.stringify(created)).toBe(true);
      const debtId = created.debt?.id ?? created.id;
      const paid = await api(page, `/api/debts/${debtId}/payments`, {
        amount: '12345.67', payment_date: '2025-10-28', comment: LONG_COMMENT,
      });
      expect(paid.success, JSON.stringify(paid)).toBe(true);

      await page.goto('/');
      await page.evaluate(id => openHistoryModal(id, 'E2E'), debtId);
      const table = page.locator('#historyContent .history-table');
      await expect(table).toBeVisible();

      for (const header of await table.locator('th').all()) {
        expect((await singleLine(header)).lines, `header "${await header.textContent()}" wraps`).toBeLessThanOrEqual(1);
      }
      const row = table.locator('tbody tr').first();
      await expect(row.locator('.payment-history-date')).toHaveText('28.10.2025');
      expect((await singleLine(row.locator('.payment-history-date'))).lines).toBe(1);
      expect((await singleLine(row.locator('.payment-history-remaining'))).lines).toBe(1);

      const comment = row.locator('.payment-history-comment .history-comment');
      await expect(comment).toHaveAttribute('title', LONG_COMMENT);
      const overflow = await comment.evaluate(element => ({
        ellipsis: getComputedStyle(element).textOverflow,
        clipped: element.scrollWidth > element.clientWidth,
      }));
      expect(overflow).toEqual({ ellipsis: 'ellipsis', clipped: true });
      await page.screenshot({ path: testInfo.outputPath('payment-history.png') });

      await page.keyboard.press('Escape');
      await page.goto('/expenses');
      const form = page.locator('form[action$="/expenses"]');
      await form.locator('select[name="category"]').selectOption('savings');
      await form.locator('input[name="title"]').fill(`Отложить ${suffix}`);
      await form.locator('input[name="amount"]').fill('5000');
      await form.locator('input[name="expense_date"]').fill('2025-10-28');
      await form.getByRole('button', { name: 'Сохранить расход' }).click();
      await page.waitForURL(url => url.pathname === '/expenses' && url.searchParams.get('month') === '2025-10');

      const expenseRow = page.locator('#collapse-2025-10 tr', { hasText: `Отложить ${suffix}` });
      await expenseRow.scrollIntoViewIfNeeded();
      const category = expenseRow.locator('.history-category');
      await expect(category).toHaveText('Накопления и цели');
      expect((await singleLine(category)).lines).toBe(1);
      expect((await singleLine(expenseRow.locator('.history-date'))).lines).toBe(1);
      for (const header of await page.locator('#collapse-2025-10 th').all()) {
        expect((await singleLine(header)).lines, `header "${await header.textContent()}" wraps`).toBeLessThanOrEqual(1);
      }
      await page.screenshot({ path: testInfo.outputPath('expenses.png'), fullPage: true });
    });
  });
}
