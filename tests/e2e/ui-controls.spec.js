const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
    await page.goto('/dev-login/superadmin');
});

test('settings keep equal field bounds with different help text', async ({ page }) => {
    for (const width of [390, 768, 1440]) {
        await page.setViewportSize({ width, height: 1000 });
        await page.goto('/admin/settings');
        const first = await page.locator('#setting-payment_warning_days').boundingBox();
        const second = await page.locator('#setting-urgent_payment_days').boundingBox();
        expect(first.height).toBeCloseTo(44, 2);
        expect(second.height).toBeCloseTo(44, 2);
        if (width > 767) expect(first.y).toBeCloseTo(second.y, 2);
    }
});

test('file picker preserves selection, accessible input and form reset', async ({ page }) => {
    await page.goto('/expenses/import');
    const input = page.locator('input[type=file]').first();
    await input.setInputFiles({name:'statement.csv', mimeType:'text/csv', buffer:Buffer.from('date,amount\n2026-11-02,100')});
    await expect(input.locator('..').locator('.ui-file-picker__name')).toHaveText('statement.csv');
    await input.evaluate(el => el.form.reset());
    await expect(input.locator('..').locator('.ui-file-picker__name')).toHaveText('Файл не выбран');
});

test('styled confirmation cancels with Escape and submits exactly once on acceptance', async ({ page }) => {
    await page.goto('/incomes');
    await page.locator('select[name=category]').selectOption({index:1});
    await page.locator('input[name=amount]').fill('100');
    await page.locator('input[name=income_date]').fill('2026-11-02');
    await page.getByRole('button', {name:'Сохранить доход',exact:true}).click();
    const deletion = page.locator('form[data-ui-confirm]').first();
    await expect(deletion).toBeAttached();
    const action = await deletion.getAttribute('action');
    let submitted = 0;
    await page.route('**' + action, route => { submitted++; return route.fulfill({status:200, body:'ok'}); });
    // Submit the real form even when its month in the history is collapsed.
    await deletion.evaluate(form => form.requestSubmit(form.querySelector('button')));
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(submitted).toBe(0);
    await deletion.evaluate(form => form.requestSubmit(form.querySelector('button')));
    await page.getByRole('button', {name:'Продолжить',exact:true}).click();
    await expect.poll(() => submitted).toBe(1);
});
