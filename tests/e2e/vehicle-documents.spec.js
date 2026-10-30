const fs=require('node:fs');
const {test,expect}=require('@playwright/test');
test.use({video:'off'});
for(const width of [390,1440]) for(const theme of ['light','dark']) {
 test(`documents ${width} ${theme}`,async({page},info)=>{
  test.setTimeout(180000);
  await page.setViewportSize({width,height:1000});
  await page.addInitScript(t=>localStorage.setItem('officiumTheme',t),theme);
  await page.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({contentType:'application/javascript',body:''}));
  await page.goto('/dev-login/user',{waitUntil:'domcontentloaded'});
  await page.goto('/vehicles/new',{waitUntil:'domcontentloaded'});
  await page.locator('#v_name').fill(`Документы ${info.project.name} ${width} ${theme} ${Date.now()}`);
  await page.getByRole('button',{name:'Сохранить автомобиль',exact:true}).click();
  await page.waitForURL(/\/vehicles\/\d+$/);
  await page.getByRole('link',{name:'Документы',exact:true}).click();
  await page.getByText('Добавить документ',{exact:true}).click();
  const form=page.locator('.vehicle-document-form');
  await form.locator('[name=title]').fill('Тестовый полис');
  await form.locator('[name=issued_date]').fill('2025-01-01');
  await form.locator('[name=expires_at]').fill('2026-01-01');
  await form.locator('[name=amount]').fill('15000');
  await form.getByRole('button',{name:'Сохранить документ'}).click();
  await expect(page.locator('.vehicle-document-card')).toContainText('Тестовый полис');
  await expect(page.locator('.vehicle-document-card .badge')).toHaveText('Просрочено');
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  fs.mkdirSync('docs/qa/vehicles-stage5',{recursive:true});
  await page.screenshot({path:`docs/qa/vehicles-stage5/${info.project.name}-${width}-${theme}.png`,fullPage:true});
  await page.getByText('Редактировать документ',{exact:true}).click();
  await page.locator('.vehicle-document-card [name=title]').fill('Продлённый полис');
  await page.locator('.vehicle-document-card [name=title]').blur();
  expect(await page.locator('.vehicle-document-card form').evaluate(f=>Array.from(f.elements).filter(e=>e.willValidate&&!e.validity.valid).map(e=>({name:e.name,value:e.value,error:e.validationMessage})))).toEqual([]);
  await page.locator('.vehicle-document-card').getByRole('button',{name:'Сохранить документ'}).click();
  await expect(page.locator('.vehicle-document-card')).toContainText('Продлённый полис');
 });
}

