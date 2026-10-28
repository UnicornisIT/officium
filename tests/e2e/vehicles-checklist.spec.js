const { test, expect } = require('@playwright/test');

const dateInMoscow = () => {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  return ['year', 'month', 'day'].map(key => parts.find(part => part.type === key).value).join('-');
};
const cents = text => Math.round(Number(text.replace(/[^\d,.-]/g, '').replace(',', '.')) * 100);
const saveVehicle = async page => {
  await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
  await expect(page).toHaveURL(/\/vehicles\/\d+$/);
  return new URL(page.url()).pathname;
};
const createVehicle = async (page, name, kind = 'passenger', trailerName = '') => {
  await page.goto('/vehicles/new');
  await page.locator('[name="name"]').fill(name);
  await page.locator('[name="kind"]').selectOption(kind);
  if (trailerName) await page.locator('[name="trailer_name"]').fill(trailerName);
  return saveVehicle(page);
};
const editVehicle = async (page, path, update) => {
  await page.goto(`${path}/edit`);
  await update();
  await saveVehicle(page);
};
const addReading = async (page, path, date, km) => {
  await page.goto(`${path}?tab=mileage`);
  await page.locator('[name="reading_date"]').fill(date);
  await page.locator('[name="odometer_km"]').fill(String(km));
  await page.getByRole('button', { name: 'Добавить замер', exact: true }).click();
};

