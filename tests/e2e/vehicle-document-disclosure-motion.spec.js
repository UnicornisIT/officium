const { test, expect } = require('@playwright/test');

test.use({ reducedMotion: 'no-preference', video: 'off' });

for (const width of [390, 1280]) {
  test(`document form opens, closes and reverses smoothly at ${width}px`, async ({ page, baseURL }, info) => {
    await page.setViewportSize({ width, height: 844 });
    const origin = new URL(baseURL).origin;
    await page.route(url => url.origin !== origin, route => route.abort());
    await page.goto('/dev-login/user', { waitUntil: 'domcontentloaded' });
    const csrf = await page.locator('meta[name="csrf-token"]').getAttribute('content');
    const vehicle = await page.request.post('/vehicles/new', {
      form: { csrf_token: csrf, name: `Плавная форма ${info.project.name} ${width} ${Date.now()}` },
    });
    const vehiclePath = new URL(vehicle.url()).pathname;
    expect(vehiclePath).toMatch(/^\/vehicles\/\d+$/);
    await page.goto(`${vehiclePath}?tab=documents`, { waitUntil: 'domcontentloaded' });
    const summary = page.getByText('Добавить документ', { exact: true });
    const details = summary.locator('..');
    const form = details.locator('.vehicle-document-form');
    const closedHeight = (await details.boundingBox()).height;

    // Pause at the event boundary so intermediate frames can be measured reliably.
    await summary.evaluate(element => element.addEventListener('click', () => {
      element.parentElement.getAnimations().forEach(animation => animation.pause());
    }));
    async function halfway() {
      return details.evaluate(async element => {
        const animation = element.getAnimations()[0];
        if (!animation) throw new Error('Expected a form height animation');
        const duration = animation.effect.getTiming().duration;
        animation.currentTime = duration / 2;
        await new Promise(requestAnimationFrame);
        return element.getBoundingClientRect().height;
      });
    }
    async function finish() {
      await details.evaluate(element => element.getAnimations().forEach(animation => animation.finish()));
      await expect(details).not.toHaveClass(/vehicle-disclosure-moving/);
    }
    await summary.click();
    await expect(summary).toHaveAttribute('aria-expanded', 'true');
    const openingHeight = await halfway();
    expect(openingHeight).toBeGreaterThan(closedHeight + 10);
    await finish();
    const openHeight = (await details.boundingBox()).height;
    expect(openingHeight).toBeLessThan(openHeight - 10);
    await expect(form).toBeVisible();

    await summary.click();
    const closingHeight = await halfway();
    expect(closingHeight).toBeGreaterThan(closedHeight + 10);
    expect(closingHeight).toBeLessThan(openHeight - 10);
    await summary.evaluate(element => element.click());
    expect(Math.abs((await details.boundingBox()).height - closingHeight)).toBeLessThan(2);
    await finish();
    await expect(summary).toHaveAttribute('aria-expanded', 'true');
    await summary.click();
    await finish();
    await expect(form).toBeHidden();
    expect(Math.abs((await details.boundingBox()).height - closedHeight)).toBeLessThan(1);

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await summary.click();
    await expect(form).toBeVisible();
    expect(await details.evaluate(element => element.getAnimations().length)).toBe(0);
    const payer = form.getByRole('button', { name: /^Кто оплачивает/ });
    await payer.click();
    await expect(form.locator('.dropdown-menu')).toBeVisible();
    await payer.press('Escape');
    await expect(form.locator('.dropdown-menu')).toBeHidden();
    await expect(summary).toHaveAttribute('aria-expanded', 'true');
    await summary.press('Escape');
    await expect(form).toBeHidden();

    await details.evaluate(element => { element.open = true; });
    await form.locator('[name="title"]').fill('Документ после программного раскрытия');
    await summary.click();
    await expect(form).toBeHidden();
  });
}
