const { test, expect } = require('@playwright/test');

// Startup failure contracts use isolated shells with the real served app module.
// All interaction checks use the actual generated homepage and its components.
async function fixturePage(page, content, { rendered = false } = {}) {
  await page.route('**/__interaction-fixture.html', route => route.fulfill({
    contentType: 'text/html',
    body: `<!doctype html><html lang="en"><head><meta charset="utf-8">
      <script type="module" src="/assets/js/app.js"></script></head>
      <body class="app-loading" ${rendered ? 'data-rendered="true"' : ''}>${content}</body></html>`,
  }));
  await openHome(page, '/__interaction-fixture.html');
}

async function openHome(page, path = '/') {
  await page.goto(path);
  await expect(page.locator('body')).toHaveClass('app-ready');
}

test('prerendered startup enhances existing content without fetching components', async ({ page }) => {
  const requests = [];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.route('**/components/**', route => {
    requests.push(route.request().url());
    return route.abort('failed');
  });
  await fixturePage(page, `<div data-component="components/unavailable.html">
    <h1>Static content stays visible</h1><button id="theme-toggle">Auto</button>
    <button id="language-toggle">한국어</button></div>`, { rendered: true });
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.locator('#language-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
});

test('one failed development component leaves controls usable with a working retry', async ({ page }) => {
  let fail = true;
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/components/cards/visitor-ip.html', async route => {
    if (fail) return route.fulfill({ status: 503, body: 'Unavailable' });
    return route.continue();
  });
  await fixturePage(page, `<div data-component="components/sections/header.html"></div>
    <div data-component="components/cards/visitor-ip.html"></div>`);
  await page.locator('#language-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
  const retry = page.locator('[data-component-retry]');
  await expect(retry).toBeVisible();
  await expect(page.locator('[data-component-error]')).toContainText('불러');
  fail = false;
  await retry.click();
  await expect(page.locator('#visitor-ip-check')).toBeVisible();
  await expect(retry).toHaveCount(0);
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  expect(errors).toEqual([]);
});

