const { execFileSync } = require('node:child_process');
const os = require('node:os');
const path = require('node:path');
const { test, expect, devices } = require('@playwright/test');

// Regression for "pick a future expense date in the Telegram Mini App and
// nothing happens": the expense was saved, but the redirect expanded the
// current month and left the month of the new record collapsed.
//
// Playwright cannot reproduce the real Telegram WebView (native Android/iOS
// date pickers, the Telegram Web iframe); window.Telegram.WebApp is a stub.

const TELEGRAM_SCRIPT = 'https://telegram.org/js/telegram-web-app.js';
const DATABASE_PATH = path.join(os.tmpdir(), 'officium-playwright.sqlite');

const telegramStub = `
  window.__telegramCalls = [];
  const noop = name => (...args) => window.__telegramCalls.push([name, ...args]);
  window.Telegram = { WebApp: {
    initData: 'query_id=e2e&user=%7B%22id%22%3A1%7D&auth_date=1&hash=e2e',
    initDataUnsafe: { user: { id: 1, first_name: 'E2E' } },
    platform: 'android',
    version: '8.0',
    colorScheme: 'light',
    themeParams: { bg_color: '#ffffff', secondary_bg_color: '#f1f1f4', text_color: '#111111',
      hint_color: '#8a8a8f', link_color: '#2481cc', button_color: '#2481cc',
      button_text_color: '#ffffff', header_bg_color: '#ffffff' },
    viewportHeight: window.innerHeight,
    viewportStableHeight: window.innerHeight,
    isExpanded: true,
    ready: noop('ready'),
    expand: noop('expand'),
    onEvent: noop('onEvent'),
    offEvent: noop('offEvent'),
    setHeaderColor: noop('setHeaderColor'),
    setBackgroundColor: noop('setBackgroundColor'),
    setBottomBarColor: noop('setBottomBarColor'),
    BackButton: { show: noop('BackButton.show'), hide: noop('BackButton.hide'), onClick: noop('BackButton.onClick') },
    HapticFeedback: { impactOccurred: noop('impactOccurred') },
  } };
`;

const { defaultBrowserType: _iphoneBrowser, ...iPhone13 } = devices['iPhone 13'];
const { defaultBrowserType: _pixelBrowser, ...pixel7 } = devices['Pixel 7'];

function localIsoDate(value) {
  const pad = number => String(number).padStart(2, '0');
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;
}

// The 5th of next month: 2026-11-05 when run on 2026-10-15, and still a
// future month that differs from the current one whenever CI runs it.
function futureExpenseDate() {
  const today = new Date();
  return localIsoDate(new Date(today.getFullYear(), today.getMonth() + 1, 5));
}

function savedExpenseDates(title) {
  const script = [
    'import sqlite3, sys',
    'rows = sqlite3.connect(sys.argv[1]).execute(',
    '    "SELECT expense_date FROM expenses WHERE title = ?", (sys.argv[2],)).fetchall()',
    'print("\\n".join(str(row[0]) for row in rows))',
  ].join('\n');
  const output = execFileSync(process.env.PYTHON || 'python', ['-c', script, DATABASE_PATH, title], {
    encoding: 'utf-8',
  });
  return output.split(/\r?\n/).filter(Boolean);
}

async function startSession(page, { miniApp }) {
  if (miniApp) {
    await page.route(TELEGRAM_SCRIPT, route => route.fulfill({
      status: 200, contentType: 'application/javascript', body: telegramStub,
    }));
  }
  await page.goto('/dev-login/user');
  if (miniApp) {
    // Same entry point the bot button opens; it flags the session as Mini App.
    await page.goto('/telegram-app');
  }
  await page.goto('/expenses');
  await expect(page.locator('body')).toHaveClass(miniApp ? /telegram-mini-app/ : /has-mobile-app-nav/);
}

async function addExpense(page, { title, isoDate }) {
  const form = page.locator('form[action$="/expenses"]');
  await form.locator('select[name="category"]').selectOption('products');
  await form.locator('input[name="title"]').fill(title);
  await form.locator('input[name="amount"]').fill('1250');
  await form.locator('input[name="expense_date"]').fill(isoDate);
  await form.getByRole('button', { name: 'Сохранить расход' }).click();
  // The current page is already loaded, so waitForLoadState() would return
  // before the redirect; wait for the URL of the saved record's month.
  const month = isoDate.slice(0, 7);
  await page.waitForURL(url => url.pathname === '/expenses' && url.searchParams.get('month') === month);
}

async function expectSavedAndVisible(page, title, isoDate, testInfo) {
  const [year, month, day] = isoDate.split('-');
  expect(savedExpenseDates(title)).toEqual([isoDate]);

  const alert = page.locator('.alert-success', { hasText: 'Расход сохранён' });
  await expect(alert).toBeVisible();
  const placement = await alert.evaluate(element => {
    const rect = element.getBoundingClientRect();
    const bar = document.querySelector('.telegram-app-bar');
    const barRect = bar && getComputedStyle(bar).display !== 'none' ? bar.getBoundingClientRect() : null;
    return { top: rect.top, bottom: rect.bottom, barBottom: barRect ? barRect.bottom : 0, height: window.innerHeight };
  });
  expect(placement.top, 'success alert hidden under the app bar').toBeGreaterThanOrEqual(placement.barBottom - 1);
  expect(placement.bottom, 'success alert outside the viewport').toBeLessThanOrEqual(placement.height);

  const group = page.locator(`#collapse-${year}-${month}`);
  await expect(group).toHaveClass(/\bshow\b/);
  const row = group.locator('tr', { hasText: title });
  await row.scrollIntoViewIfNeeded();
  await expect(row).toBeVisible();
  await expect(row.locator('.history-date')).toHaveText(`${day}.${month}.${year}`);
  await page.screenshot({ path: testInfo.outputPath('after-save.png'), fullPage: true });
}

