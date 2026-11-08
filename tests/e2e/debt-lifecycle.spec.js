const { test: base, expect } = require('@playwright/test');

const test = base.extend({
  debtCleanup: [async ({ page }, use) => {
    const created = {};
    try {
      await use(created);
    } finally {
      // Teardown has its own budget even if a browser operation times out.
      if (created.id) {
        const removed = await page.request.delete(`/api/debts/${created.id}/delete`, {
          headers: { 'X-CSRFToken': created.csrf },
        });
        expect(removed.ok(), 'Remove only the debt created by this test').toBe(true);
      }
    }
  }, { timeout: 60_000 }],
});

function mutation(page, path, method) {
  return page.waitForResponse(response =>
    new URL(response.url()).pathname === path && response.request().method() === method);
}

async function readyModal(page, selector) {
  await expect(page.locator(selector)).toBeVisible();
  // Bootstrap moves focus when its transition ends; wait before typing.
  await expect(page.locator(`${selector} .modal-dialog`)).toHaveCSS('transform', 'none');
}

async function mutateAndReload(page, path, method, action) {
  // Read the body before the application's delayed reload discards it.
  const response = mutation(page, path, method).then(async result => ({
    ok: result.ok(), payload: await result.json(),
  }));
  const loaded = page.waitForEvent('load');
  await action();
  const { ok, payload } = await response;
  expect(ok, JSON.stringify(payload)).toBe(true);
  expect(payload.success).toBe(true);
  await loaded;
  return payload;
}

