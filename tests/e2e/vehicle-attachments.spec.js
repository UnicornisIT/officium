const fs = require('node:fs');
const { test, expect } = require('@playwright/test');
const { revealVehicleTab, openVehicleTab } = require('./helpers/vehicle-tabs');

test.use({ video: 'off', trace: 'off', actionTimeout: 15000, navigationTimeout: 30000 });
const image = fs.readFileSync('tests/e2e/fixtures/vehicle-photo.png');
for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
  test(`attachments ${width} ${theme}`, async ({ page }, info) => {
    test.setTimeout(300000);
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(t => localStorage.setItem('officiumTheme', t), theme);
    await page.route('https://telegram.org/js/telegram-web-app.js', r => r.fulfill({ contentType: 'application/javascript', body: '' }));
    await page.goto('/dev-login/user', { waitUntil: 'domcontentloaded' });
    await page.goto('/vehicles/new', { waitUntil: 'domcontentloaded' });
    const unique = `${info.project.name}-${width}-${theme}-${Date.now()}`;
    await page.locator('#v_name').fill(`Фото ${unique}`);
    await page.locator('#v_company').check();
    await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
    await page.waitForURL(/\/vehicles\/\d+$/);
    const vehicleId = page.url().split('/').pop();
    await openVehicleTab(page, 'Фото');
    await page.getByText('Загрузить фото', { exact: true }).click();
    let form = page.locator('[data-photo-drop]');
    await form.locator('[name=file]').setInputFiles({ name: 'car.png', mimeType: 'image/png', buffer: Buffer.concat([image, Buffer.from(unique)]) });
    await form.locator('[name=caption]').fill('Автомобиль на стоянке');
    await form.getByRole('button', { name: 'Сохранить фото', exact: true }).click();
    const photos = page.locator('.vehicle-photo');
    await expect(photos).toHaveCount(1);
    await expect(photos.locator('img')).toBeVisible();
    await expect.poll(() => photos.locator('img').evaluate(img => img.complete && img.naturalWidth === 200)).toBe(true);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    expect(await page.locator('.vehicle-photo-grid').evaluate(el => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(width === 390 ? 2 : 4);
    const root = info.outputPath('screenshots');
    fs.mkdirSync(root, { recursive: true });
    await page.screenshot({ path: `${root}/${info.project.name}-${width}-${theme}-gallery.png`, fullPage: true });
    await photos.locator('a').click();
    await expect(page.locator('dialog')).toBeVisible();
    await expect.poll(() => page.locator('dialog img').evaluate(img => img.complete && img.naturalWidth === 640)).toBe(true);
    await page.screenshot({ path: `${root}/${info.project.name}-${width}-${theme}-lightbox.png` });
    await page.getByRole('button', { name: 'Закрыть фото' }).click();
    await expect(page.locator('dialog')).not.toBeVisible();
    await photos.locator('a').click();
    await page.keyboard.press('Escape');
    await expect(page.locator('dialog')).not.toBeVisible();

    if (info.project.name !== 'webkit') {
      await page.getByText('Загрузить фото', { exact: true }).click();
      form = page.locator('[data-photo-drop]');
      const transfer = await page.evaluateHandle(({ data, unique }) => {
        const dt = new DataTransfer();
        dt.items.add(new File([Uint8Array.from(data), unique + '-drop'], 'drop.png', { type: 'image/png' }));
        return dt;
      }, { data: [...image], unique });
      await form.dispatchEvent('drop', { dataTransfer: transfer });
      await expect(form.locator('[name=file]')).toHaveJSProperty('value', 'C:\\fakepath\\drop.png');
      await form.getByRole('button', { name: 'Сохранить фото', exact: true }).click();
      await expect(photos).toHaveCount(2);
      await transfer.dispose();
    }
    const before = await photos.count();
    await photos.first().getByRole('button', { name: 'Удалить фото' }).click();
    await expect(photos).toHaveCount(before - 1);

    const fileFor = suffix => ({ name: 'attachment.png', mimeType: 'image/png', buffer: Buffer.concat([image, Buffer.from(unique + suffix)]) });
    const verifyContext = async (name, container) => {
      await expect(container.locator('[data-vehicle-lightbox] img').first()).toBeVisible();
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (info.project.name === 'chromium') {
        await page.screenshot({ path: `${root}/${info.project.name}-${width}-${theme}-${name}.png`, fullPage: true });
      }
    };
    await page.goto(`/vehicles/${vehicleId}?tab=documents`);
    await page.getByText('Добавить документ', { exact: true }).click();
    const docForm = page.locator('.vehicle-document-form');
    await expect(docForm).toBeVisible();
    await docForm.locator('[name=file]').setInputFiles(fileFor('document'));
    await docForm.getByRole('button', { name: 'Сохранить документ' }).click();
    await verifyContext('documents', page.locator('.vehicle-document-card'));

    await page.goto(`/vehicles/${vehicleId}?tab=defects`);
    const defectForm = page.locator('form').filter({ has: page.locator('[name=severity]') });
    await defectForm.locator('[name=title]').fill('Царапина');
    await defectForm.locator('[name=file]').setInputFiles(fileFor('defect'));
    await defectForm.getByRole('button', { name: 'Сохранить неисправность' }).click();
    await verifyContext('defects', page.locator('article').filter({ hasText: 'Царапина' }));

    await page.goto(`/vehicles/${vehicleId}/service`);
    const serviceForm = page.locator('.vehicle-service-form');
    await serviceForm.locator('[name=odometer_km]').fill('100');
    await serviceForm.locator('[name=file]').setInputFiles(fileFor('service'));
    await serviceForm.getByRole('button', { name: 'Сохранить обслуживание' }).click();
    await verifyContext('maintenance', page.locator('.vehicle-service-entry'));

    for (const payer of ['organization', 'self']) {
      await page.goto(`/vehicles/${vehicleId}/spend`);
      const spendForm = page.locator('form').filter({ has: page.locator('[name=cost_date]') });
      await spendForm.locator('[name=title]').fill(`Чек ${payer} ${unique}`);
      await spendForm.locator('[name=amount]').fill('100');
      await spendForm.locator('[name=payer]').selectOption(payer);
      await spendForm.locator('[name=file]').setInputFiles(fileFor(payer));
      await spendForm.getByRole('button', { name: 'Сохранить трату' }).click();
      await page.waitForURL(/reports/);
    }
    await page.goto(`/vehicles/${vehicleId}?tab=expenses`);
    await verifyContext('vehicle-expenses', page.locator('article').filter({ hasText: `Чек self ${unique}` }));
    await page.getByRole('link', { name: `Чек organization ${unique}`, exact: true }).click();
    await verifyContext('cost-detail', page.locator('section.card'));
    await page.goto('/expenses');
    const receipt = page.locator('tr').filter({ hasText: `Чек self ${unique}` });
    await verifyContext('expenses', receipt);
    // The stage 7 analytics tab must remain available after adding the photo tab.
    await page.goto(`/vehicles/${vehicleId}?tab=tco`, { waitUntil: 'domcontentloaded' });
    await revealVehicleTab(page, 'Фото');
    await revealVehicleTab(page, 'Аналитика');
    await expect(page.locator('.vehicle-tco-summary')).toContainText('200');
    await expect.poll(() => page.evaluate(() => [...document.querySelectorAll('canvas')].filter(c => window.Chart?.getChart(c)).length)).toBe(3);
    await expect(page.getByRole('link', { name: 'Скачать CSV' })).toHaveAttribute('download', 'vehicle-tco.csv');
  });
}
