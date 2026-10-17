const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

// Telegram Web (web.telegram.org/k and /a) opens the Mini App in a cross-site
// iframe. The Telegram page is a stand-in served on port 5011 under another
// site (browsers block a public HTTPS page from framing 127.0.0.1, and the e2e
// server allows the stand-in next to https://web.telegram.org). The iframe
// loads the real local server, so the browser enforces frame-ancestors and the
// SameSite/Partitioned cookie rules itself. window.Telegram.WebApp is a stub
// carrying initData signed with the e2e server's placeholder bot token.

const APP_ORIGIN = 'http://127.0.0.1:5010';
const TELEGRAM_WEB = 'http://telegram-web.localhost:5011/k/';
const FOREIGN_SITE = 'http://localhost:5011/';
const TELEGRAM_SCRIPT = 'https://telegram.org/js/telegram-web-app.js';
const BOT_TOKEN = '123456:playwright-placeholder-token';
const FRAME_ANCESTORS = "frame-ancestors 'self' https://web.telegram.org http://telegram-web.localhost:5011";
const DATABASE_PATH = path.join(os.tmpdir(), 'officium-playwright.sqlite');

function signedInitData(user) {
  const data = {
    auth_date: String(Math.floor(Date.now() / 1000)),
    query_id: 'e2e-telegram-web',
    user: JSON.stringify(user),
  };
  const checkString = Object.keys(data).sort().map(key => `${key}=${data[key]}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update(BOT_TOKEN).digest();
  data.hash = crypto.createHmac('sha256', secret).update(checkString).digest('hex');
  return new URLSearchParams(data).toString();
}

function telegramStub(initData) {
  return `
    window.Telegram = { WebApp: {
      initData: ${JSON.stringify(initData)},
      initDataUnsafe: { user: { first_name: 'Web' } },
      platform: 'weba', version: '8.0', colorScheme: 'light', themeParams: {},
      viewportHeight: window.innerHeight, viewportStableHeight: window.innerHeight, isExpanded: true,
      ready() {}, expand() {}, onEvent() {}, offEvent() {},
      setHeaderColor() {}, setBackgroundColor() {}, setBottomBarColor() {},
      BackButton: { show() {}, hide() {}, onClick() {} },
      HapticFeedback: { impactOccurred() {}, notificationOccurred() {} },
    } };
  `;
}

function enableRegistration() {
  const script = [
    'import sqlite3, sys',
    'db = sqlite3.connect(sys.argv[1])',
    'db.execute("DELETE FROM app_settings WHERE key = \'registration_enabled\'")',
    'db.execute("INSERT INTO app_settings (key, value) VALUES (\'registration_enabled\', \'true\')")',
    'db.commit()',
  ].join('\n');
  execFileSync(process.env.PYTHON || 'python', ['-c', script, DATABASE_PATH]);
}

const HOST_PAGE = `<!doctype html><title>host</title>
  <iframe name="miniapp" src="${APP_ORIGIN}/telegram-app" width="420" height="760"></iframe>`;
let hostServer;

test.beforeAll(async () => {
  hostServer = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
    response.end(HOST_PAGE);
  });
  await new Promise(resolve => hostServer.listen(5011, resolve));
});

test.afterAll(async () => {
  await new Promise(resolve => hostServer.close(resolve));
});

async function embed(page, hostUrl) {
  await page.route(TELEGRAM_SCRIPT, route => route.fulfill({
    status: 200,
    contentType: 'application/javascript',
    body: telegramStub(signedInitData({ id: 777000123, first_name: 'Web' })),
  }));
  const entry = page.waitForResponse(`${APP_ORIGIN}/telegram-app`);
  await page.goto(hostUrl);
  return entry;
}

test('another site cannot frame the Mini App', async ({ page }) => {
  const entry = await embed(page, FOREIGN_SITE);

  // Either header is fine as long as the foreign origin is not in it.
  const headers = await entry.allHeaders();
  expect(
    headers['x-frame-options'] === 'SAMEORIGIN' || headers['content-security-policy'] === FRAME_ANCESTORS,
    `framing headers: ${JSON.stringify(headers)}`,
  ).toBe(true);
  await page.waitForTimeout(1_000);
  const frame = page.frame({ name: 'miniapp' });
  const rendered = frame ? await frame.locator('#miniAppTitle').count().catch(() => 0) : 0;
  expect(rendered, 'Mini App rendered inside a foreign site').toBe(0);
});

test.describe('Telegram Web iframe', () => {
  // Chromium is the engine of Telegram Web's main audience and supports
  // partitioned (CHIPS) cookies; Firefox and WebKit treat SameSite=None
  // cookies over plain http://127.0.0.1 differently, so they are not a
  // faithful model of the HTTPS production setup.
  test.skip(({ browserName }) => browserName !== 'chromium', 'partitioned cookies are modelled in Chromium');

  test('logs in, saves an expense and keeps the regular session separate', async ({ page, context }) => {
    enableRegistration();
    const entry = await embed(page, TELEGRAM_WEB);

    const headers = await entry.allHeaders();
    expect(headers['x-frame-options']).toBeUndefined();
    expect(headers['content-security-policy']).toBe(FRAME_ANCESTORS);

    const frame = page.frame({ name: 'miniapp' });
    await expect.poll(() => frame.url()).toBe(`${APP_ORIGIN}/`);
    await expect(frame.locator('body')).toHaveClass(/telegram-mini-app/);

    const cookies = await context.cookies(APP_ORIGIN);
    const frameCookie = cookies.find(cookie => cookie.name === 'officium_tg_frame');
    expect(frameCookie, 'frame session cookie').toBeTruthy();
    expect(frameCookie.sameSite).toBe('None');
    expect(frameCookie.secure).toBe(true);
    expect(cookies.find(cookie => cookie.name === 'session')).toBeUndefined();

    // A regular form POST inside the frame still passes CSRF validation.
    await frame.goto(`${APP_ORIGIN}/expenses`);
    const form = frame.locator('form[action$="/expenses"]');
    await form.locator('select[name="category"]').selectOption('products');
    await form.locator('input[name="title"]').fill('Покупка из Telegram Web');
    await form.locator('input[name="amount"]').fill('990');
    await form.locator('input[name="expense_date"]').fill(new Date().toISOString().slice(0, 10));
    await form.getByRole('button', { name: 'Сохранить расход' }).click();
    await expect(frame.locator('.alert-success', { hasText: 'Расход сохранён' })).toBeVisible();

    // The partitioned frame session is not the top-level session.
    const topLevel = await context.newPage();
    await topLevel.goto(`${APP_ORIGIN}/expenses`);
    await expect(topLevel).toHaveURL(/\/login/);
  });
});
