const fs=require('node:fs');
const {test,expect}=require('@playwright/test');
test.use({video:'off',trace:'off'});
for(const width of [390,1440]) for(const theme of ['light','dark']) {
 test(`documents ${width} ${theme}`,async({page},info)=>{
  test.setTimeout(60000);
  await page.setViewportSize({width,height:1000});
  await page.addInitScript(t=>localStorage.setItem('officiumTheme',t),theme);
  await page.route('https://telegram.org/js/telegram-web-app.js',r=>r.fulfill({contentType:'application/javascript',body:''}));
  await page.goto('/dev-login/user',{waitUntil:'domcontentloaded'});
  await page.goto('/vehicles/new',{waitUntil:'domcontentloaded'});
  await page.locator('#v_name').fill(`Документы ${info.project.name} ${width} ${theme} ${Date.now()}`);
  await page.getByRole('button',{name:'Сохранить автомобиль',exact:true}).click({noWaitAfter:true});
  await page.waitForURL(/\/vehicles\/\d+$/,{waitUntil:'domcontentloaded'});
  await page.getByRole('link',{name:'Документы',exact:true}).click({noWaitAfter:true});
  await page.getByText('Добавить документ',{exact:true}).click({noWaitAfter:true});
  const form=page.locator('.vehicle-document-form');
  await form.locator('[name=title]').fill('Тестовый полис');
  await form.locator('[name=issued_date]').fill('2025-01-01');
  await form.locator('[name=expires_at]').fill('2026-01-01');
  await form.locator('[name=amount]').fill('15000');
  await form.locator('[name=amount]').press('Tab');
  await expect(form.locator('[name=amount]')).toHaveValue('15 000,00');
  await form.getByRole('button',{name:'Сохранить документ'}).click({noWaitAfter:true});
  await expect(page.locator('.vehicle-document-card')).toContainText('Тестовый полис');
  await expect(page.locator('.vehicle-document-card .badge')).toHaveText('Просрочено');
  await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  fs.mkdirSync('docs/qa/vehicles-stage5',{recursive:true});
  await page.screenshot({path:`docs/qa/vehicles-stage5/${info.project.name}-${width}-${theme}.png`,fullPage:true});

 });
}
