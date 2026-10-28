const { test, expect } = require('@playwright/test');

const projects = {
  official: ['Arch Linux', 'Gentoo', 'Kali Linux', 'CachyOS', 'Artix', 'OPNsense'],
  community: ['EndeavourOS', 'Fyra Labs', 'Manjaro', 'Proxmox'],
};

async function openHome(page, language = 'en') {
  await page.goto(language === 'ko' ? '/?lang=ko' : '/');
  await expect(page.locator('body')).toHaveClass(/app-ready/);
}

test('project directory exposes all ten projects with labeled search and category controls', async ({ page }) => {
  await openHome(page);
  const directory = page.locator('#projects[data-project-directory]');
  await expect(directory).toBeVisible();
  await expect(directory.locator('label[for="project-search"]')).toBeVisible();
  await expect(directory.locator('#project-search')).toHaveAttribute('type', 'search');
  await expect(directory.locator('#project-search')).toHaveAttribute('data-i18n-placeholder', 'projectSearchPlaceholder');
  for (const category of ['all', 'official', 'community']) {
    const filter = directory.locator(`[data-project-filter="${category}"]`);
    await expect(filter).toBeVisible();
    await expect(filter).toHaveAttribute('aria-pressed', category === 'all' ? 'true' : 'false');
  }
  await expect(directory.locator('[data-project]')).toHaveCount(10);
  for (const [category, names] of Object.entries(projects)) {
    const rows = directory.locator(`[data-project][data-category="${category}"]`);
    await expect(rows).toHaveCount(names.length);
    for (const name of names) {
      const row = rows.filter({ has: page.getByRole('heading', { name, exact: true }) });
      await expect(row).toHaveCount(1);
      expect(await row.getAttribute('data-search')).toMatch(/[가-힣]/);
      await expect(row.locator('a[href^="https://mirror.keiminem.com/"]')).toHaveCount(1);
      await expect(row.locator('a[href^="https://mirror2.keiminem.com/"]')).toHaveCount(1);
    }
  }
  await expect(directory.locator('[data-project-count]')).toHaveAttribute('role', 'status');
  await expect(directory.locator('[data-project-count]')).toHaveAttribute('aria-live', 'polite');
  await expect(directory.locator('[data-project-empty]')).toBeHidden();
  await expect(directory.locator('[data-project-reset]')).toHaveCount(1);
});

test('mirror access presents six readable copyable endpoints with one preview control per mirror', async ({ page }) => {
  await openHome(page);
  const access = page.locator('#mirror-access');
  await expect(access).toBeVisible();
  const endpoints = [
    'https://mirror.keiminem.com/', 'http://mirror.keiminem.com/', 'rsync://rsync.keiminem.com/',
    'https://mirror2.keiminem.com/', 'http://mirror2.keiminem.com/', 'rsync://rsync2.keiminem.com/',
  ];
  await expect(access.locator('[data-copy]')).toHaveCount(6);
  for (const endpoint of endpoints) {
    const copy = access.locator(`[data-copy="${endpoint}"]`);
    await expect(copy).toBeVisible();
    await expect(copy).toHaveAccessibleName(/copy/i);
    await expect(copy.locator('..').locator('code')).toHaveText(endpoint);
  }
  for (const mirror of ['mirror1', 'mirror2']) {
    const preview = page.locator(`[data-mirror-trigger="${mirror}"]`);
    await expect(preview).toHaveCount(1);
    await expect(preview).toHaveAttribute('aria-controls', 'mirror-preview');
    await expect(preview).toHaveAttribute('aria-pressed', 'false');
  }
  await expect(page.locator('[data-mirror-list]')).toHaveAttribute('id', 'mirror-preview');
  await expect(page.locator('[data-mirror-close]')).toHaveCount(1);
  await expect(page.locator('[data-mirror-open]')).toHaveAttribute('rel', /noreferrer/);
  await expect(page.locator('.hero a[href="#projects"]')).toBeVisible();
  await expect(page.locator('.hero a[href="#mirror-access"]')).toBeVisible();
});

