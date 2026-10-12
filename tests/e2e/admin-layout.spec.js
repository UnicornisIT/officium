const { test, expect } = require('@playwright/test');

const adminRoutes = [
  '/admin', '/admin/users', '/admin/finance', '/admin/dictionaries',
  '/admin/settings', '/admin/logs', '/admin/system', '/admin/export',
  '/admin/server-update',
];
const viewports = [
  { width: 375, height: 812 }, { width: 390, height: 844 },
  { width: 768, height: 1024 }, { width: 1024, height: 768 },
  { width: 1280, height: 800 }, { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
];

test.beforeEach(async ({ page }) => {
  await page.goto('/dev-login/superadmin');
  await expect(page).toHaveURL(/\/admin/);
});

for (const theme of ['light', 'dark']) {
  test(`all admin screens keep usable geometry in ${theme} theme`, async ({ page }, testInfo) => {
    test.setTimeout(300_000);
    const browserErrors = [];
    page.on('console', message => { if (message.type() === 'error') browserErrors.push(`console: ${message.text()}`); });
    page.on('pageerror', error => browserErrors.push(`page: ${error.message}`));
    page.on('response', response => {
      if (response.status() >= 400 && new URL(response.url()).origin === 'http://127.0.0.1:5010') {
        browserErrors.push(`HTTP ${response.status()}: ${response.url()}`);
      }
    });
    for (const viewport of viewports) {
      await page.setViewportSize(viewport);
      await page.evaluate(selected => {
        localStorage.setItem('officiumTheme', selected);
        document.documentElement.dataset.theme = selected;
        document.documentElement.dataset.bsTheme = selected;
      }, theme);
      for (const route of adminRoutes) {
        const response = await page.goto(route, { waitUntil: 'networkidle' });
        expect(response.status(), route).toBeLessThan(400);
        await expect(page.locator('.admin-shell')).toBeVisible();
        const geometry = await page.evaluate(() => {
          const stage = document.querySelector('.admin-stage').getBoundingClientRect();
          const content = document.querySelector('.admin-content').getBoundingClientRect();
          return {
            horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
            stageLeft: stage.left,
            stageRight: stage.right,
            contentWidth: content.width,
            viewportWidth: window.innerWidth,
          };
        });
        expect(geometry.horizontalOverflow, `${route} ${viewport.width}px overflow`).toBeLessThanOrEqual(1);
        expect(geometry.stageLeft, `${route} stage left`).toBeGreaterThanOrEqual(0);
        expect(geometry.stageRight, `${route} stage right`).toBeLessThanOrEqual(geometry.viewportWidth + 1);
        expect(geometry.contentWidth, `${route} narrow content`).toBeGreaterThan(viewport.width < 768 ? 340 : 600);
        const safeName = route.replaceAll('/', '_') || '_root';
        await page.screenshot({
          path: testInfo.outputPath(`${theme}-${viewport.width}${safeName}.png`),
          fullPage: true,
        });
      }
    }
    expect(browserErrors).toEqual([]);
  });
}

test('contacts modal is keyboard reachable and validates through the real form', async ({ page }) => {
  await page.goto('/admin/settings');
  await page.getByRole('button', { name: 'Добавить' }).focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  await dialog.locator('select[name="type"]').selectOption('website');
  await dialog.locator('input[name="label"]').fill('Документация экземпляра');
  await dialog.locator('input[name="address"]').fill('https://example.org/help');
  await dialog.locator('#contactIconSelect').selectOption('globe2');
  await dialog.getByText('Footer', { exact: true }).click();
  await dialog.locator('.admin-publish-toggle').click();
  await dialog.getByRole('button', { name: 'Сохранить', exact: true }).click();
  await expect(page.getByText('Документация экземпляра')).toBeVisible();
});

test('contacts modal stays aligned and scrollable at responsive widths', async ({ page }) => {
  for (const viewport of [{ width: 375, height: 667 }, { width: 820, height: 720 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(viewport);
    await page.goto('/admin/settings');
    await page.getByRole('button', { name: 'Добавить', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    const geometry = await dialog.evaluate(element => {
      const dialogRect = element.querySelector('.modal-dialog').getBoundingClientRect();
      const contentRect = element.querySelector('.modal-content').getBoundingClientRect();
      const controls = [...element.querySelectorAll('.admin-control')].map(control => control.getBoundingClientRect());
      const hiddenCheckboxes = [...element.querySelectorAll('input[type="checkbox"]')].map(input => input.getBoundingClientRect());
      return {
        dialogLeft: dialogRect.left,
        dialogRight: dialogRect.right,
        contentHeight: contentRect.height,
        viewportWidth: window.innerWidth,
        viewportHeight: window.innerHeight,
        controlsInside: controls.every(rect => rect.left >= contentRect.left && rect.right <= contentRect.right),
        checkboxesCompact: hiddenCheckboxes.every(rect => rect.width <= 2 && rect.height <= 2),
      };
    });
    expect(geometry.dialogLeft).toBeGreaterThanOrEqual(0);
    expect(geometry.dialogRight).toBeLessThanOrEqual(geometry.viewportWidth);
    expect(geometry.contentHeight).toBeLessThanOrEqual(geometry.viewportHeight - 8);
    expect(geometry.controlsInside).toBe(true);
    expect(geometry.checkboxesCompact).toBe(true);
    await dialog.getByRole('button', { name: 'Закрыть', exact: true }).click();
  }
});

test('contact icon picker shows previews and keeps the submitted value in sync', async ({ page }) => {
  await page.goto('/admin/settings');
  await page.getByRole('button', { name: 'Добавить', exact: true }).click();
  await page.locator('.admin-icon-picker__toggle').click();
  const options = page.locator('[data-icon-picker-option]');
  await expect(options).toHaveCount(7);
  for (const option of await options.all()) {
    await expect(option.locator('.bi').first()).toBeVisible();
  }
  await page.locator('[data-icon-value="link-45deg"]').click();
  await expect(page.locator('#contactIconSelect')).toHaveValue('link-45deg');
  await expect(page.locator('.admin-icon-picker__toggle .bi-link-45deg')).toBeVisible();
  await expect(page.locator('[data-icon-picker-label]')).toHaveText('Ссылка');
});

test('users table becomes aligned cards before it can overflow a laptop viewport', async ({ page }) => {
  for (const viewport of [{ width: 1024, height: 768 }, { width: 1440, height: 900 }]) {
    await page.setViewportSize(viewport);
    await page.goto('/admin/users');
    const geometry = await page.evaluate(() => {
      const actions = document.querySelector('.admin-table--responsive td.admin-table__actions');
      const actionControls = [...actions.querySelectorAll('a, button')].map(control => control.getBoundingClientRect());
      const table = document.querySelector('.admin-table--responsive');
      return {
        overflow: document.documentElement.scrollWidth - window.innerWidth,
        tableDisplay: getComputedStyle(table).display,
        actionTopSpread: actionControls.length ? Math.max(...actionControls.map(rect => rect.top)) - Math.min(...actionControls.map(rect => rect.top)) : 0,
      };
    });
    expect(geometry.overflow).toBeLessThanOrEqual(1);
    expect(geometry.tableDisplay).toBe('block');
    expect(geometry.actionTopSpread).toBeLessThanOrEqual(1);
  }
});

test('inline page alerts never collapse the administrative application shell', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/server-update');
  const geometry = await page.evaluate(() => {
    const main = document.querySelector('.app-main');
    const stage = document.querySelector('.admin-stage').getBoundingClientRect();
    const style = getComputedStyle(main);
    return {
      mainPosition: style.position,
      stageWidth: stage.width,
      overflow: document.documentElement.scrollWidth - window.innerWidth,
    };
  });
  expect(geometry.mainPosition).not.toBe('fixed');
  expect(geometry.stageWidth).toBeGreaterThan(900);
  expect(geometry.overflow).toBeLessThanOrEqual(1);
});

test('server update readiness checks render as separate non-overlapping cards', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/admin/server-update');
  const cards = page.locator('.admin-readiness-card');
  await expect(cards).toHaveCount(2);
  for (const card of await cards.all()) {
    const geometry = await card.evaluate(element => {
      const title = element.querySelector('div > span').getBoundingClientRect();
      const description = element.querySelector('small').getBoundingClientRect();
      return { height: element.getBoundingClientRect().height, descriptionBelowTitle: description.top >= title.bottom };
    });
    expect(geometry.height).toBeGreaterThanOrEqual(80);
    expect(geometry.descriptionBelowTitle).toBe(true);
  }
});

test('paired release cards and finance filters keep equal geometry', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/server-update');
  const releaseCards = await page.locator('.admin-release-card').all();
  expect(releaseCards).toHaveLength(2);
  const releaseRects = await Promise.all(releaseCards.map(card => card.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return { top: rect.top, bottom: rect.bottom, width: rect.width };
  })));
  expect(Math.abs(releaseRects[0].top - releaseRects[1].top)).toBeLessThanOrEqual(1);
  expect(Math.abs(releaseRects[0].bottom - releaseRects[1].bottom)).toBeLessThanOrEqual(1);
  expect(Math.abs(releaseRects[0].width - releaseRects[1].width)).toBeLessThanOrEqual(1);

  await page.goto('/admin/dictionaries');
  const dictionaryPanels = await page.locator('.admin-detail-grid--dictionary > .admin-panel').all();
  expect(dictionaryPanels).toHaveLength(2);
  const dictionaryRects = await Promise.all(dictionaryPanels.map(panel => panel.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return { top: rect.top, bottom: rect.bottom };
  })));
  expect(Math.abs(dictionaryRects[0].top - dictionaryRects[1].top)).toBeLessThanOrEqual(1);
  expect(Math.abs(dictionaryRects[0].bottom - dictionaryRects[1].bottom)).toBeLessThanOrEqual(1);

  await page.goto('/admin/finance');
  const filterGeometry = await page.evaluate(() => {
    const controls = [...document.querySelectorAll('.admin-filter-grid--finance > .admin-control')];
    const searchInput = document.querySelector('.admin-input-icon input');
    const searchIcon = document.querySelector('.admin-input-icon .admin-icon');
    const inputRect = searchInput.getBoundingClientRect();
    const iconRect = searchIcon.getBoundingClientRect();
    return {
      widths: controls.map(control => control.getBoundingClientRect().width),
      textStart: inputRect.left + Number(getComputedStyle(searchInput).paddingLeft.replace('px', '')),
      iconRight: iconRect.right,
    };
  });
  expect(Math.abs(filterGeometry.widths[0] - filterGeometry.widths[1])).toBeLessThanOrEqual(1);
  expect(filterGeometry.textStart).toBeGreaterThanOrEqual(filterGeometry.iconRight + 8);
});

