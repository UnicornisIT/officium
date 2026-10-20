const { test, expect } = require('@playwright/test');

// Telegram itself is never contacted: telegram-widget.js is replaced by a
// stand-in that records Telegram.Login.auth() calls and opens a blank window
// the way the real SDK opens oauth.telegram.org. This checks our wiring only;
// the real Telegram window needs a live bot and is verified by hand.
const SDK_PATTERN = 'https://telegram.org/js/telegram-widget.js*';
const SDK_STUB = `
  window.__tgAuthCalls = [];
  window.Telegram = window.Telegram || {};
  window.Telegram.Login = {
    init: function () {},
    auth: function (options, callback) {
      window.__tgAuthCalls.push(options);
      window.__tgAuthCallback = callback;
      window.__tgPopup = window.open('about:blank', 'telegram_oauth_bot' + options.bot_id, 'width=550,height=470');
    },
  };
`;

async function stubSdk(page) {
  await page.route(SDK_PATTERN, (route) => route.fulfill({ contentType: 'application/javascript', body: SDK_STUB }));
}

async function openLogin(page) {
  await page.goto('/login', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.Telegram && window.Telegram.Login && window.__tgAuthCalls);
}

const blueButton = (page) => page.locator('#telegram-login-loader');
const headerButton = (page) => page.locator('nav a.nav-link[href="/login"][data-telegram-login-trigger]');
const authCalls = (page) => page.evaluate(() => window.__tgAuthCalls.length);

test('one click on the blue button opens the Telegram window directly', async ({ page }) => {
  await stubSdk(page);
  await openLogin(page);
  const box = await blueButton(page).boundingBox();

  const [popup] = await Promise.all([page.waitForEvent('popup'), blueButton(page).click()]);

  expect(popup).toBeTruthy();
  expect(await page.evaluate(() => window.__tgAuthCalls)).toEqual([{ bot_id: 123456, request_access: 'write' }]);
  // The original button stays where it was; no second Telegram button appears.
  await expect(blueButton(page)).toBeVisible();
  expect(await blueButton(page).boundingBox()).toEqual(box);
  await expect(page.locator('iframe[src*="telegram"], script[data-telegram-login]')).toHaveCount(0);
  await expect(page.getByText('Войти через Telegram', { exact: true })).toHaveCount(1);
});

test('header sign-in opens the same Telegram window without reloading /login', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await stubSdk(page);
  await openLogin(page);
  await page.evaluate(() => { window.__sameDocument = true; });

  const [popup] = await Promise.all([page.waitForEvent('popup'), headerButton(page).click()]);

  expect(popup).toBeTruthy();
  expect(await authCalls(page)).toBe(1);
  expect(page.url()).toMatch(/\/login$/);
  expect(await page.evaluate(() => window.__sameDocument)).toBe(true);
});

test('header sign-in starts Telegram from other public pages too', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await stubSdk(page);
  await page.goto('/documentation', { waitUntil: 'networkidle' });
  await page.waitForFunction(() => window.__tgAuthCalls);

  const [popup] = await Promise.all([page.waitForEvent('popup'), headerButton(page).click()]);

  expect(popup).toBeTruthy();
  expect(page.url()).toMatch(/\/documentation$/);
});

test('rapid clicks open one window only', async ({ page }) => {
  await stubSdk(page);
  await openLogin(page);

  await blueButton(page).click();
  await blueButton(page).click();
  await blueButton(page).click({ force: true });

  expect(await authCalls(page)).toBe(1);
  await expect(blueButton(page)).toHaveAttribute('aria-busy', 'true');
});

test('cancelled sign-in can be retried', async ({ page }) => {
  await stubSdk(page);
  await openLogin(page);
  await blueButton(page).click();

  await page.evaluate(() => window.__tgAuthCallback(false));

  await expect(page.locator('#telegram-login-status')).toContainText('Вход через Telegram отменён');
  await expect(blueButton(page)).not.toHaveAttribute('aria-busy', 'true');
  await blueButton(page).click();
  expect(await authCalls(page)).toBe(2);
});

test('blocked popup is explained and the button keeps working', async ({ page }) => {
  await stubSdk(page);
  await page.addInitScript(() => { window.open = () => null; });
  await openLogin(page);

  await blueButton(page).click();

  await expect(page.locator('#telegram-login-status')).toContainText('заблокировал окно Telegram');
  await expect(blueButton(page)).not.toHaveAttribute('aria-busy', 'true');
});

test('SDK load failure is reported and retried on the next click', async ({ page }) => {
  let attempts = 0;
  await page.route(SDK_PATTERN, (route) => { attempts += 1; return route.abort(); });
  await page.goto('/login', { waitUntil: 'networkidle' });

  await blueButton(page).click();

  await expect(page.locator('#telegram-login-status')).toContainText('Не удалось загрузить вход через Telegram');
  await expect(blueButton(page)).toBeVisible();
  await expect(blueButton(page)).not.toHaveAttribute('aria-busy', 'true');
  expect(attempts).toBeGreaterThanOrEqual(2);
});

test('Telegram result is handed to /telegram-login with every signed field', async ({ page }) => {
  await stubSdk(page);
  await openLogin(page);
  await blueButton(page).click();

  const [request] = await Promise.all([
    page.waitForRequest((req) => req.url().includes('/telegram-login?')),
    page.evaluate(() => {
      setTimeout(() => window.__tgAuthCallback({
        id: 42, first_name: 'Ева', username: 'eva', auth_date: 1700000000, hash: 'deadbeef',
      }), 0);
    }),
  ]);

  const params = new URL(request.url()).searchParams;
  expect(Object.fromEntries(params)).toEqual({
    id: '42', first_name: 'Ева', username: 'eva', auth_date: '1700000000', hash: 'deadbeef',
  });
  // The unsigned stand-in is refused by the unchanged server check.
  await expect(page.getByText('Ошибка авторизации через Telegram')).toBeVisible();
});

for (const viewport of [{ width: 390, height: 844 }, { width: 1280, height: 800 }]) {
  test(`login button keeps its look at ${viewport.width}px`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await stubSdk(page);
    await openLogin(page);

    const style = await blueButton(page).evaluate((el) => {
      const s = getComputedStyle(el);
      return { radius: s.borderTopLeftRadius, color: s.color, display: s.display };
    });
    expect(style.display).not.toBe('none');
    expect(parseFloat(style.radius)).toBeGreaterThan(100);
    await expect(page.locator('#telegram-login-status')).toBeHidden();
  });
}