test('vehicle checklist: cashflow, mileage, repair, reminders, archive and ownership', async ({ page }, testInfo) => {
  test.setTimeout(300_000);
  const name = `Учебный чек-лист ${testInfo.project.name} прогон ${testInfo.repeatEachIndex} попытка ${testInfo.retry}`;
  await page.goto('/dev-login/user');
  await page.goto('/');
  const originalTotal = cents(await page.locator('.money-flow__item--expense strong').innerText());
  const car = await createVehicle(page, `${name} легковая`);
  const carId = car.split('/').pop();
  const tractor = await createVehicle(page, `${name} тягач`, 'tractor_unit', `${name} прицеп`);
  const trailer = await page.locator('.vehicle-trailer a').getAttribute('href');
  await page.goto(trailer);
  await expect(page.getByRole('link', { name: 'Пробег', exact: true })).toHaveCount(0);
  await expect(page.locator('.vehicle-facts')).not.toContainText('Топливо');
  await expect(page.getByRole('link', { name: `${name} тягач`, exact: true })).toHaveAttribute('href', tractor);
  const second = await createVehicle(page, `${name} вторая`);
  const secondId = second.split('/').pop();

  await page.goto(`${car}/expense`);
  await expect(page.locator('[name="category"]')).toHaveValue('transport');
  await page.locator('[name="title"]').fill(`${name} расход`);
  await page.locator('[name="amount"]').fill('123.45');
  await page.locator('[name="expense_date"]').fill(dateInMoscow());
  await page.getByRole('button', { name: 'Сохранить расход', exact: true }).click();
  await expect(page).toHaveURL(/month=/);
  const row = page.locator('tr', { hasText: `${name} расход` });
  await expect(row).toContainText('Транспорт');
  const editExpense = await row.getByRole('link', { name: 'Редактировать', exact: true }).getAttribute('href');
  await page.goto('/');
  expect(cents(await page.locator('.money-flow__item--expense strong').innerText())).toBe(originalTotal + 12345);
  await page.goto(`${car}?tab=expenses`);
  await expect(page.locator('tbody')).toContainText(`${name} расход`);

  await addReading(page, car, '2020-01-10', 100);
  await addReading(page, car, '2020-01-20', 200);
  await addReading(page, car, '2020-01-21', 99);
  await expect(page.locator('.alert-danger:visible')).toContainText('меньше замера');
  await addReading(page, car, '2020-01-15', 201);
  await expect(page.locator('.alert-danger:visible')).toContainText('более позднюю');
  await page.goto('/vehicles');
  await expect(page.locator(`#km${carId}`)).toBeVisible();
  await expect(page.locator('.mileage-reminder')).not.toContainText(`${name} прицеп`);
  await editVehicle(page, car, async () => page.locator('[name="status"]').selectOption('in_repair'));
  await page.goto('/vehicles');
  await expect(page.locator(`#km${carId}`)).toHaveCount(0);
  await editVehicle(page, car, async () => {
    await page.locator('[name="status"]').selectOption('active');
    await page.locator('[name="mileage_reminder_days"]').fill('0');
  });
  await page.goto('/vehicles');
  await expect(page.locator(`#km${carId}`)).toHaveCount(0);
  await editVehicle(page, car, async () => page.locator('[name="mileage_reminder_days"]').fill('30'));
  await page.goto('/');
  await expect(page.locator(`#km${carId}`)).toBeVisible();
  await page.locator(`#km${carId}`).fill('300');
  await page.locator(`#km${secondId}`).fill('400');
  await page.getByRole('button', { name: 'Обновить пробег', exact: true }).click();
  await expect(page.locator(`#km${carId}`)).toHaveCount(0);
  await expect(page.locator(`#km${secondId}`)).toHaveCount(0);
  await page.getByRole('button', { name: 'Напомнить через 3 дня', exact: true }).click();
  await expect(page.locator('.mileage-reminder')).toHaveCount(0);
  await page.goto(`${car}?tab=mileage`);
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await expect(page.locator('tbody tr').first()).toContainText('300');
  await page.goto(`${second}?tab=mileage`);
  await expect(page.locator('tbody')).toContainText('400');

  await page.goto('/dev-login/admin');
  await page.goto('/expenses');
  const foreignToken = await page.locator('meta[name="csrf-token"]').getAttribute('content');
  for (const path of [car, `${car}/edit`, `${car}/expense`]) {
    expect((await page.request.get(path)).status()).toBe(404);
  }
  for (const [suffix, data] of [['edit', { name: 'Попытка изменения' }], ['archive', { reason: 'sold' }], ['restore', {}], ['odometer', { reading_date: '2020-01-01', odometer_km: '1' }]]) {
    expect((await page.request.post(`${car}/${suffix}`, { form: { csrf_token: foreignToken, ...data } })).status()).toBe(404);
  }
  await page.goto('/dev-login/user');
  await page.goto(car);
  await page.getByRole('button', { name: 'Архивировать автомобиль', exact: true }).click();
  await page.locator('[name="reason"]').selectOption('sold');
  await page.locator('[name="note"]').fill('Вымышленное приватное примечание');
  await page.getByRole('button', { name: 'В архив', exact: true }).click();
  await expect(page.locator('body')).toContainText('Только просмотр');
  await expect(page.getByRole('link', { name: 'Редактировать', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Добавить расход на авто', exact: true })).toHaveCount(0);
  const token = await page.locator('meta[name="csrf-token"]').getAttribute('content');
  await page.request.post(`${car}/odometer`, { form: { csrf_token: token, reading_date: dateInMoscow(), odometer_km: '900' } });
  await page.goto(`${car}?tab=mileage`);
  await expect(page.locator('tbody tr')).toHaveCount(3);
  await page.goto(editExpense);
  await page.locator('[name="amount"]').fill('999');
  await page.getByRole('button', { name: 'Сохранить изменения', exact: true }).click();
  await expect(page.locator('.alert-danger:visible')).toContainText('Автомобиль в архиве');
  await page.locator('[name="amount"]').fill('123.45');
  await page.locator('[name="vehicle_id"]').selectOption(secondId);
  await page.getByRole('button', { name: 'Сохранить изменения', exact: true }).click();
  await expect(page.locator('.alert-danger:visible')).toContainText('Автомобиль в архиве');
  await page.goto(car);
  // WebKit may defer the form navigation beyond click(). Do not cancel the
  // restore POST by immediately navigating to the expense edit form.
  const [restoreResponse] = await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === `${car}/restore`
      && response.request().method() === 'POST', { timeout: 8_000 }),
    page.getByRole('button', { name: 'Восстановить автомобиль', exact: true }).click(),
  ]);
  expect(restoreResponse.status()).toBe(302);
  await expect(page.getByRole('link', { name: 'Редактировать', exact: true })).toBeVisible();
  await page.goto(editExpense);
  await page.locator('[name="amount"]').fill('123.46');
  await page.getByRole('button', { name: 'Сохранить изменения', exact: true }).click();
  await expect(page).toHaveURL(/month=/);
  await expect(page.locator('.alert-success:visible')).toContainText('Расход обновлён');
  await page.goto('/');
  expect(cents(await page.locator('.money-flow__item--expense strong').innerText())).toBe(originalTotal + 12346);
});

