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
  await (await revealVehicleTab(page, name)).click(options);
}

module.exports = { revealVehicleTab, openVehicleTab };
