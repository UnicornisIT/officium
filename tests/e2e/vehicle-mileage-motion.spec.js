const { test, expect } = require('@playwright/test');

for (const path of ['/', '/vehicles']) {
  test(`native mileage snooze returns to ${path}`, async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    await page.goto('/dev-login/user');
    await page.goto('/vehicles/new');
    await page.getByLabel('Название', { exact: true }).fill(`Отсрочка ${testInfo.testId}`);
    await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
    await expect(page).toHaveURL(/\/vehicles\/\d+$/);
    await page.route('**/js/vehicle-mileage.js*', route => route.abort());
    await page.goto(path);
    await page.locator('.mileage-reminder button[value="snooze"]').click();
    await expect(page).toHaveURL(new RegExp(`${path === '/' ? '/$' : '/vehicles$'}`));
    await expect(page.locator('.mileage-reminder')).toHaveCount(0);
  });
}

for (const path of ['/', '/vehicles']) {
  for (const action of ['save', 'snooze']) {
    for (const reducedMotion of ['no-preference', 'reduce']) {
      test(`mileage reminder ${path} ${action} ${reducedMotion}`, async ({ page }, testInfo) => {
        test.setTimeout(120_000);
        await page.emulateMedia({ reducedMotion });
        if (path === '/vehicles') await page.setViewportSize({ width: 390, height: 844 });
        await page.goto('/dev-login/user');
        await page.goto('/vehicles/new');
        await page.getByLabel('Название', { exact: true }).fill(`Плавность ${testInfo.testId}`);
        await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
        await expect(page).toHaveURL(/\/vehicles\/\d+$/);
        await page.goto(path);
        const reminder = page.locator('.mileage-reminder');
        const inputs = reminder.locator('[data-vehicle-km]');
        for (const input of await inputs.all()) await input.fill('100');
        const button = reminder.locator(`button[value="${action}"]`);
        await page.route('**/vehicles/mileage', route => route.fulfill({
          status: 503, contentType: 'application/json', body: JSON.stringify({ error: 'Не удалось сохранить данные автомобиля. Повторите попытку.' }),
        }));
        await button.click();
        await expect(reminder.getByRole('alert')).toBeVisible();
        for (const input of await inputs.all()) await expect(input).toHaveValue('100');
        await expect(button).toBeEnabled();
        await page.screenshot({ path: testInfo.outputPath('reminder-error.png') });
        await page.unroute('**/vehicles/mileage');
        await reminder.evaluate(element => {
          window.mileageFrames = [];
          const animate = element.animate.bind(element);
          element.animate = (keyframes, options) => {
            const animation = animate(keyframes, options);
            animation.pause();
            for (const progress of [0, 0.5, 0.99]) {
              animation.currentTime = options.duration * progress;
              window.mileageFrames.push({
                height: element.getBoundingClientRect().height,
                opacity: Number(getComputedStyle(element).opacity),
                margin: parseFloat(getComputedStyle(element).marginBottom),
              });
            }
            animation.currentTime = 0;
            animation.play();
            return animation;
          };
        });
        await button.click();
        await expect(reminder).toHaveCount(0);
        const frames = await page.evaluate(() => window.mileageFrames);
        if (reducedMotion === 'reduce') {
          expect(frames).toHaveLength(0);
        } else {
          expect(frames.length).toBeGreaterThan(2);
          const first = frames[0];
          const middle = frames[1];
          const last = frames.at(-1);
          expect(middle.height).toBeLessThan(first.height);
          expect(middle.height).toBeGreaterThan(last.height);
          expect(last.height).toBeLessThan(first.height / 2);
          expect(last.opacity).toBeLessThan(first.opacity);
          expect(last.margin).toBeLessThan(first.margin);
        }
        await expect(page).toHaveURL(new RegExp(`${path === '/' ? '/$' : '/vehicles$'}`));
        await page.reload();
        await expect(reminder).toHaveCount(0);
      });
    }
  }
}

test('partial mileage update keeps the remaining vehicle on the dashboard', async ({ page }, testInfo) => {
  test.setTimeout(120_000);
  await page.goto('/dev-login/user');
  const ids = [];
  for (const suffix of ['Первый', 'Второй']) {
    await page.goto('/vehicles/new');
    await page.getByLabel('Название', { exact: true }).fill(`${suffix} ${testInfo.testId}`);
    await page.getByRole('button', { name: 'Сохранить автомобиль', exact: true }).click();
    await expect(page).toHaveURL(/\/vehicles\/\d+$/);
    ids.push(new URL(page.url()).pathname.split('/').pop());
  }
  await page.goto('/');
  const reminder = page.locator('.mileage-reminder');
  await page.locator(`#km${ids[0]}`).fill('100');
  await reminder.locator('button[value="save"]').click();
  await expect(page.locator(`#km${ids[0]}`)).toHaveCount(0);
  await expect(page.locator(`#km${ids[1]}`)).toHaveValue('');
  await expect(reminder.locator('button[value="save"]')).toBeEnabled();
  await expect(page).toHaveURL(/\/$/);
  await page.locator(`#km${ids[1]}`).fill('200');
  await reminder.locator('button[value="save"]').click();
  await expect(reminder).toHaveCount(0);
});
