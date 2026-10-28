const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
for (const width of [1440,390]) for (const theme of ['light','dark']) {
  test(`vehicle costs ${width} ${theme}`, async ({page}, info) => {
    test.setTimeout(180000);
    await page.setViewportSize({width,height:1000});
    await page.addInitScript(t=>localStorage.setItem('officiumTheme',t),theme);
    if(width===390) await page.route('https://telegram.org/js/telegram-web-app.js', route=>route.fulfill({contentType:'application/javascript',body:`const noop=()=>{};window.Telegram={WebApp:{initData:'e2e',initDataUnsafe:{user:{id:1}},platform:'android',version:'8.0',colorScheme:'${theme}',themeParams:{},viewportHeight:innerHeight,viewportStableHeight:innerHeight,ready:noop,expand:noop,onEvent:noop,offEvent:noop,setHeaderColor:noop,setBackgroundColor:noop,setBottomBarColor:noop,BackButton:{show:noop,hide:noop,onClick:noop},HapticFeedback:{impactOccurred:noop}}};` }));
    await page.goto('/dev-login/user');
    if(width===390) await page.goto('/telegram-app');
    await page.goto('/vehicles/new');
    await page.locator('#v_name').fill(`Учебное авто ${width} ${theme} ${info.project.name} ${info.retry}`);
    await page.locator('#v_company').check();
    await page.getByRole('button',{name:'Сохранить автомобиль',exact:true}).click();
    await page.waitForURL(/\/vehicles\/\d+$/);
    const id=page.url().split('/').pop();
    const root=path.join('docs','qa','vehicles-stage2'); fs.mkdirSync(root,{recursive:true});
    const capture=async name=>{
      await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.screenshot({path:path.join(root,`${width}-${theme}-${name}.png`),fullPage:true,animations:'disabled'});
    };
    for (const payer of ['self','organization']) {
      await page.goto(`/vehicles/${id}/spend`);
      await page.getByLabel('Кто платит').selectOption(payer);
      await page.getByLabel('Дата',{exact:true}).fill('2026-10-15');
      await page.getByLabel('Сумма',{exact:true}).fill('120.50');
      await page.getByLabel('Название',{exact:true}).fill(payer==='self'?'Учебная личная заправка':'Учебная заправка организации');
      await page.getByLabel('Способ оплаты',{exact:true}).selectOption('card');
      await page.getByLabel('Комментарий',{exact:true}).fill('Вымышленная подробность для проверки');
      if(payer==='organization') await capture('payer-form');
      await page.getByRole('button',{name:'Сохранить трату'}).click();
      await page.waitForURL(/reports/);
    }
    await page.goto(`/vehicles/${id}?tab=expenses`);
    await page.getByRole('button',{name:'Оплатил водитель, к возмещению',exact:true}).click();
    await expect(page).toHaveURL(new RegExp(`/vehicles/${id}\\?tab=expenses$`));
    await expect(page.getByRole('button',{name:'Возмещено',exact:true})).toBeVisible();
    await capture('vehicle-reimbursement');
    await expect(page.locator('article')).toContainText(['Учебная личная заправка','Учебная заправка организации']);
    await page.getByRole('link',{name:'Учебная заправка организации',exact:true}).click();
    await expect(page.getByRole('heading',{name:'Учебная заправка организации',exact:true})).toBeVisible();
    await expect(page.locator('dl')).toContainText('Карта');
    await expect(page.locator('dl')).toContainText('Вымышленная подробность для проверки');
    await capture('cost-details');

    await page.goto(`/vehicles/reports?vehicle_id=${id}&date_from=2026-10-01&date_to=2026-10-31`);
    await expect(page.locator('body')).toContainText('241');
    await capture('month-report');
    await page.getByLabel('С',{exact:true}).fill('2026-09-01');
    await page.getByRole('button',{name:'Показать',exact:true}).click();
    await capture('custom-report');
    await page.goto(`/vehicles/reports?vehicle_id=${id}&date_from=2026-10-01&date_to=2026-10-31&day=2026-10-15`);
    await expect(page.getByRole('heading',{name:'Траты 2026-10-15'})).toBeVisible();
    const calendar = page.locator('.spend-calendar');
    await expect(calendar).toBeVisible();
    await expect(calendar.locator('.is-selected')).toHaveAttribute('aria-label', /15.10.2026.*выбран/);
    await expect(calendar.locator('.is-selected .spend-calendar__dot')).toHaveCount(2);
    await calendar.getByRole('link', {name: /16.10.2026/}).click();
    await expect(page.getByRole('heading',{name:'Траты 2026-10-16'})).toBeVisible();
    await expect(calendar.locator('.is-selected')).toHaveAttribute('aria-label', /16.10.2026.*выбран/);
    await calendar.getByRole('link', {name: /15.10.2026/}).click();
    await page.evaluate(()=>scrollTo(0,0));
    await capture('calendar-day');
    await calendar.screenshot({path:path.join(root,`${width}-${theme}-calendar-detail.png`),animations:'disabled',style:'.telegram-app-bar, .telegram-bottom-nav { visibility: hidden !important; }'});
    await page.getByLabel('Не возмещено',{exact:true}).check();
    await page.getByRole('button',{name:'Показать',exact:true}).click();
    await expect(page.locator('article')).toHaveCount(1);
    await capture('unpaid');
    const downloadPromise=page.waitForEvent('download',{timeout:15000});
    await page.getByRole('link',{name:'Скачать CSV'}).click();
    const download=await downloadPromise; expect(download.suggestedFilename()).toBe('vehicle-report.csv');
    const csv=fs.readFileSync(await download.path(),'utf8');
    expect(csv.startsWith('\ufeffДата;')).toBe(true);
    expect(csv).toContain('Учебная заправка организации');
    expect(csv).not.toContain('Учебная личная заправка');
    expect(csv.trimEnd().split('\r\n').at(-1)).toBe('Итого;;;;;;120,50;;');
    await page.getByRole('button',{name:'Возмещено',exact:true}).click();
    await page.waitForURL(/reports/);
    await page.goto(`/vehicles/${id}?tab=expenses`);
    await expect(page.getByRole('button',{name:'Снять отметку возмещения',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Снять отметку возмещения',exact:true}).click();
    await expect(page.getByRole('button',{name:'Возмещено',exact:true})).toBeVisible();

  });
}
