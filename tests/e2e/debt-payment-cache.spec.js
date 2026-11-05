const { test: base, expect } = require('@playwright/test');

const test = base.extend({
  registerDebtCleanup: async ({ playwright, baseURL }, use) => {
    let cleanup;
    await use(async (page, debtId, csrf) => {
      const api = await playwright.request.newContext({
        baseURL, storageState: await page.context().storageState(), timeout: 10_000,
      });
      cleanup = { api, debtId, csrf };
    });
    if (cleanup) {
      try {
        const response = await cleanup.api.delete(`/api/debts/${cleanup.debtId}/delete`, {
          headers: { 'X-CSRFToken': cleanup.csrf },
        });
        expect(response.ok(), 'Delete the test debt').toBeTruthy();
      } finally {
        await cleanup.api.dispose();
      }
    }
  },
});

test('payment updates the debt balance before any page reload', async ({ page, registerDebtCleanup }) => {
  await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
    contentType: 'application/javascript', body: '',
  }));
  await page.goto('/dev-login/user');
  await page.goto('/');
  const csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content');
  const response = await page.request.post('/api/debts', {
    headers: { 'X-CSRFToken': csrf },
    data: {
      bank_name: 'Проверка остатка', product_name: 'Тестовый кредит',
      debt_type: 'consumer_credit', total_amount: '25000', remaining_amount: '25000',
      minimum_payment: '2500', interest_rate: '0',
    },
  });
  expect(response.ok()).toBeTruthy();
  const created = await response.json();
  expect(created.success).toBe(true);
  const debtId = created.debt.id;
  await registerDebtCleanup(page, debtId, csrf);
  await page.goto('/');
  const card = page.locator(`.debt-card[data-id="${debtId}"]`);
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
  await card.getByRole('button', { name: /Платеж/ }).click();
  await page.evaluate(() => window.paymentFormReady.then(() => { delete window.paymentFormReady; }));
  await page.locator('#pm_amount').fill('2500');
  await expect(page.locator('#pm_amount')).toHaveValue(/2\s*500/);
  const paymentResponse = page.waitForResponse(r =>
    r.url().endsWith(`/api/debts/${debtId}/payments`) && r.request().method() === 'POST');
  await page.locator('#paymentModal').getByRole('button', { name: 'Внести платеж' }).click();
  const paid = await (await paymentResponse).json();
  expect(paid.debt.remaining_amount).toBe(22500);
  expect(paid.debt.total_debt).toBe(22500);
  // sessionStorage preserves evidence even if the normal delayed reload beats the assertion.
  await expect(async () => {
    const observed = await page.evaluate(key => JSON.parse(sessionStorage.getItem(key)), observationKey);
    expect(observed).not.toBeNull();
    expect(observed.amount).toMatch(/22\s*500/);
    expect(observed.progress).toMatch(/22\s*500/);
  }).toPass({ timeout: 8_000 });
});
