const { test, expect } = require('@playwright/test');

for (const width of [1440, 390]) {
  test(`documentation tracks headings in both scroll directions ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/documentation');
    const sections = await page.locator('[data-docs-section]').evaluateAll(nodes => nodes.map(node => node.id));
    const active = page.locator('.docs-toc a.active');
    const select = page.locator('#docsSectionSelect');
    for (const id of [...sections, ...sections.slice().reverse()]) {
      await page.locator(`#${id}`).evaluate(section => section.scrollIntoView({ block: 'start', behavior: 'instant' }));
      await expect(active).toHaveAttribute('href', `#${id}`);
      await expect(active).toHaveAttribute('aria-current', 'location');
      await expect(select).toHaveValue(`#${id}`);
    }
    // A large illustrated section remains active well past its heading.
    await page.locator('#vehicles').evaluate(section => {
      window.scrollTo({ top: scrollY + section.getBoundingClientRect().top + 600, behavior: 'instant' });
    });
    await expect(active).toHaveAttribute('href', '#vehicles');
    // The next heading is visible near the top, as in the reported screenshot.
    await page.locator('#cashflow').evaluate(section => {
      window.scrollTo({ top: scrollY + section.getBoundingClientRect().top - 160, behavior: 'instant' });
    });
    await expect(active).toHaveAttribute('href', '#cashflow');
    await expect(select).toHaveValue('#cashflow');
    await page.evaluate(() => window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' }));
    await expect(active).toHaveAttribute('href', '#faq');
    if (width === 1440) {
      const search = page.locator('#docsSearch');
      await search.fill('нет_такого_раздела_123');
      await expect(active).toHaveCount(0);
      await search.fill('номера кузова');
      await expect(active).toHaveAttribute('href', '#vehicles');
      await search.press('Escape');
    }
  });
}
