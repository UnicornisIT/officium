const fs = require('node:fs');
const {test, expect} = require('@playwright/test');
const { seedPersonalAccount } = require('./helpers/personal-account');

test.use({video: 'off', trace: 'off'});
for (const width of [390, 1440]) for (const theme of ['light', 'dark']) {
  test(`vehicle notification preference ${width} ${theme}`, async ({page}, info) => {
    await page.setViewportSize({width, height: 1000});
    await page.addInitScript(t => localStorage.setItem('officiumTheme', t), theme);
    await page.route('https://telegram.org/js/telegram-web-app.js', r => r.fulfill({contentType: 'application/javascript', body: ''}));
    await page.goto('/dev-login/user', {waitUntil: 'domcontentloaded'});
    seedPersonalAccount();
    await page.goto('/profile', {waitUntil: 'domcontentloaded'});
    const checkbox = page.getByRole('checkbox', {name: 'Напоминания об автомобилях в Telegram'});
    const save = page.getByRole('region', {name: 'Уведомления', exact: true}).getByRole('button', {name: 'Сохранить', exact: true});
    await checkbox.check();
    await save.click();
    await expect(checkbox).toBeChecked();
    await checkbox.uncheck();
    await save.click();
    await page.reload();
    await expect(checkbox).not.toBeChecked();
    await checkbox.check();
    await save.click();
    await expect(checkbox).toBeChecked();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    const box = await checkbox.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(width);
    fs.mkdirSync('docs/qa/vehicles-stage6', {recursive: true});
    await page.screenshot({path: `docs/qa/vehicles-stage6/profile-${info.project.name}-${width}-${theme}.png`, fullPage: true, animations: 'disabled'});
  });
}