test('a stalled development component aborts and exposes recovery instead of blocking startup', async ({ page }) => {
  await page.clock.install();
  let requested = false;
  await page.route('**/components/cards/visitor-ip.html', () => { requested = true; });
  const startup = fixturePage(page, `<div data-component="components/sections/header.html"></div>
    <div data-component="components/cards/visitor-ip.html"></div>`);
  await expect.poll(() => requested).toBe(true);
  await page.clock.fastForward(8000);
  await startup;
  await expect(page.locator('[data-component-retry]')).toBeVisible();
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('directory combines case-insensitive search terms and category with accurate counts', async ({ page }) => {
  await openHome(page);
  const entries = page.locator('[data-project]:visible h3');
  const count = page.locator('[data-project-count]');
  await expect(count).toHaveText('10 of 10 projects');
  await page.locator('[data-project-filter="official"]').click();
  await expect(entries).toHaveCount(6);
  await expect(count).toHaveText('6 of 10 projects');
  await page.locator('[data-project-filter="community"]').click();
  await expect(entries).toHaveCount(4);
  await expect(count).toHaveText('4 of 10 projects');
  await page.locator('#project-search').fill('  LiNuX  ');
  await page.locator('[data-project-filter="official"]').click();
  await expect(entries).toHaveText(['Arch Linux', 'Kali Linux']);
  await expect(count).toHaveText('2 of 10 projects');
  await page.locator('#project-search').fill(' LiNuX ARCH ');
  await expect(entries).toHaveText(['Arch Linux']);
  await expect(count).toHaveText('1 of 10 projects');
  await page.locator('#project-search').fill('칼리');
  await expect(entries).toHaveText(['Kali Linux']);
  await expect(page.locator('[data-project-filter="official"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-project-filter="all"]')).toHaveAttribute('aria-pressed', 'false');
});

test('empty directory offers a reset that clears query and category', async ({ page }) => {
  await openHome(page);
  await page.locator('[data-project-filter="community"]').click();
  await page.locator('#project-search').fill('arch');
  await expect(page.locator('[data-project]:visible')).toHaveCount(0);
  await expect(page.locator('[data-project-count]')).toHaveText('0 of 10 projects');
  await expect(page.locator('[data-project-empty]')).toBeVisible();
  await page.locator('[data-project-reset]').click();
  await expect(page.locator('#project-search')).toHaveValue('');
  await expect(page.locator('#project-search')).toBeFocused();
  await expect(page.locator('[data-project-filter="all"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-project]:visible')).toHaveCount(10);
  await expect(page.locator('[data-project-empty]')).toBeHidden();
  await page.locator('#project-search').fill('unknown');
  await page.locator('#project-search').fill('');
  await expect(page.locator('[data-project-empty]')).toBeHidden();
  await expect(page.locator('[data-project-count]')).toHaveText('10 of 10 projects');
});

test('language changes translate directory labels without clearing filter state', async ({ page }) => {
  await openHome(page);
  await page.locator('[data-project-filter="official"]').click();
  await page.locator('#project-search').fill('arch');
  await page.locator('#language-toggle').click();
  await expect(page.locator('label[for="project-search"]')).toHaveText('프로젝트 검색');
  await expect(page.locator('#project-search')).toHaveAttribute('placeholder', '프로젝트 이름으로 검색');
  await expect(page.locator('#project-search')).toHaveValue('arch');
  await expect(page.locator('[data-project-filter="official"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-project]:visible h3')).toHaveText(['Arch Linux']);
  await expect(page.locator('[data-project-count]')).toHaveText('전체 10개 중 1개 프로젝트');
  await page.locator('#language-toggle').click();
  await expect(page.locator('#project-search')).toHaveAttribute('placeholder', 'Search by project name');
  await expect(page.locator('[data-project-count]')).toHaveText('1 of 10 projects');
});

for (const failure of ['getter', 'getItem', 'setItem']) {
  test(`blocked preference ${failure} leaves directory and dialog interactions usable`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(failure => {
      if (failure === 'getter') Object.defineProperty(window, 'localStorage', { get() { throw new Error('Blocked'); } });
      else Storage.prototype[failure] = () => { throw new DOMException('Blocked', 'SecurityError'); };
    }, failure);
    await openHome(page, '/?lang=ko');
    await page.locator('#project-search').fill('arch');
    await page.locator('#language-toggle').click();
    await expect(page.locator('[data-project]:visible h3')).toHaveText(['Arch Linux']);
    await expect(page.locator('[data-project-count]')).toHaveText('1 of 10 projects');
    await page.locator('[data-sponsor-toggle]').first().click();
    await expect(page.locator('[data-sponsor-modal]')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('[data-sponsor-modal]')).toBeHidden();
    await page.locator('#theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    expect(errors).toEqual([]);
  });
}

test('copy buttons write the exact endpoint before announcing success', async ({ page }) => {
  await page.addInitScript(() => {
    window.copiedAddresses = [];
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: {
      writeText: async value => { window.copiedAddresses.push(value); },
    } });
  });
  await openHome(page);
  const buttons = page.locator('[data-copy]');
  await expect(buttons).toHaveCount(6);
  const expected = await buttons.evaluateAll(nodes => nodes.map(node => node.dataset.copy));
  for (const button of await buttons.all()) {
    await button.click();
    await expect(page.locator('[data-copy-status]')).toContainText('Copied address');
  }
  expect(await page.evaluate(() => window.copiedAddresses)).toEqual(expected);
  await page.locator('#language-toggle').click();
  await expect(page.locator('[data-copy-status]')).toContainText('주소를 복사했습니다');
});

for (const failure of ['unavailable', 'rejected']) {
  test(`clipboard ${failure} selects the address for manual copying without false success`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(failure => {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: failure === 'unavailable' ? undefined : {
        writeText: async () => { throw new DOMException('Denied', 'NotAllowedError'); },
      } });
      document.execCommand = () => { throw new Error('Do not use legacy copying'); };
    }, failure);
    await openHome(page);
    const button = page.locator('[data-copy]').first();
    const address = await button.getAttribute('data-copy');
    await button.click();
    await expect(page.locator('[data-copy-status]')).toContainText('copy it manually');
    await expect(page.locator('[data-copy-status]')).not.toContainText('Copied address');
    expect(await page.evaluate(() => window.getSelection().toString())).toBe(address);
    await expect(button).toBeEnabled();
    expect(errors).toEqual([]);
  });
}

test('Space opens mirror preview and Close restores focus to its single original trigger', async ({ page }) => {
  await page.route('https://mirror.keiminem.com/**', route => route.fulfill({ contentType: 'text/html', body: '<p>Mirror directory fixture</p>' }));
  await openHome(page);
  const opener = page.locator('[data-mirror-trigger="mirror1"]');
  await expect(opener).toHaveCount(1);
  await expect(page.locator('[data-mirror-trigger="mirror2"]')).toHaveCount(1);
  await expect(opener).toHaveAttribute('href', 'https://mirror.keiminem.com/');
  await expect(opener).toHaveAttribute('role', 'button');
  await opener.focus();
  await page.keyboard.press('Space');
  await expect(page.locator('[data-mirror-list]')).toBeVisible();
  await expect(opener).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-mirror-frame]')).toHaveAttribute('title', 'Mirror 1 directory preview');
  await expect(page.locator('[data-mirror-open]')).toHaveAttribute('target', '_blank');
  await page.locator('[data-mirror-close]').click();
  await expect(page.locator('[data-mirror-list]')).toBeHidden();
  await expect(page.locator('[data-mirror-frame]')).not.toHaveAttribute('src');
  await expect(opener).toHaveAttribute('aria-pressed', 'false');
  await expect(opener).toBeFocused();
});

