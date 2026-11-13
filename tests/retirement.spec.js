const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..');
const RETIRED_DOM = '[data-mirror-cell], [data-sync-state], [data-disk-usage], [data-sync-rx], [data-sync-tx], [data-freshness], [data-telemetry-note], .telemetry-rate';
const RETIRED_REQUEST = /(?:\/status\/|\/telemetry(?:\/|\.js)|mirrors\.json)/;
const PROJECTS = ['archlinux', 'gentoo', 'kali', 'cachyos', 'artix', 'opnsense', 'endeavouros', 'fyralabs', 'manjaro', 'proxmox'];

async function expectBrowseLinks(page) {
  await expect(page.locator('[data-project]')).toHaveCount(10);
  await expect(page.locator('[data-project] a')).toHaveCount(20);
  for (const project of PROJECTS) {
    const links = page.locator(`[data-project="${project}"] a`);
    await expect(links.nth(0)).toHaveAttribute('href', `https://mirror.keiminem.com/${project}/`);
    await expect(links.nth(1)).toHaveAttribute('href', `https://mirror2.keiminem.com/${project}/`);
  }
}

for (const language of ['en', 'ko']) {
  test(`retired telemetry has no DOM, module, requests or periodic timers in ${language}`, async ({ page }, testInfo) => {
    const requests = [];
    page.on('request', request => requests.push(request.url()));
    await page.clock.install();
    await page.addInitScript(() => {
      window.registeredIntervals = [];
      const original = window.setInterval;
      window.setInterval = (...args) => {
        window.registeredIntervals.push(args[1]);
        return original(...args);
      };
    });
    await page.goto(`/?lang=${language}`);
    await expect(page.locator('#language-toggle')).toHaveText(language === 'ko' ? 'English' : '한국어');
    await expectBrowseLinks(page);
    await expect.soft(page.locator(RETIRED_DOM)).toHaveCount(0);
    expect.soft(fs.existsSync(path.join(ROOT, 'assets/js/telemetry.js'))).toBe(false);
    for (const file of ['assets/js/app.js', 'assets/js/site.js', 'assets/js/i18n.js']) {
      expect.soft(fs.readFileSync(path.join(ROOT, file), 'utf8')).not.toMatch(/telemetry|\/status\/mirrors\.json/i);
    }
    await page.clock.fastForward(65000);
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await page.locator('#language-toggle').click();
    await page.locator('#theme-toggle').click();
    await page.clock.fastForward(65000);
    await testInfo.attach('retirement-network', {
      body: JSON.stringify({ requests, advancedMilliseconds: 130000, registeredIntervals: await page.evaluate(() => window.registeredIntervals) }, null, 2),
      contentType: 'application/json',
    });
    expect.soft(requests.filter(url => RETIRED_REQUEST.test(url))).toEqual([]);
    expect.soft(await page.evaluate(() => window.registeredIntervals)).toEqual([]);
    await expect(page.locator(RETIRED_DOM)).toHaveCount(0);
    await expectBrowseLinks(page);
  });
}

test('development component recovery restores browsing without reviving the retired poller', async ({ page }) => {
  const requests = [];
  const errors = [];
  page.on('request', request => requests.push(request.url()));
  page.on('pageerror', error => errors.push(error.message));
  await page.clock.install();
  const template = fs.readFileSync(path.join(ROOT, 'templates/index.html'), 'utf8');
  await page.route('**/__retirement-dev.html', route => route.fulfill({
    contentType: 'text/html',
    body: template.replace('data-rendered="true"', 'data-rendered="false"').replace('class="app-ready"', 'class="app-loading"'),
  }));
  let broken = true;
  await page.route('**/components/cards/project-status.html', route => broken
    ? route.fulfill({ status: 503, body: '' }) : route.continue());
  await page.goto('/__retirement-dev.html');
  const host = page.locator('[data-component="components/cards/project-status.html"]');
  await expect(host.locator('[data-component-retry]')).toBeVisible();
  await page.locator('#language-toggle').click();
  broken = false;
  await host.locator('[data-component-retry]').click();
  await expectBrowseLinks(page);
  await expect(page.locator('#project-status-title')).toHaveText('미러 프로젝트');
  await page.locator('#project-search').fill('아치');
  await expect(page.locator('[data-project]:visible h3')).toHaveText(['Arch Linux']);
  await page.clock.fastForward(130000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await page.locator('#project-search').fill('');
  await expect(page.locator('[data-project]:visible')).toHaveCount(10);
  expect(requests.filter(url => RETIRED_REQUEST.test(url))).toEqual([]);
  await expect(page.locator(RETIRED_DOM)).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('no-JavaScript browsing retains all destinations without status placeholders', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL });
  try {
    const page = await context.newPage();
    await page.goto('/');
    await expectBrowseLinks(page);
    await expect(page.locator(RETIRED_DOM)).toHaveCount(0);
    await expect(page.locator('#projects')).not.toContainText(/\b(?:RX|TX|Unknown|Syncing|Completed|Online)\b/);
    await expect(page.locator('#mirror-access code')).toHaveText([
      'https://mirror.keiminem.com/', 'http://mirror.keiminem.com/', 'rsync://rsync.keiminem.com/',
      'https://mirror2.keiminem.com/', 'http://mirror2.keiminem.com/', 'rsync://rsync2.keiminem.com/',
    ]);
    await expect(page.locator('a[href="https://grafana.keiminem.com/public-dashboards/32d4cf1d87a24a31b5540995402ab6ee"]')).toBeVisible();
    await expect(page.locator('a[href="mailto:me0kmin27@keiminem.com"]')).toBeVisible();
    await expect(page.locator('.sponsor-link')).toBeVisible();
  } finally {
    await context.close();
  }
});
