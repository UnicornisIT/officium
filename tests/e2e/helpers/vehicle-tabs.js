const { expect } = require('@playwright/test');

// Vehicle tabs may move into More at any viewport width.
async function revealVehicleTab(page, name) {
  await page.evaluate(() => document.fonts.ready);
  const nav = page.getByRole('navigation', { name: 'Вкладки автомобиля' });
  const menu = nav.locator('.dropdown-menu');
  await menu.evaluate(async element => Promise.allSettled(element.getAnimations().map(animation => animation.finished)));
  const link = nav.getByRole('link', { name, exact: true });
  if (!await link.isVisible()) {
    await nav.evaluate(element => element.scrollIntoView({block:'center',behavior:'instant'}));
    const more = nav.getByRole('button', { name: 'Ещё', exact: true });
    if (await more.getAttribute('aria-expanded') !== 'true') await more.click();
    await menu.evaluate(async element => Promise.allSettled(element.getAnimations().map(animation => animation.finished)));
  }
  await expect(link).toBeVisible();
  return link;
}

async function openVehicleTab(page, name, options = {}) {
  const link = await revealVehicleTab(page, name);
  const destination = new URL(await link.getAttribute('href'), page.url()).href;
  await Promise.all([page.waitForURL(destination, {waitUntil:'domcontentloaded'}), link.click(options)]);
}

module.exports = { revealVehicleTab, openVehicleTab };
