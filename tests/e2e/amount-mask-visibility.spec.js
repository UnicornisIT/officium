const { test, expect } = require('@playwright/test');

async function expectRevealed(page) {
  await expect(page.locator('#amountPrivacyToggle')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.money-value:visible').first()).toBeVisible();
  await expect(page.locator('.money-mask:visible')).toHaveCount(0);
}

async function expectMasked(page) {
  await expect(page.locator('#amountPrivacyToggle')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.money-value:visible')).toHaveCount(0);
  await expect(page.locator('.money-mask:visible').first()).toBeVisible();
}

for (const mode of [{ width: 1440, theme: 'light' }, { width: 390, theme: 'dark' }]) {
  test(`amount masks follow privacy state across pages and schedules ${mode.width}`, async ({ page }, info) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: mode.width, height: 1000 });
    await page.addInitScript(theme => localStorage.setItem('officiumTheme', theme), mode.theme);
    await page.goto('/dev-login/user');
    const token = await page.locator('meta[name="csrf-token"]').getAttribute('content');
    const today = await page.locator('html').getAttribute('data-financial-date');
    const due = new Date(`${today}T00:00:00Z`);
    due.setUTCDate(due.getUTCDate() + 10);
    const response = await page.request.post('/api/debts', {
      headers: { 'X-CSRFToken': token },
      data: {
        bank_name: 'Проверка приватности', product_name: 'Видимость сумм',
        debt_type: 'consumer_credit', total_amount: '25000', remaining_amount: '25000',
        minimum_payment: '2500', interest_rate: '0', penalty_rate: '0',
        is_payment_recurring: true, next_payment_date: due.toISOString().slice(0, 10),
        accrual_start_date: today,
      },
    });
    expect(response.status()).toBe(201);
    const debtId = (await response.json()).debt.id;
    try {
      for (const path of ['/financial-plan', '/goals', '/finance', '/']) {
        await page.goto(path);
        await expectRevealed(page);
        await page.locator('#amountPrivacyToggle').click();
        await expectMasked(page);
        await page.locator('#amountPrivacyToggle').click();
        await expectRevealed(page);
      }
      const card = page.locator(`.debt-card[data-id="${debtId}"]`);
      await card.locator('.btn-action--schedule').click();
      const summary = page.locator('#scheduleModal .schedule-summary');
      await expect(summary).toBeVisible();
      await expect(summary.locator('.money-value').first()).toBeVisible();
      await expectRevealed(page);
      await page.locator('#scheduleModal').screenshot({ path: info.outputPath('schedule-revealed.png') });
      await page.locator('#scheduleModal .btn-close').click();
      await page.locator('#amountPrivacyToggle').click();
      await card.locator('.btn-action--schedule').click();
      await expect(summary).toBeVisible();
      await expectMasked(page);
      await expect(summary.locator('.money-mask').first()).toBeVisible();
      await expect(summary.locator('.money-value:visible')).toHaveCount(0);
    } finally {
      const deleted = await page.request.delete(`/api/debts/${debtId}/delete`, {
        headers: { 'X-CSRFToken': token },
      });
      expect(deleted.ok()).toBe(true);
    }
  });
}