test('browser zoom equivalents keep navigation single-line and login content reachable', async ({ page }) => {
  const zoomViewports = [
    { zoom: 100, width: 1920, height: 900 },
    { zoom: 110, width: 1745, height: 818 },
    { zoom: 125, width: 1536, height: 720 },
    { zoom: 150, width: 1280, height: 600 },
    { zoom: 200, width: 960, height: 450 },
  ];

  for (const viewport of zoomViewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/', { waitUntil: 'networkidle' });
    const navGeometry = await page.evaluate(() => {
      const navbar = document.querySelector('.navbar');
      const labels = [...document.querySelectorAll('.nav-expand__compact')];
      return {
        horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
        compact: navbar.classList.contains('navbar--compact'),
        wrappedLabels: navbar.classList.contains('navbar--compact') ? false : labels.some(label => {
          const style = getComputedStyle(label);
          const lineHeight = Number.parseFloat(style.lineHeight)
            || Number.parseFloat(style.fontSize) * 1.3;
          return label.getBoundingClientRect().height > lineHeight * 1.45;
        }),
      };
    });
    expect(navGeometry.horizontalOverflow, `${viewport.zoom}% horizontal overflow`).toBeLessThanOrEqual(1);
    expect(navGeometry.wrappedLabels, `${viewport.zoom}% wrapped navigation`).toBe(false);
    if (viewport.zoom === 110) expect(navGeometry.compact).toBe(true);
  }

  await page.goto('/logout', { waitUntil: 'networkidle' });
  await expect(page).toHaveURL(/\/login/);
  for (const viewport of zoomViewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.goto('/login', { waitUntil: 'networkidle' });
    const loginGeometry = await page.evaluate(() => {
      const card = document.querySelector('.login-auth-card');
      const footer = document.querySelector('.site-footer');
      const lastAction = card.querySelector('.dev-login-actions .btn:last-child');
      const cardRect = card.getBoundingClientRect();
      const footerRect = footer.getBoundingClientRect();
      const actionRect = lastAction.getBoundingClientRect();
      return {
        horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
        cardClipped: card.scrollHeight > card.clientHeight + 1,
        actionInsideCard: actionRect.top >= cardRect.top && actionRect.bottom <= cardRect.bottom + 1,
        footerAfterCard: footerRect.top >= cardRect.bottom - 1,
      };
    });
    expect(loginGeometry.horizontalOverflow, `${viewport.zoom}% login overflow`).toBeLessThanOrEqual(1);
    expect(loginGeometry.cardClipped, `${viewport.zoom}% clipped login card`).toBe(false);
    expect(loginGeometry.actionInsideCard, `${viewport.zoom}% missing final login action`).toBe(true);
    expect(loginGeometry.footerAfterCard, `${viewport.zoom}% footer overlaps login card`).toBe(true);
  }
});

