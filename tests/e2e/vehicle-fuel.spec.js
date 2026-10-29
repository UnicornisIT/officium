const fs = require('node:fs');
const path = require('node:path');
const {test, expect} = require('@playwright/test');

for (const width of [390,1440]) for (const theme of ['light','dark']) {
  test(`fuel ${width} ${theme}`, async ({page}, info) => {
    test.setTimeout(180000);
    await page.setViewportSize({width,height:1000});
    await page.addInitScript(t => localStorage.setItem('officiumTheme',t),theme);
    await page.goto('/dev-login/user', {waitUntil:'domcontentloaded'});
    await page.goto('/vehicles/new', {waitUntil:'domcontentloaded'});
    await page.locator('#v_name').fill(`Топливо ${width} ${theme} ${info.project.name} ${Date.now()}`);
    await page.locator('#v_company').check();
    await page.getByRole('button',{name:'Сохранить автомобиль',exact:true}).click();
    await page.waitForURL(/\/vehicles\/\d+$/);
    const id=page.url().split('/').pop();
    await page.getByRole('link',{name:'Заправки',exact:true}).click();
    await page.getByText('Добавить заправку',{exact:true}).click();
    const form=page.locator('.vehicle-fuel-form');
    await form.getByLabel('Дата заправки').fill('2026-03-01');
    await form.getByLabel('Пробег (км)').fill('1000');
    await form.getByLabel('Литры',{exact:true}).fill('40');
    await form.getByLabel('Цена за литр').fill('50');
    await expect(form.locator('[name="total_amount"]')).toHaveValue('2000.00');
    await form.getByLabel('АЗС',{exact:true}).fill('Учебная АЗС');
    const root=path.join('docs','qa','vehicles-stage4');
    fs.mkdirSync(root,{recursive:true});
    const capture=async name => {
      await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await page.evaluate(() => { document.activeElement?.blur(); scrollTo({top:0,left:0,behavior:'instant'}); });
      await page.screenshot({path:path.join(root,`${info.project.name}-${width}-${theme}-${name}.png`),fullPage:true,animations:'disabled',style:width===390 ? 'body { position:relative !important; } .telegram-bottom-nav { position:absolute !important; }' : undefined});
    };
    await capture('form');
    await form.getByRole('button',{name:'Сохранить заправку'}).click();
    await expect(page.locator('.vehicle-fuel-history')).toContainText('Учебная АЗС');
    await page.getByText('Добавить заправку',{exact:true}).click();
    await form.getByLabel('Дата заправки').fill('2026-03-02');
    await form.getByLabel('Пробег (км)').fill('1500');
    await form.getByLabel('Литры',{exact:true}).fill('50');
    await form.getByLabel('Цена за литр').fill('60');
    await form.getByLabel('Организация',{exact:true}).check();
    await form.getByRole('button',{name:'Сохранить заправку'}).click();
    await expect(page.locator('.vehicle-fuel-history')).toContainText('10.00');
    await capture('history');
    await page.getByRole('link',{name:'Редактировать',exact:true}).last().click();
    await page.getByLabel('Литры',{exact:true}).fill('41');
    await page.getByRole('button',{name:'Сохранить заправку'}).click();
    await expect(page.locator('.vehicle-fuel-history')).toContainText('41.000');
    await page.getByRole('link',{name:'Пробег',exact:true}).click();
    await expect(page.locator('tbody')).toContainText('Заправка');
    await capture('mileage');
    await page.getByRole('link',{name:'Обзор',exact:true}).click();
    await capture('overview');
    await page.goto('/dev-login/superadmin', {waitUntil:'domcontentloaded'});
    expect((await page.request.get(`/vehicles/${id}/refueling/new`)).status()).toBe(404);
  });
}
