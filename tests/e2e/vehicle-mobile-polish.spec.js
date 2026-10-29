const {test, expect} = require('@playwright/test');

test('maintenance disclosures animate, reverse and respect reduced motion', async ({page}, info) => {
  await page.setViewportSize({width:390, height:844});
  await page.goto('/dev-login/user');
  await page.goto('/vehicles/new');
  await page.getByLabel('Название', {exact:true}).fill(`Плавность ${info.project.name}`);
  await page.getByRole('button', {name:'Сохранить автомобиль', exact:true}).click();
  await page.waitForURL(/\/vehicles\/\d+$/);
  await page.goto(`${page.url()}?tab=maintenance`);
  const panel = page.locator('.vehicle-disclosure').filter({hasText:'Добавить из шаблона'});
  const summary = panel.locator('summary');
  await summary.click();
  await expect(summary).toHaveAttribute('aria-expanded','true');
  await expect(panel).not.toHaveClass(/vehicle-disclosure-moving/);
  await expect(panel.getByLabel('Последняя замена', {exact:true})).toBeVisible();
  // Dispatch consecutive clicks before the animation finishes to exercise reversal.
  await summary.evaluate(el => {el.click(); el.click(); el.click();});
  await expect(summary).toHaveAttribute('aria-expanded','false');
  await expect(panel).not.toHaveAttribute('open');
  await summary.focus();
  await summary.press('Enter');
  await expect(panel).not.toHaveClass(/vehicle-disclosure-moving/);
  await panel.getByLabel('Пробег при замене').focus();
  await page.keyboard.press('Escape');
  await expect(summary).toBeFocused();
  await expect(panel).not.toHaveAttribute('open');
  await page.emulateMedia({reducedMotion:'reduce'});
  await summary.click();
  await expect(panel).toHaveAttribute('open');
  await expect(panel).not.toHaveClass(/vehicle-disclosure-moving/);
});

test('mobile mileage grouping, history actions and scrolling header', async ({page}, info) => {
  await page.setViewportSize({width:390, height:844});
  await page.goto('/dev-login/user');
  await page.goto('/vehicles/new');
  await page.getByLabel('Название', {exact:true}).fill(`Мобильная проверка ${info.project.name}`);
  await page.getByRole('button', {name:'Сохранить автомобиль', exact:true}).click();
  await page.waitForURL(/\/vehicles\/\d+$/);
  const vehicleUrl = page.url();
  await page.goto(`${vehicleUrl}?tab=mileage`);
  await page.getByLabel('Пробег (км)', {exact:true}).fill('123654');
  await expect(page.getByLabel('Пробег (км)', {exact:true})).toHaveValue('123 654');
  await page.getByRole('button', {name:'Добавить замер'}).click();
  await expect(page.locator('table')).toContainText('123 654');
  await page.goto(`${vehicleUrl}/service`);
  await expect(page.getByLabel('Пробег, км', {exact:true})).toHaveValue('123 654');
  await page.getByLabel('Сумма (необязательно)').fill('100');
  await page.getByRole('button', {name:'Сохранить обслуживание'}).click();
  await expect(page.locator('.vehicle-service-entry')).toContainText('123 654 км');
  const spend = page.getByRole('link', {name:'Расход на обслуживание', exact:true});
  await expect(spend).toHaveClass(/btn/);
  await page.evaluate(() => scrollTo({top: document.body.scrollHeight, behavior:'instant'}));
  expect(await page.locator('nav.navbar').evaluate(el => el.getBoundingClientRect().bottom)).toBeLessThan(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('mobile-history.png'), fullPage:false});
});
