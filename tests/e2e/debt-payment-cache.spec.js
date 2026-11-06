const { execFileSync } = require('node:child_process');
const { test: base, expect } = require('@playwright/test');

function paymentDebtFixture(...args) {
  return execFileSync(process.env.PYTHON || 'python', ['scripts/seed_playwright_payment_debt.py', ...args], {
    encoding: 'utf8', timeout: 20_000,
  }).trim();
}

const test = base.extend({
  paymentDebtId: [async ({ page }, use, testInfo) => {
    await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
      contentType: 'application/javascript', body: '',
    }));
    await page.goto('/dev-login/user', { waitUntil: 'domcontentloaded' });
    const debtId = paymentDebtFixture();
    try {
      await page.goto('/');
      await use(debtId);
    } finally {
      // A timed-out browser action may still submit the payment during teardown.
      if (testInfo.status === 'timedOut') await page.close();
      paymentDebtFixture('--delete', debtId);
    }
  }, { timeout: 60_000 }],
});

test('confirming a planned payment updates the debt balance before any page reload', async ({ page, paymentDebtId: debtId }) => {
  const card = page.locator(`.debt-card[data-id="${debtId}"]`);
  const paymentModal = page.locator('#paymentModal');
  await expect(card.locator('.debt-card__amount-main')).toContainText(/25\s*000/);
  await card.locator('.btn-action--pay').click();
  await expect(paymentModal).toBeVisible();
  const today = await page.locator('#pm_date').inputValue();
  const dueDate = new Date(`${today}T00:00:00Z`);
  dueDate.setUTCDate(dueDate.getUTCDate() + 5);
  await page.locator('#pm_amount').fill('2500');
  await page.locator('#pm_date').fill(dueDate.toISOString().slice(0, 10));
  await Promise.all([
    page.waitForEvent('load'),
    paymentModal.getByRole('button', { name: 'Запланировать платёж' }).click(),
  ]);
  await expect(card.getByText(/Запланировано 2\s*500/)).toBeVisible();
  await expect(card.locator('.debt-card__amount-main')).toContainText(/25\s*000/);
  const observationKey = `payment-before-reload-${debtId}`;
  await page.evaluate(({ debtId, observationKey }) => {
    sessionStorage.removeItem(observationKey);
    let leaving = false;
    addEventListener('beforeunload', () => { leaving = true; }, { once: true });
    const card = document.querySelector(`.debt-card[data-id="${debtId}"]`);
    // This observer belongs only to the current document; a reloaded card cannot satisfy it.
    const observer = new MutationObserver(() => {
      const amount = card.querySelector('.debt-card__amount-main').textContent;
      const progress = card.querySelector('.debt-card__progress-label').textContent;
      if (!leaving && /22\s*500/.test(amount) && /22\s*500/.test(progress)) {
        sessionStorage.setItem(observationKey, JSON.stringify({ amount, progress }));
        observer.disconnect();
      }
    });
    observer.observe(card, { subtree: true, childList: true, characterData: true });
  }, { debtId, observationKey });
  await page.evaluate(() => {
    window.paymentFormReady = new Promise(resolve => {
      document.getElementById('paymentModal').addEventListener('shown.bs.modal', resolve, { once: true });
    });
  });
  await card.locator('.btn-action--history').click();
  await page.locator('#historyModal').getByRole('button', { name: 'Записать оплату' }).click();
  await page.evaluate(() => window.paymentFormReady.then(() => { delete window.paymentFormReady; }));
  await expect(page.locator('#pm_amount')).toHaveValue(/2\s*500/);
  await expect(page.locator('#pm_date')).toHaveValue(today);
  const paymentResponse = page.waitForResponse(r =>
    r.url().endsWith(`/api/debts/${debtId}/payments`) && r.request().method() === 'POST');
  const refreshed = page.waitForEvent('domcontentloaded', { timeout: 30_000 });
  await page.locator('#paymentModal').getByRole('button', { name: 'Записать оплату' }).click();
  const response = await paymentResponse;
  expect(response.ok(), 'Submit the debt payment').toBeTruthy();
  const paid = await response.json();
  expect(paid.debt.remaining_amount).toBe(22500);
  expect(paid.debt.total_debt).toBe(22500);
  // sessionStorage preserves evidence even if the normal delayed reload beats the assertion.
  await expect(async () => {
    const observed = await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), observationKey);
    expect(observed).not.toBeNull();
    expect(observed.amount).toMatch(/22\s*500/);
    expect(observed.progress).toMatch(/22\s*500/);
  }).toPass({ timeout: 8_000 });
  await refreshed;
  await expect(card.getByText(/Запланировано/)).toHaveCount(0);
});