for (const width of [320, 375, 768, 1280, 1440]) {
  for (const language of ['en', 'ko']) {
    test(`directory layout remains readable and touchable at ${width}px in ${language}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await openHome(page, language);
      const geometry = await page.evaluate(() => {
        const rect = node => { const { x, y, width, height, right, bottom } = node.getBoundingClientRect(); return { x, y, width, height, right, bottom }; };
        const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
        const controls = [...document.querySelectorAll('button, input, .button, .project-links a, .mirror-preview-trigger, .hero-actions a, .endpoint-address a')].filter(visible);
        return {
          viewport: innerWidth,
          pageWidth: document.documentElement.scrollWidth,
          directory: rect(document.querySelector('#projects')),
          shell: rect(document.querySelector('main')),
          firstProject: rect(document.querySelector('[data-project]')),
          shortControls: controls.filter(node => rect(node).height < 43.9 || rect(node).width < 43.9).map(node => ({ text: node.textContent.trim(), ...rect(node) })),
          overflowingControls: controls.filter(node => rect(node).x < -0.1 || rect(node).right > innerWidth + 0.1).map(node => node.textContent.trim()),
          rows: [...document.querySelectorAll('[data-project]')].map(row => ({ name: rect(row.querySelector('h3')), actions: rect(row.querySelector('.project-links')) })),
        };
      });
      expect(geometry.pageWidth).toBeLessThanOrEqual(width);
      expect(geometry.shortControls).toEqual([]);
      expect(geometry.overflowingControls).toEqual([]);
      expect(Math.abs(geometry.directory.width - geometry.shell.width)).toBeLessThan(2);
      for (const row of geometry.rows) {
        expect(row.name.right <= row.actions.x || row.name.bottom <= row.actions.y).toBe(true);
      }
      const donate = await page.locator('nav [data-sponsor-toggle]').boundingBox();
      const status = await page.locator('nav [data-i18n="navServerStatus"]').boundingBox();
      expect(Math.abs(donate.y - status.y)).toBeLessThan(2);
      expect(status.x).toBeGreaterThanOrEqual(donate.x + donate.width);
      if (width >= 1280) {
        expect(geometry.directory.y).toBeLessThan(630);
        expect(geometry.firstProject.bottom).toBeLessThan(900);
      }
    });
  }
}

for (const viewport of [{ width: 320, height: 568 }, { width: 375, height: 667 }]) {
  for (const language of ['en', 'ko']) {
    test(`opened mirror preview keeps its directory reachable at ${viewport.width}x${viewport.height} in ${language}`, async ({ page }) => {
      await page.setViewportSize(viewport);
      await page.route('https://mirror.keiminem.com/**', route => route.fulfill({
        contentType: 'text/html',
        body: route.request().url().endsWith('/last-directory/')
          ? '<!doctype html><html><body><h1>Directory end reached</h1></body></html>'
          : `<!doctype html><html lang="en"><head><meta charset="utf-8"><style>
              body { margin: 8px; } a { display: block; min-height: 44px; line-height: 44px; }
            </style></head><body><h1>Directory fixture</h1>
            ${Array.from({ length: 12 }, (_, index) => `<a href="/directory-${index}/">Directory ${index}</a>`).join('')}
            <a href="/last-directory/">Last directory</a></body></html>`,
      }));
      await openHome(page, language);
      await page.locator('[data-mirror-trigger="mirror1"]').click();
      const frame = page.frameLocator('[data-mirror-frame]');
      await expect(frame.getByRole('heading', { name: 'Directory fixture' })).toBeVisible();
      const geometry = await page.locator('[data-mirror-list]').evaluate(panel => {
        const rect = node => {
          const { top, right, bottom, left, width, height } = node.getBoundingClientRect();
          return { top, right, bottom, left, width, height };
        };
        return {
          panel: rect(panel), frame: rect(panel.querySelector('iframe')),
          clientHeight: panel.clientHeight, scrollHeight: panel.scrollHeight,
          pageWidth: document.documentElement.scrollWidth,
          nextSection: rect(document.querySelector('#projects')),
          controls: [...panel.querySelectorAll('a, button')].map(rect),
        };
      });
      expect(geometry.scrollHeight).toBeLessThanOrEqual(geometry.clientHeight);
      expect(geometry.frame.top).toBeGreaterThanOrEqual(geometry.panel.top);
      expect(geometry.frame.bottom).toBeLessThanOrEqual(geometry.panel.bottom);
      expect(geometry.frame.left).toBeGreaterThanOrEqual(geometry.panel.left);
      expect(geometry.frame.right).toBeLessThanOrEqual(geometry.panel.right);
      expect(geometry.frame.height).toBeGreaterThanOrEqual(280);
      expect(geometry.nextSection.top).toBeGreaterThanOrEqual(geometry.panel.bottom);
      expect(geometry.pageWidth).toBeLessThanOrEqual(viewport.width);
      for (const control of geometry.controls) {
        expect(control.height).toBeGreaterThanOrEqual(44);
        expect(control.width).toBeGreaterThanOrEqual(44);
        expect(control.bottom).toBeLessThanOrEqual(geometry.frame.top);
      }
      expect(await frame.locator('html').evaluate(node => node.scrollHeight > node.clientHeight)).toBe(true);
      await page.locator('[data-mirror-frame]').scrollIntoViewIfNeeded();
      const last = frame.getByRole('link', { name: 'Last directory', exact: true });
      await last.scrollIntoViewIfNeeded();
      await expect(last).toBeInViewport({ ratio: 1 });
      await last.click();
      await expect(frame.getByRole('heading', { name: 'Directory end reached' })).toBeVisible();
    });
  }
}

for (const theme of ['auto', 'light', 'dark']) {
  test(`320px Korean ${theme} preferences do not overlap the wordmark`, async ({ page }) => {
    await page.setViewportSize({ width: 320, height: 900 });
    await page.addInitScript(theme => localStorage.setItem('keimirror-theme', theme), theme);
    await openHome(page, 'ko');
    const logo = await page.locator('.topbar .logo').boundingBox();
    const preferences = await page.locator('.nav-preferences').boundingBox();
    expect(preferences.x).toBeGreaterThanOrEqual(logo.x + logo.width + 8);
  });
}

test('infrastructure precedes policy while help has its own secondary section', async ({ page }) => {
  await openHome(page);
  const profile = page.locator('.server-profile');
  const policy = page.locator('#disclaimer-title');
  expect(await profile.evaluate((node) => Boolean(node.compareDocumentPosition(document.querySelector('#disclaimer-title')) & Node.DOCUMENT_POSITION_FOLLOWING))).toBe(true);
  await expect(page.locator('aside #contact-title')).toBeVisible();
  await expect(profile.locator('table')).toHaveCount(2);
  await expect(profile).toContainText('Intel Xeon E5-2650 v2 (2 sockets)');
  await expect(profile).toContainText('Intel Xeon E5-2696 v4');
  await expect(profile).toContainText('128 GB');
  await expect(profile).toContainText('32 GB');
  await expect(policy).toBeVisible();
});

for (const width of [320, 768, 1280]) {
  test(`endpoint addresses remain readable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openHome(page);
    const undersized = await page.locator('#mirror-access code').evaluateAll(nodes => nodes.filter(node => parseFloat(getComputedStyle(node).fontSize) < 12).map(node => node.textContent));
    expect(undersized).toEqual([]);
  });
}

