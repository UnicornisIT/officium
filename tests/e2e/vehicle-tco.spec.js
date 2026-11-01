const fs = require('node:fs');
const {randomUUID} = require('node:crypto');
const {test,expect} = require('@playwright/test');
test.use({video:'off',trace:'retain-on-failure'});
for (const width of [390,1440]) for (const theme of ['light','dark']) {
  test(`tco ${width} ${theme}`, async ({page},info) => {
    await page.setViewportSize({width,height:1000});
    await page.addInitScript(t=>localStorage.setItem('officiumTheme',t),theme);
    await page.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({contentType:'application/javascript',body:''}));
    await page.goto('/dev-login/user',{waitUntil:'domcontentloaded'});
    await page.goto('/vehicles/new',{waitUntil:'domcontentloaded'});
    const name=`TCO ${info.project.name} ${width} ${theme} ${info.retry} ${randomUUID()}`;
    await page.locator('#v_name').fill(name);
    await page.getByRole('button',{name:'Сохранить автомобиль',exact:true}).click({noWaitAfter:true});
    await page.waitForURL(/\/vehicles\/\d+$/,{waitUntil:'domcontentloaded'});
    const id=page.url().match(/vehicles\/(\d+)/)[1];
    // Seed through the same CSRF-protected routes used by the UI.
    const token=await page.locator('input[name=csrf_token]').first().inputValue();
    const year=new Date().getFullYear();
    for (const [day,km] of [[1,1000],[20,2000]]) {
      const r=await page.request.post(`/vehicles/${id}/odometer`,{form:{csrf_token:token,reading_date:`${year}-01-${String(day).padStart(2,'0')}`,odometer_km:String(km)}});
      expect(r.ok()).toBeTruthy();
    }
    const spend=await page.request.post(`/vehicles/${id}/spend`,{form:{csrf_token:token,payer:'self',amount:'10000',cost_date:`${year}-01-10`,kind:'fuel',title:'Топливо QA'}});
    expect(spend.ok()).toBeTruthy();
    await page.goto(`/vehicles/tco?vehicle_id=${id}&date_from=${year}-01-01&date_to=${year}-12-31`,{waitUntil:'domcontentloaded'});
    await expect(page.getByRole('heading',{name:'Аналитика автомобилей'})).toBeVisible();
    await expect(page.locator('.vehicle-tco-table tbody')).toContainText(name);
    await expect(page.locator('.vehicle-tco-summary')).toContainText('10 000');
    await expect.poll(()=>page.evaluate(()=>[...document.querySelectorAll('canvas')].filter(c=>window.Chart?.getChart(c)).length)).toBe(3);
    expect(await page.locator('canvas').count()).toBe(3);
    for (const canvas of await page.locator('canvas').all()) {
      expect(await canvas.evaluate(c=>c.getContext('2d').getImageData(0,0,c.width,c.height).data.some(v=>v!==0))).toBeTruthy();
    }
    await page.locator('summary').first().click();
    await page.locator('[data-chart=monthly]').first().focus();
    await expect.poll(()=>page.evaluate(()=>Chart.getChart(document.getElementById('tco-monthly')).tooltip.opacity)).toBe(1);
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    const reportURL=page.url();
    const exportURL=new URL(await page.getByRole('link',{name:'Скачать CSV'}).getAttribute('href'),reportURL);
    expect(exportURL.searchParams.getAll('vehicle_id')).toEqual([id]);
    expect(exportURL.searchParams.get('date_from')).toBe(`${year}-01-01`);
    expect(exportURL.searchParams.get('date_to')).toBe(`${year}-12-31`);
    await expect(page.getByRole('link',{name:'Скачать CSV'})).toHaveAttribute('download','vehicle-tco.csv');
    const [download]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'Скачать CSV'}).click()]);
    expect(download.suggestedFilename()).toBe('vehicle-tco.csv');
    await expect(page).toHaveURL(reportURL);
    const text=fs.readFileSync(await download.path(),'utf8');
    expect(text).toContain('10000,00');
    expect(text).toContain(name);
    fs.mkdirSync('docs/qa/vehicles-stage7',{recursive:true});
    await page.locator('summary').first().focus();
    await page.locator('summary').first().click();
    await page.evaluate(()=>window.scrollTo(0,0));
    await page.screenshot({path:`docs/qa/vehicles-stage7/${info.project.name}-${width}-${theme}.png`,fullPage:true});
    await page.goto(`/vehicles/${id}?tab=tco`,{waitUntil:'domcontentloaded'});
    const cardURL=page.url();
    const [singleDownload]=await Promise.all([page.waitForEvent('download'),page.getByRole('link',{name:'Скачать CSV'}).click()]);
    expect(singleDownload.suggestedFilename()).toBe('vehicle-tco.csv');
    await expect(page).toHaveURL(cardURL);
    const single=fs.readFileSync(await singleDownload.path(),'utf8');
    expect(single).toContain(name);
    expect(single.split('\n').filter(line=>line.trim()).length).toBe(3);
    await expect(page.getByText('Прогноз на следующий месяц:',{exact:false})).toBeVisible();
    await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  });
}