const scenarios = [
  { name: 'Telegram Mini App on iPhone 13', device: iPhone13, miniApp: true },
  { name: 'Telegram Mini App on Pixel 7', device: pixel7, miniApp: true },
  { name: 'mobile browser without Mini App on Pixel 7', device: pixel7, miniApp: false },
  { name: 'desktop browser without Mini App', device: null, miniApp: false },
];

for (const scenario of scenarios) {
  test.describe(scenario.name, () => {
    if (scenario.device) test.use(scenario.device);

    test('future expense date is saved and its month is shown', async ({ page, browserName }, testInfo) => {
      test.skip(Boolean(scenario.device) && browserName === 'firefox', 'Firefox has no mobile emulation');
      await startSession(page, scenario);
      const suffix = `${testInfo.project.name}-${testInfo.workerIndex}-${Date.now()}`;

      // A record in the current month makes the old "expand today" default
      // hide the future month.
      await addExpense(page, { title: `Текущий месяц ${suffix}`, isoDate: localIsoDate(new Date()) });

      const futureDate = futureExpenseDate();
      const title = `Запланированная трата ${suffix}`;
      await addExpense(page, { title, isoDate: futureDate });

      await expectSavedAndVisible(page, title, futureDate, testInfo);
    });
  });
}

test.describe('Telegram Mini App date field and keyboard handling', () => {
  test.use(pixel7);

  test('picker focus cycle keeps the value and the submit button usable', async ({ page, browserName }) => {
    test.skip(browserName === 'firefox', 'Firefox has no mobile emulation');
    await startSession(page, { miniApp: true });
    const viewport = page.viewportSize();
    const dateInput = page.locator('form[action$="/expenses"] input[name="expense_date"]');
    const submit = page.getByRole('button', { name: 'Сохранить расход' });
    const futureDate = futureExpenseDate();

    // Native pickers focus the input and resize the visual viewport while
    // open; emulate that with a shrunken viewport around the value change.
    await dateInput.focus();
    await page.setViewportSize({ width: viewport.width, height: Math.round(viewport.height * 0.55) });
    await dateInput.fill(futureDate);
    await page.waitForTimeout(400);
    await expect(page.locator('body')).toHaveClass(/keyboard-open/);
    await dateInput.blur();
    await page.setViewportSize(viewport);
    await page.waitForTimeout(400);

    await expect(dateInput).toHaveValue(futureDate);
    await expect(page.locator('body')).not.toHaveClass(/keyboard-open/);
    const inset = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--app-keyboard-inset').trim());
    expect(inset).toBe('0px');

    // The fixed bottom nav may overlap the button at some scroll offsets with
    // or without a picker; the user can scroll, so check it once centred.
    // Bootstrap enables smooth scrolling on :root, so scroll instantly.
    await submit.evaluate(button => button.scrollIntoView({ block: 'center', behavior: 'instant' }));
    await expect.poll(() => submit.evaluate(button => {
      const rect = button.getBoundingClientRect();
      const element = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
      if (element === button || button.contains(element)) return 'submit button';
      const name = element ? `${element.tagName.toLowerCase()}.${[...element.classList].join('.')}` : 'nothing';
      return `${name} at y=${Math.round(rect.top)} of ${window.innerHeight}`;
    }), { message: 'element on top of the submit button' }).toBe('submit button');
  });
});

test.describe('Telegram Mini App request failures', () => {
  test.use(pixel7);

  test('stale CSRF token shows a visible error and saves nothing', async ({ page, browserName }, testInfo) => {
    test.skip(browserName === 'firefox', 'Firefox has no mobile emulation');
    await startSession(page, { miniApp: true });
    const title = `CSRF ${testInfo.project.name}-${Date.now()}`;
    await page.locator('form[action$="/expenses"] input[name="csrf_token"]').evaluate(input => { input.value = 'invalid'; });

    await page.locator('form[action$="/expenses"] select[name="category"]').selectOption('products');
    await page.locator('form[action$="/expenses"] input[name="title"]').fill(title);
    await page.locator('form[action$="/expenses"] input[name="amount"]').fill('10');
    await page.locator('form[action$="/expenses"] input[name="expense_date"]').fill(futureExpenseDate());
    const [response] = await Promise.all([
      page.waitForResponse(candidate => candidate.request().method() === 'POST'),
      page.getByRole('button', { name: 'Сохранить расход' }).click(),
    ]);

    expect(response.status()).toBe(400);
    await expect(page.getByRole('heading', { name: 'Некорректный запрос' })).toBeVisible();
    expect(savedExpenseDates(title)).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath('csrf-error.png'), fullPage: true });
  });

  test('POST without the session cookie is rejected visibly', async ({ playwright, baseURL }) => {
    const anonymous = await playwright.request.newContext({ baseURL });
    const title = `No cookie ${Date.now()}`;
    const response = await anonymous.post('/expenses', {
      form: { csrf_token: 'lost', category: 'products', title, amount: '10', expense_date: futureExpenseDate() },
      maxRedirects: 0,
    });

    expect(response.status()).toBe(400);
    expect(await response.text()).toContain('Некорректный запрос');
    expect(savedExpenseDates(title)).toEqual([]);
    await anonymous.dispose();
  });
});