test('language switching preserves an open mirror directory navigation without reloading it', async ({ page }) => {
  const requests = [];
  await page.route('https://mirror.keiminem.com/**', route => {
    requests.push(route.request().url());
    return route.fulfill({ contentType: 'text/html', body: route.request().url().endsWith('/archlinux/')
      ? '<p>Arch directory stays open</p>' : '<a href="/archlinux/">Arch directory</a>' });
  });
  await openHome(page);
  await page.locator('[data-mirror-trigger="mirror1"]').click();
  const frame = page.frameLocator('[data-mirror-frame]');
  await frame.getByRole('link', { name: 'Arch directory' }).click();
  await expect(frame.getByText('Arch directory stays open')).toBeVisible();
  await page.locator('#language-toggle').click();
  await expect(page.locator('[data-mirror-title]')).toHaveText('미러 1');
  await expect(page.locator('[data-mirror-frame]')).toHaveAttribute('title', '미러 1 디렉터리 미리보기');
  await expect(frame.getByText('Arch directory stays open')).toBeVisible();
  await page.locator('#language-toggle').click();
  await expect(page.locator('[data-mirror-title]')).toHaveText('Mirror 1');
  await expect(frame.getByText('Arch directory stays open')).toBeVisible();
  expect(requests).toEqual(['https://mirror.keiminem.com/', 'https://mirror.keiminem.com/archlinux/']);
});

test('preview gives bounded loading help without treating iframe load as a health check', async ({ page }) => {
  await page.clock.install();
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('https://mirror.keiminem.com/**', async route => {
    await pending;
    await route.fulfill({ status: 503, contentType: 'text/html', body: '<p>Unavailable fixture</p>' });
  });
  await openHome(page);
  await page.locator('[data-mirror-trigger="mirror1"]').click();
  const status = page.locator('[data-mirror-status]');
  await expect(status).toHaveAttribute('role', 'status');
  await expect(status).toContainText('Opening');
  await page.clock.fastForward(10000);
  await expect(status).not.toContainText('Opening');
  await expect(status).toContainText('open it in a new tab');
  release();
  await expect(page.frameLocator('[data-mirror-frame]').getByText('Unavailable fixture')).toBeVisible();
  await expect(status).not.toContainText(/success|online|reachable/i);
  await page.locator('#language-toggle').click();
  await expect(status).toContainText('새 탭');
});

test('IP lookup runs only on request and preserves pending and success through language changes', async ({ page }) => {
  let requests = 0;
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  await page.route('https://api.ipify.org/**', async route => {
    requests += 1;
    await pending;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ip: '203.0.113.27' }) });
  });
  await openHome(page);
  expect(requests).toBe(0);
  const button = page.locator('#visitor-ip-check');
  const status = page.locator('#visitor-ip');
  await expect(status).toHaveText('Not checked');
  await button.click();
  await expect(button).toBeDisabled();
  await expect(status).toHaveText('Checking...');
  await page.locator('#language-toggle').click();
  await expect(status).toHaveText('확인 중...');
  await expect(button).toBeDisabled();
  release();
  await expect(status).toHaveText('203.0.113.27');
  await expect(button).toBeEnabled();
  await page.locator('#language-toggle').click();
  await expect(status).toHaveText('203.0.113.27');
  expect(requests).toBe(1);
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }))).not.toContain('203.0.113.27');
});

test('IP lookup rejects hostile or invalid values as text-safe failures and can recover', async ({ page }) => {
  const bodies = [
    JSON.stringify({ ip: '<img src=x onerror="window.ipInjected=true">' }),
    JSON.stringify({ ip: { address: '203.0.113.27' } }),
    JSON.stringify({ ip: '999.0.0.1' }),
    JSON.stringify({ ip: 'not-an-ip' }),
    JSON.stringify({ ip: null }),
    'not JSON',
    JSON.stringify({ ip: '2001:db8::27' }),
  ];
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('https://api.ipify.org/**', route => route.fulfill({ contentType: 'application/json', body: bodies.shift() }));
  await openHome(page);
  const button = page.locator('#visitor-ip-check');
  const status = page.locator('#visitor-ip');
  for (let attempt = 0; attempt < 6; attempt += 1) {
    await button.click();
    await expect(status).toHaveText('Unavailable');
    await expect(button).toBeEnabled();
    await page.locator('#language-toggle').click();
    await expect(status).toHaveText('확인할 수 없음');
    await page.locator('#language-toggle').click();
    await expect(status).toHaveText('Unavailable');
    await expect(status.locator('*')).toHaveCount(0);
  }
  await button.click();
  await expect(status).toHaveText('2001:db8::27');
  expect(await page.evaluate(() => window.ipInjected)).toBeUndefined();
  expect(errors).toEqual([]);
});