for (const mode of [{ width: 1440, theme: 'light' }, { width: 390, theme: 'dark' }]) {
  test(`debt lifecycle through forms, payments and archive ${mode.width}`, async ({ page, debtCleanup, browserName }, info) => {
    // WebKit needs more time for the complete sequence on Windows.
    test.setTimeout(browserName === 'webkit' ? 600_000 : 300_000);
    page.setDefaultTimeout(60_000);
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.setViewportSize({ width: mode.width, height: 1000 });
    await page.addInitScript(theme => localStorage.setItem('officiumTheme', theme), mode.theme);
    await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({
      contentType: 'application/javascript', body: '',
    }));
    await page.goto('/dev-login/user');
    const csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content');
    const today = await page.locator('html').getAttribute('data-financial-date');
    const due = new Date(`${today}T00:00:00Z`);
    due.setUTCDate(due.getUTCDate() + 10);
    const product = `Сквозная проверка долгов ${info.project.name} ${mode.width}`;
    let debtId;
    await page.locator('button[onclick="openAddModal()"]').filter({ visible: true }).first().click();
    await readyModal(page, '#debtModal');
    await page.locator('#f_bank_name').fill('Тестовый банк');
    await page.locator('#f_debt_type').selectOption('consumer_credit');
    await page.locator('#f_product_name').fill(product);
    await page.locator('#f_total_amount').fill('25000');
    await page.locator('#f_remaining_amount').fill('25000');
    await page.locator('#f_minimum_payment').fill('2500');
    await page.locator('#f_interest_rate').fill('0');
    await page.locator('#f_penalty_rate').fill('0');
    await page.locator('#f_next_payment_date').fill(due.toISOString().slice(0, 10));
    await page.locator('#f_accrual_start_date').fill(today);
    await page.locator('#f_is_payment_recurring').check();
    const createdResponse = mutation(page, '/api/debts', 'POST').then(async response => ({
      status: response.status(), body: await response.json(),
    }));
    const createdLoad = page.waitForEvent('load');
    await page.locator('#debtSaveBtn').click();
    const { status, body } = await createdResponse;
    expect(status, JSON.stringify(body)).toBe(201);
    debtId = body.debt.id;
    debtCleanup.id = debtId;
    debtCleanup.csrf = csrf;
    await createdLoad;
    const base = `/api/debts/${debtId}`;
    const card = page.locator(`.debt-card[data-id="${debtId}"]`);
    await expect(card).toContainText(product);
    await expect(card.locator('.debt-card__amount-main')).toContainText(/25\s*000/);

    await card.locator('button[onclick^="openEditModal"]').click();
    await readyModal(page, '#debtModal');
    await expect(page.locator('#f_product_name')).toHaveValue(product);
    await expect(page.locator('#debtSaveBtn .bi-floppy')).toHaveCount(1);
    await page.locator('#f_product_name').fill(`${product} — изменён`);
    await page.locator('#f_product_name').press('Tab');
    await expect(page.locator('#f_product_name')).toHaveValue(`${product} — изменён`);
    const renamed = await mutateAndReload(page, base, 'PUT', () => page.locator('#debtSaveBtn').click());
    expect(renamed.debt.product_name).toBe(`${product} — изменён`);
    await expect(card).toContainText(`${product} — изменён`);

    await card.locator('.btn-action--pay').click();
    await readyModal(page, '#paymentModal');
    await page.locator('#pm_amount').fill('30000');
    await page.locator('#pm_date').fill(today);
    const rejected = mutation(page, `${base}/payments`, 'POST');
    await page.locator('#pm_record').click();
    expect((await rejected).status()).toBe(422);
    await expect(page.locator('#paymentFormError')).toBeVisible();
    expect((await (await page.request.get(`${base}/payments`)).json()).payments).toHaveLength(0);

    await page.locator('#pm_amount').fill('2500');
    const paid = await mutateAndReload(page, `${base}/payments`, 'POST', () => page.locator('#pm_record').click());
    const paymentId = paid.payment.id;
    expect(paid.payment.principal_amount).toBe(2500);
    expect(paid.payment.interest_amount).toBe(0);
    expect(paid.debt.current_principal).toBe(22500);
    await expect(card.locator('.debt-card__amount-main')).toContainText(/22\s*500/);

    await card.locator('.btn-action--history').click();
    await readyModal(page, '#historyModal');
    const history = page.locator('#historyModal');
    await expect(history.locator('tbody tr')).toHaveCount(1);
    const buttons = history.locator('.history-actions button');
    const gap = await buttons.evaluateAll(([edit, remove]) =>
      remove.getBoundingClientRect().top - edit.getBoundingClientRect().bottom);
    expect(gap).toBeGreaterThanOrEqual(7.5);
    await history.locator('.history-icon-btn-edit').click();
    await readyModal(page, '#editPaymentModal');
    await expect(page.locator('#ep_amount')).toHaveValue('2500.00');
    await expect(page.locator('#editPaymentModal .bi-floppy')).toHaveCount(1);
    await page.locator('#ep_amount').fill('3000');
    const edited = await mutateAndReload(page, `${base}/payments/${paymentId}`, 'PUT', () =>
      page.locator('#editPaymentModal').getByRole('button', { name: 'Сохранить', exact: true }).click());
    expect(edited.debt.current_principal).toBe(22000);
    await expect(card.locator('.debt-card__amount-main')).toContainText(/22\s*000/);

    await card.locator('.btn-action--pay').click();
    await readyModal(page, '#paymentModal');
    await page.locator('#pm_amount').fill('1000');
    await page.locator('#pm_principal_amount').fill('900');
    await page.locator('#pm_interest_amount').fill('0');
    await page.locator('#pm_fee_amount').fill('100');
    await page.locator('#pm_bank_remaining_after_payment').fill('21100');
    const manual = await mutateAndReload(page, `${base}/payments`, 'POST', () => page.locator('#pm_record').click());
    const manualId = manual.payment.id;
    expect(manual.payment.breakdown_source).toBe('manual');
    expect(manual.debt.current_principal).toBe(21100);

    await card.locator('.btn-action--history').click();
    await readyModal(page, '#historyModal');
    const manualRow = history.locator('tbody tr').filter({ has: page.locator(`button[onclick="openEditPaymentModal(${debtId}, ${manualId})"]`) });
    await manualRow.locator('.history-icon-btn-edit').click();
    await readyModal(page, '#editPaymentModal');
    await expect(page.locator('#ep_manual_breakdown')).toBeChecked();
    await page.locator('#ep_comment').fill('Ручная разбивка сохранена');
    const preserved = await mutateAndReload(page, `${base}/payments/${manualId}`, 'PUT', () =>
      page.locator('#editPaymentModal').getByRole('button', { name: 'Сохранить', exact: true }).click());
    expect(preserved.payment).toMatchObject({
      principal_amount: 900, interest_amount: 0, fee_amount: 100,
      bank_remaining_after_payment: 21100, breakdown_source: 'manual',
    });
    expect(preserved.debt.current_principal).toBe(21100);

    await card.locator('.btn-action--schedule').click();
    await readyModal(page, '#scheduleModal');
    const schedule = page.locator('#scheduleModal');
    await expect(schedule.locator('.schedule-table tbody tr').first()).toBeVisible();
    await expect(schedule.locator('.schedule-summary')).toContainText(/21\s*100/);
    await expect(schedule.locator('.money-mask:visible')).toHaveCount(0);
    await schedule.screenshot({ path: info.outputPath('debt-schedule.png') });
    await schedule.locator('.btn-close').click();

    for (const [id, expected] of [[paymentId, 24100], [manualId, 25000]]) {
      await card.locator('.btn-action--history').click();
      await readyModal(page, '#historyModal');
      const row = history.locator('tbody tr').filter({ has: page.locator(`button[onclick="deletePayment(${debtId}, ${id})"]`) });
      await row.locator('.history-icon-btn-delete').click();
      const confirm = page.locator('.ui-confirm-dialog');
      await expect(confirm).toBeVisible();
      const deleted = await mutateAndReload(page, `${base}/payments/${id}`, 'DELETE', () =>
        confirm.getByRole('button', { name: 'Продолжить', exact: true }).click());
      expect(deleted.debt.current_principal).toBe(expected);
      const historyData = await (await page.request.get(`${base}/payments`)).json();
      if (id === paymentId) {
        expect(historyData.payments).toHaveLength(1);
        expect(historyData.payments[0]).toMatchObject({
          principal_amount: 900, fee_amount: 100, bank_remaining_after_payment: 21100,
        });
      } else expect(historyData.payments).toHaveLength(0);
    }
    await expect(card.locator('.debt-card__amount-main')).toContainText(/25\s*000/);
    await mutateAndReload(page, `${base}/archive`, 'POST', () => card.locator('.btn-action--archive').click());
    await expect(card).toHaveCount(0);
    await page.goto('/archive');
    await expect(card).toContainText(`${product} — изменён`);
    await card.locator('.btn-action--restore').click();
    await mutateAndReload(page, `${base}/restore`, 'POST', () =>
      page.locator('.ui-confirm-dialog').getByRole('button', { name: 'Продолжить', exact: true }).click());
    await expect(card).toHaveCount(0);
    await page.goto('/');
    await expect(card.locator('.debt-card__amount-main')).toContainText(/25\s*000/);
    await page.reload();
    await expect(card).toContainText(`${product} — изменён`);
    expect(errors).toEqual([]);
  });
}
