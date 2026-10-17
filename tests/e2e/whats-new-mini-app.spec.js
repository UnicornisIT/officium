const { execFileSync } = require('node:child_process');
const os = require('node:os');
const path = require('node:path');
const { test, expect } = require('@playwright/test');

// The "Все разделы" tile of the admin panel stays inside its tile on narrow
// dark Mini App screens, and the "what's new" window is shown once to an
// account that has not acknowledged the running release.
//
// window.Telegram.WebApp is a stub; the real Telegram WebView is not covered.

const TELEGRAM_SCRIPT = 'https://telegram.org/js/telegram-web-app.js';
const DATABASE_PATH = path.join(os.tmpdir(), 'officium-playwright.sqlite');
const DEV_ADMIN_TELEGRAM_ID = -1000000002;

const telegramStub = `
  const noop = () => () => {};
  window.Telegram = { WebApp: {
    initData: 'query_id=e2e&user=%7B%22id%22%3A1%7D&auth_date=1&hash=e2e',
    initDataUnsafe: { user: { id: 1, first_name: 'E2E' } },
    platform: 'android',
    version: '8.0',
    colorScheme: 'dark',
    themeParams: { bg_color: '#17212b', secondary_bg_color: '#232e3c', text_color: '#f5f5f5',
      hint_color: '#708499', link_color: '#6ab3f3', button_color: '#5288c1',
      button_text_color: '#ffffff', header_bg_color: '#17212b' },
    viewportHeight: window.innerHeight,
    viewportStableHeight: window.innerHeight,
    isExpanded: true,
    ready: noop(), expand: noop(), onEvent: noop(), offEvent: noop(),
    setHeaderColor: noop(), setBackgroundColor: noop(), setBottomBarColor: noop(),
    BackButton: { show: noop(), hide: noop(), onClick: noop() },
    HapticFeedback: { impactOccurred: noop() },
  } };
`;

function sqlite(statement, ...params) {
  const script = [
    'import sqlite3, sys',
    'connection = sqlite3.connect(sys.argv[1])',
    'rows = connection.execute(sys.argv[2], sys.argv[3:]).fetchall()',
    'connection.commit()',
    'print("\\n".join("" if row[0] is None else str(row[0]) for row in rows))',
  ].join('\n');
  return execFileSync(process.env.PYTHON || 'python', ['-c', script, DATABASE_PATH, statement, ...params.map(String)], {
    encoding: 'utf-8',
  }).trim();
}

async function startMiniApp(page) {
  await page.route(TELEGRAM_SCRIPT, route => route.fulfill({
    status: 200, contentType: 'application/javascript', body: telegramStub,
  }));
  await page.goto('/dev-login/admin');
  await page.goto('/telegram-app');
}

// 360px has two columns, where the full name wraps between words; at 390px
// a third of the screen cannot hold "администратора" and the tile shows the
// abbreviation instead of breaking the word.
for (const [width, label] of [[360, 'Панель администратора'], [390, 'Панель админ-ра']]) {
  test(`admin tile of "Все разделы" fits at ${width}px in the dark theme`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 780 });
    await page.emulateMedia({ colorScheme: 'dark' });
    await startMiniApp(page);
    await page.goto('/');
    await expect(page.locator('body')).toHaveClass(/telegram-mini-app/);
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

    await page.locator('.telegram-app-bar__menu').click();
    const menu = page.locator('#telegramMoreMenu');
    await expect(menu).toBeVisible();
    const tiles = menu.locator('.telegram-more-menu__grid a');
    const adminTile = menu.locator('[data-admin-panel-link]');
    await expect(adminTile).toHaveAttribute('aria-label', 'Панель администратора');
    expect(await adminTile.locator(':scope > span').innerText()).toBe(label);

    const overflowing = await tiles.evaluateAll(links => links.flatMap(link => {
      const tile = link.getBoundingClientRect();
      const label = link.querySelector(':scope > span');
      const box = label.getBoundingClientRect();
      const fits = box.left >= tile.left - 0.5 && box.right <= tile.right + 0.5
        && label.scrollWidth <= label.clientWidth + 1;
      return fits ? [] : [label.textContent.trim()];
    }));
    expect(overflowing, 'labels wider than their tile').toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`more-menu-${width}.png`) });
  });
}

test('what\'s new is shown once and "Понятно" records the release', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 780 });
  await startMiniApp(page);
  sqlite('UPDATE users SET last_seen_release = NULL WHERE telegram_id = ?', DEV_ADMIN_TELEGRAM_ID);

  await page.goto('/');
  const modal = page.locator('#whatsNewModal');
  await expect(modal).toBeVisible();
  await expect(modal.locator('.modal-title')).toHaveText(/^Что нового в v\d+\.\d+\.\d+/);
  const version = await modal.getAttribute('data-whats-new-version');
  await expect(modal.getByRole('button', { name: 'Напомнить позже' })).toHaveCount(0);

  const saved = page.waitForResponse(response => response.url().endsWith('/whats-new/seen'));
  await modal.getByRole('button', { name: 'Понятно' }).click();
  expect((await saved).status()).toBe(200);
  await expect(modal).toBeHidden();
  expect(sqlite('SELECT last_seen_release FROM users WHERE telegram_id = ?', DEV_ADMIN_TELEGRAM_ID)).toBe(version);

  await page.reload();
  await expect(page.locator('#whatsNewModal')).toHaveCount(0);
});