test('without JavaScript the directory keeps direct links and hides nonworking controls', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
  const page = await context.newPage();
  try {
    await page.goto('/');
    await expect(page.locator('h1')).toBeVisible();
    await expect(page.locator('[data-project]')).toHaveCount(10);
    await expect(page.locator('[data-project] a')).toHaveCount(20);
    await expect(page.locator('.directory-toolbar')).toBeHidden();
    await expect(page.locator('[data-copy]').first()).toBeHidden();
    await expect(page.locator('nav [data-i18n="navServerStatus"]')).toBeVisible();
    await expect(page.locator('.sponsor-link')).toBeVisible();
    await expect(page.locator('[data-mirror-trigger="mirror1"]')).toHaveAttribute('href', 'https://mirror.keiminem.com/');
  } finally {
    await context.close();
  }
});

test('keyboard focus and hidden content remain accessible without layout leaks', async ({ page }) => {
  await openHome(page);
  const search = page.locator('#project-search');
  await search.focus();
  expect(await search.evaluate(node => {
    const style = getComputedStyle(node);
    return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) >= 2;
  })).toBe(true);
  await expect(page.locator('[data-project-empty]')).toBeHidden();
  await expect(page.locator('[data-mirror-list]')).toBeHidden();
  expect(await page.evaluate(() => {
    document.body.classList.add('app-loading');
    return getComputedStyle(document.body).visibility;
  })).toBe('visible');
});