test('IP lookup aborts after a bounded wait and enables an explicit retry', async ({ page }) => {
  await page.clock.install();
  let requests = 0;
  const aborted = [];
  page.on('requestfailed', request => { if (request.url().includes('api.ipify.org')) aborted.push(request.url()); });
  await page.route('https://api.ipify.org/**', route => {
    requests += 1;
    if (requests === 1) return; // Deliberately never answer the first request.
    return route.fulfill({ contentType: 'application/json', body: '{"ip":"203.0.113.28"}' });
  });
  await openHome(page);
  await page.locator('#visitor-ip-check').click();
  await expect.poll(() => requests).toBe(1);
  await expect(page.locator('#visitor-ip-check')).toBeDisabled();
  await page.clock.fastForward(8000);
  await expect(page.locator('#visitor-ip')).toHaveText('Unavailable');
  await expect(page.locator('#visitor-ip-check')).toBeEnabled();
  await expect.poll(() => aborted.length).toBe(1);
  await page.locator('#visitor-ip-check').click();
  await expect(page.locator('#visitor-ip')).toHaveText('203.0.113.28');
  expect(requests).toBe(2);
});

test('every marked homepage label has and applies an English and Korean translation', async ({ page }) => {
  await openHome(page);
  for (const language of ['en', 'ko']) {
    if (language === 'ko') await page.locator('#language-toggle').click();
    const problems = await page.evaluate(async language => {
      const { translations } = await import('/assets/js/i18n.js');
      const dictionary = translations[language];
      const problems = [];
      for (const [marker, attribute] of [['data-i18n', null], ['data-i18n-placeholder', 'placeholder'], ['data-i18n-aria-label', 'aria-label']]) {
        for (const node of document.querySelectorAll(`[${marker}]`)) {
          const key = node.getAttribute(marker);
          if (!dictionary[key]) problems.push(`${language}: missing ${key}`);
          else if ((attribute ? node.getAttribute(attribute) : node.textContent) !== dictionary[key]) problems.push(`${language}: untranslated ${key}`);
        }
      }
      return problems;
    }, language);
    expect(problems).toEqual([]);
  }
});

test('preference controls expose localized current theme and next language labels', async ({ page }) => {
  await openHome(page, '/?lang=ko');
  const theme = page.locator('#theme-toggle');
  const language = page.locator('#language-toggle');
  await expect(theme).toHaveAccessibleName('테마 변경, 현재 테마: 자동');
  await expect(language).toHaveAccessibleName('영어로 전환 (English)');
  await theme.press('Enter');
  await expect(theme).toHaveAccessibleName('테마 변경, 현재 테마: 밝게');
  await expect(theme).toContainText('밝게');
  await language.click();
  await expect(theme).toHaveAccessibleName('Change theme, current theme: Light');
  await expect(language).toHaveAccessibleName('Switch to Korean (한국어)');
  await expect(page.locator('[data-copy]').first()).toHaveAccessibleName('Copy address');
});

test('sponsor closes by its close button or backdrop but not by a content click', async ({ page }) => {
  await openHome(page);
  const opener = page.locator('[data-sponsor-toggle]').first();
  const modal = page.locator('[data-sponsor-modal]');
  await opener.click();
  await modal.locator('h2').click();
  await expect(modal).toBeVisible();
  await modal.locator('[data-sponsor-close]').click();
  await expect(modal).toBeHidden();
  await expect(opener).toBeFocused();
  await opener.click();
  await page.mouse.click(2, 2);
  await expect(modal).toBeHidden();
  await expect(opener).toBeFocused();
  await expect(opener).toHaveAttribute('aria-expanded', 'false');
});

test('sponsor uses a native modal with keyboard confinement and restored focus', async ({ page }) => {
  await openHome(page);
  const opener = page.locator('[data-sponsor-toggle]').first();
  const modal = page.locator('[data-sponsor-modal]');
  await opener.click();
  expect(await modal.evaluate(node => node instanceof HTMLDialogElement && node.matches(':modal'))).toBe(true);
  await expect(opener).toHaveAttribute('aria-expanded', 'true');
  for (let i = 0; i < 5; i += 1) {
    await page.keyboard.press('Tab');
    // Chromium can cycle through browser chrome (document.activeElement becomes body).
    expect(await modal.evaluate(node => document.activeElement === document.body || node.contains(document.activeElement))).toBe(true);
    await opener.evaluate(node => node.focus());
    await expect(opener).not.toBeFocused();
  }
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();
  await expect(modal).toHaveAttribute('hidden', '');
  await expect(opener).toHaveAttribute('aria-expanded', 'false');
  await expect(opener).toBeFocused();
});
