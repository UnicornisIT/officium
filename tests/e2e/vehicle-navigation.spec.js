const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('officiumTheme', 'dark'));
  await page.route('https://telegram.org/js/telegram-web-app.js', route => route.fulfill({ body: '', contentType: 'application/javascript' }));
  await page.goto('/dev-login/user');
  await page.goto('/vehicles/new');
  await page.getByLabel('Название', { exact: true }).fill(`Навигация ${Date.now()}`);
  await page.getByRole('button', { name: 'Сохранить автомобиль' }).click();
  await expect(page).toHaveURL(/\/vehicles\/\d+$/);
});

test('responsive navigation keeps every section on one row and marks overflow active', async ({ page }) => {
  const nav = page.getByRole('navigation', { name: 'Вкладки автомобиля' });
  const more = nav.getByRole('button', { name: 'Ещё', exact: true });
  for (const width of [1440, 1100, 768, 390, 320, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect.poll(async () => nav.evaluate(element => {
      const links = [...element.querySelectorAll(':scope > a, :scope > .dropdown')];
      return Math.max(...links.map(link => link.getBoundingClientRect().top)) -
        Math.min(...links.map(link => link.getBoundingClientRect().top));
    })).toBeLessThan(2);
    await expect(nav.locator('[data-vehicle-section]')).toHaveCount(9);
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (width === 1440) await expect(nav.locator(':scope > a')).toHaveCount(4);
    await more.click();
    await expect(more).toHaveAttribute('aria-expanded', 'true');
    const bounds = await nav.locator('.dropdown-menu').boundingBox();
    expect(bounds.x).toBeGreaterThanOrEqual(0);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(width);
    await page.keyboard.press('Escape');
    await expect(more).toBeFocused();
    await expect(more).toHaveAttribute('aria-expanded', 'false');
  }
  await page.setViewportSize({ width: 320, height: 1000 });
  const fuel = nav.locator('[data-vehicle-section="fuel"]');
  await expect(fuel).toBeHidden();
  await more.click();
  await fuel.click();
  await expect(more).toHaveClass(/active/);
  await expect(nav.locator('[aria-current="page"]')).toHaveCount(1);
  await expect(nav.locator('[aria-current="page"]')).toHaveAttribute('data-vehicle-section', 'fuel');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(nav.locator(':scope > a.active')).toHaveAttribute('data-vehicle-section', 'fuel');
  await expect(more).not.toHaveClass(/active/);
});

test('all nine URL sections, dropdown keyboard and dismissal remain available', async ({ page }) => {
  test.setTimeout(120_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const nav = page.getByRole('navigation', { name: 'Вкладки автомобиля' });
  const more = nav.getByRole('button', { name: 'Ещё', exact: true });
  for (const key of ['expenses', 'mileage', 'fuel', 'maintenance', 'defects', 'documents', 'photos', 'tco', 'overview']) {
    const link = nav.locator(`[data-vehicle-section="${key}"]`);
    if (!await link.isVisible()) await more.click();
    await link.click();
    await expect(page).toHaveURL(new RegExp(`tab=${key}$`));
    await expect(nav.locator('[aria-current="page"]')).toHaveAttribute('data-vehicle-section', key);
    await expect(nav.locator('[aria-current="page"]')).toHaveClass(/active/);
    await expect(more).toHaveAttribute('aria-expanded', 'false');
    if (['maintenance', 'defects', 'documents', 'photos', 'tco'].includes(key)) {
      await expect(more).toHaveClass(/active/);
      await expect(more).toHaveCSS('color', 'rgb(255, 255, 255)');
      await more.click();
      await expect(nav.locator('[aria-current="page"]')).toHaveCSS('color', 'rgb(226, 232, 240)');
      const selected = nav.locator('[aria-current="page"]');
      const normal = nav.locator('.dropdown-item:not(.active)').first();
      await expect.poll(async () => (await selected.evaluate(element => getComputedStyle(element).backgroundColor)) !==
        (await normal.evaluate(element => getComputedStyle(element).backgroundColor))).toBe(true);
      await page.keyboard.press('Escape');
      await expect(page.locator('.vehicle-detail-grid > section').nth(1).locator('h2').first()).toBeVisible();
    } else await expect(more).not.toHaveClass(/active/);
  }
  await more.focus();
  await page.keyboard.press('ArrowDown');
  await expect(nav.locator('.dropdown-item').first()).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(nav.locator('.dropdown-item').nth(1)).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(more).toBeFocused();
  await more.click();
  await page.locator('h1').click();
  await expect(more).toHaveAttribute('aria-expanded', 'false');
  await expect(page.getByRole('link', { name: 'Редактировать', exact: true })).toBeVisible();
  await page.screenshot({ path: '.tmp/vehicle-navigation-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 1000 });
  await more.click();
  await page.screenshot({ path: '.tmp/vehicle-navigation-mobile.png', fullPage: true });
});

test('More animates, highlights hovered items and respects reduced motion', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const nav = page.getByRole('navigation', { name: 'Вкладки автомобиля' });
  const more = nav.getByRole('button', { name: 'Ещё', exact: true });
  await more.evaluate(button => {
    const menu = document.getElementById('vehicleMoreMenu');
    for (const event of ['shown.bs.dropdown', 'hide.bs.dropdown']) {
      button.addEventListener(event, () => {
        button.dataset[event.startsWith('shown') ? 'openAnimations' : 'closeAnimations'] = menu.getAnimations().length;
      });
    }
  });
  await more.click();
  await expect(more).toHaveAttribute('data-open-animations', '1');
  await expect(page.locator('link[href*="css/vehicles.css"]')).toHaveAttribute('href', /v=navigation-menu-4/);
  const item = nav.locator('.dropdown-item').first();
  const nextItem = nav.locator('.dropdown-item').nth(1);
  const firstBounds = await item.boundingBox();
  const nextBounds = await nextItem.boundingBox();
  expect(nextBounds.y - firstBounds.y - firstBounds.height).toBeCloseTo(8, 0);
  const background = await item.evaluate(element => getComputedStyle(element).backgroundColor);
  await item.hover();
  await expect.poll(() => item.evaluate(element => getComputedStyle(element).backgroundColor)).not.toBe(background);
  await page.keyboard.press('Escape');
  await expect(more).toHaveAttribute('data-close-animations', '1');
  await expect(more).toHaveAttribute('aria-expanded', 'false');
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await more.click();
  await expect(more).toHaveAttribute('data-open-animations', '0');
  await page.keyboard.press('Escape');
  await expect(more).toHaveAttribute('aria-expanded', 'false');
});

test('archive disclosure animates and compact left-aligned reason submits selected value', async ({ page }) => {
  const panel = page.locator('.vehicle-archive-panel');
  const summary = panel.locator('summary');
  await summary.click();
  await expect(summary).toHaveAttribute('aria-expanded', 'true');
  await expect(panel.locator('details')).not.toHaveClass(/vehicle-disclosure-moving/);
  const toggle = page.locator('#archiveReasonToggle');
  await expect(toggle).toHaveText('Продано');
  await expect(toggle).toHaveCSS('text-align', 'left');
  expect((await toggle.boundingBox()).width).toBeLessThanOrEqual(200);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await toggle.click();
    const writtenOff = page.locator('#archiveReasonMenu').getByRole('button', { name: 'Списано', exact: true });
    await expect(writtenOff).toHaveCSS('text-align', 'left');
    await expect.poll(() => page.locator('#archiveReasonMenu').evaluate(menu => menu.getAnimations().length)).toBe(0);
    const first = await page.locator('#archiveReasonMenu > button').first().boundingBox();
    const second = await writtenOff.boundingBox();
    expect(second.y - first.y - first.height).toBeCloseTo(8, 0);
    const other = page.locator('#archiveReasonMenu').getByRole('button', { name: 'Другое', exact: true });
    const normalBackground = await other.evaluate(button => getComputedStyle(button).backgroundColor);
    await other.hover();
    await expect.poll(() => other.evaluate(button => getComputedStyle(button).backgroundColor)).not.toBe(normalBackground);
    await writtenOff.click();
    await expect(toggle).toHaveText('Списано');
    await expect(page.locator('#archiveReason')).toHaveValue('written_off');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  }
  await summary.click();
  await expect(panel.locator('form')).toBeHidden();
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await summary.click();
  await expect(panel.locator('details')).not.toHaveClass(/vehicle-disclosure-moving/);
  await page.getByLabel('Примечание', { exact: true }).fill('Проверка архивирования');
  await panel.getByRole('button', { name: 'В архив', exact: true }).click();
  await expect(page.locator('.alert-secondary')).toContainText('Списано');
  await panel.getByRole('button', { name: 'Восстановить автомобиль', exact: true }).click();
  await expect(panel.locator('summary')).toBeVisible();
});
