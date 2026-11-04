const { test, expect } = require('@playwright/test');

test('payment updates the debt balance before any page reload', async ({ page }) => {
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
  try {
    await page.goto('/');
    const card = page.locator(`.debt-card[data-id="${debtId}"]`);
    await card.getByRole('button', { name: /Платеж/ }).click();
    await expect(page.locator('#paymentModal')).toBeVisible();
    await page.locator('#pm_amount').fill('2500');
    // Hold the existing delayed reload so only the mutation response can update the card.
    await page.clock.install();
    await page.clock.pauseAt(new Date(Date.now() + 1000));
    let navigations = 0;
    page.on('framenavigated', frame => {
      if (frame === page.mainFrame()) navigations++;
    });
    const paymentResponse = page.waitForResponse(r =>
      r.url().endsWith(`/api/debts/${debtId}/payments`) && r.request().method() === 'POST');
    await page.locator('#paymentModal').getByRole('button', { name: 'Внести платеж' }).click();
    const paid = await (await paymentResponse).json();
    expect(paid.debt.remaining_amount).toBe(22500);
    expect(paid.debt.total_debt).toBe(22500);
    await expect(card.locator('.debt-card__amount-main')).toContainText(/22\s*500/);
    await expect(card.locator('.debt-card__progress-label')).toContainText(/22\s*500/);
    expect(navigations).toBe(0);
  } finally {
    await page.request.delete(`/api/debts/${debtId}/delete`, { headers: { 'X-CSRFToken': csrf } });
  }
});
