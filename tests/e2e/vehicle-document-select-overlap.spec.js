const path = require('node:path');
const { test, expect } = require('@playwright/test');

test.use({ viewport: { width: 1280, height: 800 }, reducedMotion: 'reduce', video: 'off' });

for (const isCompany of [false, true]) {
  test(`document payer opens below the fields (${isCompany ? 'company' : 'personal'})`, async ({ page, baseURL }, info) => {
    if (isCompany) await page.emulateMedia({ reducedMotion: 'no-preference' });
    const origin = new URL(baseURL).origin;
    await page.route(url => url.origin !== origin, route => route.abort());
    await page.route('https://telegram.org/js/telegram-web-app.js', route =>
      route.fulfill({ body: '', contentType: 'application/javascript' }));
    await page.goto('/dev-login/user', { waitUntil: 'domcontentloaded' });
    const csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content');
    const vehicle = await page.request.post('/vehicles/new', {
      form: {
        csrf_token: csrf,
        name: `Оплата документа ${info.project.name} ${Date.now()}`,
        default_payer: isCompany ? 'organization' : 'self',
        ...(isCompany ? { is_company: 'on' } : {}),
      },
    });
    const vehiclePath = new URL(vehicle.url()).pathname;
    expect(vehiclePath).toMatch(/^\/vehicles\/\d+$/);
    await page.goto(`${vehiclePath}?tab=documents`, { waitUntil: 'domcontentloaded' });
    const addDocument = page.getByText('Добавить документ', { exact: true });
    if (isCompany) await expect(addDocument).toHaveCSS('transition-property', /^transform,/);
    await addDocument.click();

    const form = page.locator('.vehicle-document-form');
    const payer = form.locator('[name="payer"]');
    const toggle = form.getByRole('button', { name: /^Кто оплачивает/ });
    const menu = form.locator('.dropdown-menu');
    if (isCompany) {
      await menu.evaluate(element => {
        element.addEventListener('transitionend', event => {
          if (event.propertyName === 'grid-template-rows') {
            element.dataset.motion = element.classList.contains('show') ? 'opened' : 'closed';
          }
        });
      });
    }
    await expect(payer).toHaveValue(isCompany ? 'organization' : 'self');
    await expect(toggle).toBeVisible();
    await toggle.click();
    await expect(menu).toBeVisible();
    if (isCompany) await expect(menu).toHaveAttribute('data-motion', 'opened');
    const menuBox = await menu.boundingBox();
    const toggleBox = await toggle.boundingBox();
    expect(menuBox.y).toBeGreaterThanOrEqual(toggleBox.y + toggleBox.height);
    for (const field of ['Дата истечения', 'Сумма']) {
      const fieldBox = await form.getByLabel(field, { exact: field === 'Сумма' }).boundingBox();
      expect(menuBox.y).toBeGreaterThanOrEqual(fieldBox.y + fieldBox.height + 4);
    }

    const organization = menu.getByRole('button', { name: 'Организация', exact: true });
    if (isCompany) {
      await expect(organization).toBeVisible();
      if (info.project.name === 'chromium' && process.env.V4_SCREENSHOT_DIR) {
        await form.locator('[name="expires_at"]').evaluate(input => {
          input.scrollIntoView({ block: 'start' });
          window.scrollBy(0, -120);
        });
        await expect(menu).toBeInViewport({ ratio: 1 });
        await page.screenshot({ path: path.join(process.env.V4_SCREENSHOT_DIR, 'v4-after.png') });
      }
      await toggle.click();
      await expect(menu).toHaveAttribute('data-motion', 'closed');
      await expect(menu).toBeHidden();
      await toggle.click();
      await expect(menu).toHaveAttribute('data-motion', 'opened');
    } else {
      await expect(organization).toHaveCount(0);
    }
    await menu.getByRole('button', { name: 'Самостоятельно', exact: true }).click();
    await expect(payer).toHaveValue('self');
    await expect(toggle).toHaveText('Самостоятельно');
    await expect(toggle).toBeFocused();
    await expect(menu).toBeHidden();

    if (isCompany) {
      await expect(menu).toHaveAttribute('data-motion', 'closed');
      await toggle.press('ArrowDown');
      await expect(menu.getByRole('button', { name: 'Самостоятельно', exact: true })).toBeFocused();
      await organization.click();
      await expect(payer).toHaveValue('organization');
      await expect(toggle).toHaveText('Организация');
    }
  });
}