test('responsive empty tables render one symmetric empty surface', async ({ page }) => {
  for (const viewport of [{ width: 1280, height: 800 }, { width: 1024, height: 768 }, { width: 960, height: 720 }]) {
    await page.setViewportSize(viewport);
    await page.goto('/admin/finance?q=__definitely_missing__');
    const geometry = await page.evaluate(() => {
      const wrap = document.querySelector('.admin-table-wrap').getBoundingClientRect();
      const row = document.querySelector('.admin-table__empty-row');
      const cell = row.querySelector('td');
      const empty = row.querySelector('.admin-empty-state').getBoundingClientRect();
      const rowStyle = getComputedStyle(row);
      const beforeStyle = getComputedStyle(cell, '::before');
      return {
        leftInset: empty.left - wrap.left,
        rightInset: wrap.right - empty.right,
        rowBackground: rowStyle.backgroundColor,
        rowBorder: Number.parseFloat(rowStyle.borderLeftWidth),
        beforeDisplay: beforeStyle.display,
        horizontalOverflow: document.documentElement.scrollWidth - window.innerWidth,
      };
    });
    expect(Math.abs(geometry.leftInset - geometry.rightInset)).toBeLessThanOrEqual(1);
    expect(geometry.rowBackground).toBe('rgba(0, 0, 0, 0)');
    expect(geometry.rowBorder).toBe(0);
    expect(geometry.beforeDisplay).toBe('none');
    expect(geometry.horizontalOverflow).toBeLessThanOrEqual(1);
  }
});
