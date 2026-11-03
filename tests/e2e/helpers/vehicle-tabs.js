const { expect } = require('@playwright/test');

// Vehicle tabs may move into More at any viewport width.
async function revealVehicleTab(page, name) {
  const nav = page.getByRole('navigation', { name: 'Вкладки автомобиля' });
  const link = nav.getByRole('link', { name, exact: true });
  if (!await link.isVisible()) {
    const more = nav.getByRole('button', { name: 'Ещё', exact: true });
    if (await more.getAttribute('aria-expanded') !== 'true') await more.click();
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
