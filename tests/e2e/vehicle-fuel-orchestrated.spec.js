const {test, expect} = require('@playwright/test');
test.use({video:'off'});

async function setup(page, info, suffix) {
  await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({contentType:'application/javascript',body:''}));
  await page.request.get('/dev-login/user');
  await page.goto('/vehicles/new', {waitUntil:'domcontentloaded'});
  const csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content');
  const result = await page.request.post('/vehicles/new', {form:{csrf_token:csrf,name:`Оркестр ${suffix} ${info.project.name} ${Date.now()}`,is_company:'on'}});
  expect(result.ok()).toBe(true);
  const id=result.url().match(/\/vehicles\/(\d+)$/)[1];
  await page.goto(`/vehicles/${id}/refueling/new`, {waitUntil:'domcontentloaded'});
  return {id,csrf};
}

async function fill(page, values) {
  for(const [key,value] of Object.entries(values)) await page.locator(`.vehicle-fuel-form [name="${key}"]`).fill(value);
}

test('orchestrated fuel rounds half-kopek exactly', async ({page},info) => {
  test.setTimeout(180000);
  await setup(page,info,'precision');
  await fill(page,{refuel_date:'2026-03-01',liters:'1.005',price_per_liter:'1.00'});
  await expect(page.locator('[name="total_amount"]')).toHaveValue('1.01');
  for (const [liters,price,expected] of [['10000','52.301','523000.00'],['50','1.005','50.50'],['0.0005','1000','1.00'],['40,501','52,30','2118.20']]) {
    await fill(page,{liters,price_per_liter:price});
    await expect(page.locator('[name="total_amount"]')).toHaveValue(expected);
  }
});

test('orchestrated fuel checkbox tolerance period payer date archive trailer', async ({page},info) => {
  test.setTimeout(300000);
  const {id,csrf}=await setup(page,info,'edges');
  const post=async(url,data)=>page.request.post(url,{form:{csrf_token:csrf,...data}});
  await fill(page,{refuel_date:'2026-03-01',odometer_km:'1000',liters:'40,501',price_per_liter:'52,30',station_name:'Тестовая оркестровая АЗС'});
  await expect(page.locator('[name="total_amount"]')).toHaveValue('2118.20');
  await page.locator('[name="total_amount"]').fill('2118.60');
  await page.getByRole('switch',{name:'До полного'}).uncheck();
  await page.getByRole('button',{name:'Сохранить заправку'}).click({noWaitAfter:true});
  await expect(page.locator('.vehicle-fuel-history')).toContainText('40.501');
  await page.locator('.vehicle-fuel-history').getByRole('link',{name:'Редактировать'}).click({noWaitAfter:true});
  await expect(page.getByRole('switch',{name:'До полного'})).not.toBeChecked();
  await fill(page,{refuel_date:'2026-03-02',odometer_km:'1100'});
  await page.getByLabel('Организация',{exact:true}).check();
  await page.getByRole('switch',{name:'До полного'}).check();
  await page.getByRole('button',{name:'Сохранить заправку'}).click({noWaitAfter:true});
  await expect(page.locator('.vehicle-fuel-history')).toContainText('02.03.2026');
  await expect(page.locator('.vehicle-fuel-history')).toContainText('Организация');
  await page.goto(`/vehicles/${id}?tab=expenses`,{waitUntil:'domcontentloaded'});
  await expect(page.getByText('Личных трат пока нет.',{exact:true})).toBeVisible();
  await expect(page.getByText('Трат организации пока нет.',{exact:true})).toHaveCount(0);
  await page.goto(`/vehicles/${id}?tab=mileage`,{waitUntil:'domcontentloaded'});
  await expect(page.locator('tbody')).toContainText('02.03.2026');
  await expect(page.locator('tbody')).not.toContainText('01.03.2026');
  await page.goto(`/vehicles/${id}/refueling/new`,{waitUntil:'domcontentloaded'});
  await fill(page,{refuel_date:'2026-03-03',odometer_km:'1200',liters:'10',price_per_liter:'50'});
  await page.getByRole('button',{name:'Сохранить заправку'}).click({noWaitAfter:true});
  await expect(page.locator('.vehicle-fuel-history tbody tr').first().locator('td').nth(5)).toHaveText('10.00');
  await page.getByLabel('С даты',{exact:true}).fill('2026-03-03');
  await page.getByLabel('По дату',{exact:true}).fill('2026-03-03');
  await page.getByRole('button',{name:'Показать период'}).click({noWaitAfter:true});
  await expect(page.locator('.vehicle-fuel-history tbody tr')).toHaveCount(1);
  await expect(page.getByRole('heading',{name:'За выбранный период · 1 заправок'})).toBeVisible();
  await expect(page.locator('.vehicle-fuel-fields').filter({hasText:'Литры:'})).toContainText('10.000');
  await page.goto(`/vehicles/${id}/refueling/new`,{waitUntil:'domcontentloaded'});
  await fill(page,{refuel_date:'2026-03-04',odometer_km:'1300',liters:'10',price_per_liter:'50',total_amount:'500.60'});
  await page.getByRole('button',{name:'Сохранить заправку'}).click({noWaitAfter:true});
  await expect(page.getByText('Итог отличается от объёма × цены более чем на 0,50 ₽.',{exact:true})).toBeVisible();
  await page.goto(`/vehicles/${id}?tab=fuel`,{waitUntil:'domcontentloaded'});
  await expect(page.locator('.vehicle-fuel-history tbody tr')).toHaveCount(2);
  expect((await post(`/vehicles/${id}/archive`,{reason:'sold'})).ok()).toBe(true);
  await page.goto(`/vehicles/${id}?tab=fuel`,{waitUntil:'domcontentloaded'});
  await expect(page.getByText('Добавить заправку',{exact:true})).toHaveCount(0);
  await expect(page.locator('.vehicle-fuel-history').getByRole('link',{name:'Редактировать'})).toHaveCount(0);
  const trailer=await post('/vehicles/new',{name:`Прицеп оркестр ${info.project.name} ${Date.now()}`,kind:'trailer'});
  expect(trailer.ok()).toBe(true);
  const trailerId=trailer.url().match(/\/vehicles\/(\d+)$/)[1];
  await page.goto(`/vehicles/${trailerId}`,{waitUntil:'domcontentloaded'});
  await expect(page.getByRole('link',{name:'Заправки',exact:true})).toHaveCount(0);
  expect((await page.request.get(`/vehicles/${trailerId}/refueling/new`)).status()).toBe(404);
});