test('vehicle checklist: country management, code badge and administrator privacy', async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const code = `ZZ${testInfo.project.name[0].toUpperCase()}`;
  const countryName = `Учебная страна ${testInfo.project.name}`;
  const vehicleName = `Приватное учебное авто ${testInfo.project.name}`;
  const privateVin = 'ABCDEFGHJKLMN1234';
  await page.goto('/dev-login/superadmin');
  await page.goto('/admin/registration-countries');
  const addCountry = page.locator('form').filter({ has: page.locator('[name="code"]') });
  await addCountry.locator('[name="code"]').fill(code);
  await addCountry.locator('[name="name"]').fill(countryName);
  await addCountry.getByRole('button', { name: 'Добавить страну', exact: true }).click();
  const countryPanel = page.locator('section.admin-panel').filter({ hasText: code }).last();
  const countryAction = await countryPanel.locator('form').getAttribute('action');
  const countryId = countryAction.split('/').pop();
  await page.goto('/dev-login/user');
  await page.goto('/vehicles/new');
  await page.locator('[name="name"]').fill(vehicleName);
  await page.locator('[name="vin"]').fill(privateVin);
  await page.locator('[name="registration_country_id"]').selectOption(countryId);
  await page.locator('[name="plate"]').fill('qa 42');
  const vehiclePath = await saveVehicle(page);
  await expect(page.locator('.vehicle-visual text').last()).toHaveText(code);
  await expect(page.locator('.vehicle-plate-value')).toHaveText('QA 42');
  await page.goto('/dev-login/admin');
  expect((await page.request.get('/admin/registration-countries')).status()).toBe(403);
  await page.goto('/dev-login/superadmin');
  await page.goto('/admin/registration-countries');
  const disableCountry = page.locator(`form[action="${countryAction}"]`);
  await disableCountry.locator('[name="is_active"]').uncheck();
  await disableCountry.getByRole('button', { name: 'Сохранить', exact: true }).click();
  for (const route of ['/admin', '/admin/users', '/admin/logs', '/admin/dictionaries', '/admin/registration-countries']) {
    await page.goto(route);
    for (const secret of [vehicleName, privateVin, 'QA 42', 'Вымышленное приватное примечание']) {
      await expect(page.locator('body')).not.toContainText(secret);
    }
  }
  const csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content');
  const audit = await page.request.post('/admin/export/activity-log.csv', { form: { csrf_token: csrf } });
  expect(audit.status()).toBe(200);
  for (const secret of [vehicleName, privateVin, 'QA 42', 'Вымышленное приватное примечание']) expect(await audit.text()).not.toContain(secret);
  expect((await page.request.get(vehiclePath)).status()).toBe(404);
  await page.goto('/dev-login/user');
  await page.goto('/vehicles/new');
  await expect(page.locator(`[name="registration_country_id"] option[value="${countryId}"]`)).toHaveCount(0);
  await page.goto(`${vehiclePath}/edit`);
  await expect(page.locator('[name="registration_country_id"]')).toHaveValue(countryId);
});

test('vehicle checklist: bank import cannot change an archived vehicle expense', async ({ page }, testInfo) => {
  test.setTimeout(180_000);
  const title = `Такси учебная ежемесячная перевозка ${testInfo.project.name}`;
  await page.goto('/dev-login/user');
  const vehiclePath = await createVehicle(page, `Учебное авто импорта ${testInfo.project.name}`);
  await page.goto(`${vehiclePath}/expense`);
  await page.locator('[name="title"]').fill(title);
  await page.locator('[name="amount"]').fill('123.45');
  await page.locator('[name="expense_date"]').fill(dateInMoscow());
  await page.locator('[name="is_monthly"]').check();
  await page.getByRole('button', { name: 'Сохранить расход', exact: true }).click();
  await page.waitForURL(/month=/);
  const editExpense = await page.locator('tr', { hasText: title }).getByRole('link', { name: 'Редактировать', exact: true }).getAttribute('href');
  await page.goto(vehiclePath);
  await page.getByRole('button', { name: 'Архивировать автомобиль', exact: true }).click();
  await page.getByRole('button', { name: 'В архив', exact: true }).click();
  await page.goto('/expenses/import');
  const bankDate = dateInMoscow().split('-').reverse().join('.');
  const csv = `Сбербанк Онлайн\nДата операции;Описание операции;Сумма операции;Категория;Карта\n${bankDate};${title};-999,00;Транспорт;*1111\n`;
  await page.locator('[name="statement_file"]').setInputFiles({ name: 'fictional-transport.csv', mimeType: 'text/csv', buffer: Buffer.from(csv, 'utf8') });
  await page.getByRole('button', { name: 'Разобрать', exact: true }).click();
  await expect(page.locator('[name="action_0"]')).toHaveValue('update_monthly');
  await page.locator('form[action$="/expenses/import/confirm"] button[type="submit"]').click();
  await expect(page.locator('.alert-danger:visible')).toContainText('Автомобиль в архиве');
  await page.goto(editExpense);
  expect(cents(await page.locator('[name="amount"]').inputValue())).toBe(12345);
});
