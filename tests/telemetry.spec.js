const { test, expect } = require('@playwright/test');

// SIMULATED, isolated browser data. Never published or used as production fallback.
const GROUPS = {
  archlinux: ['archlinux'], gentoo: ['gentoo', 'gentoo-portage'],
  kali: ['kali', 'kali-images'], cachyos: ['cachyos'],
  artix: ['artix', 'artix-iso', 'artix-weekly-iso'], opnsense: ['opnsense'],
  endeavouros: ['endeavouros'], fyralabs: ['fyralabs'], manjaro: ['manjaro'], proxmox: ['proxmox'],
};
const NOW = 1793170800;
const ENDPOINT = '**/status/mirrors.json';
function metric(overrides = {}) {
  return {
    state: 'completed', last_attempt_at: NOW - 180, last_finished_at: NOW - 90,
    last_success_at: NOW - 90, exit_code: 0, warning: false,
    size_bytes: 1073741824, size_measured_at: NOW - 600,
    sync_rx_bps: null, sync_tx_bps: null, rates_observed_at: null,
    http_bytes_60s: 0, rsync_bytes_60s: null, traffic_observed_since: NOW - 120,
    ...overrides,
  };
}
function payload() {
  const mirrors = {};
  for (const mirror of ['mirror1', 'mirror2']) {
    const projects = Object.fromEntries(Object.entries(GROUPS).map(([id, repositories]) => [id, metric({
      size_bytes: repositories.length * 1073741824,
      repositories: repositories.map(id => ({ id, ...metric() })),
    })]));
    mirrors[mirror] = { available: true, snapshot: {
      schema_version: 1, mirror_id: mirror, collected_at: NOW, interval_seconds: 15, projects,
    } };
  }
  return { schema_version: 1, generated_at: NOW, refresh_seconds: 15, mirrors };
}
function cell(page, project = 'archlinux', mirror = 'mirror1') {
  return page.locator(`[data-project="${project}"] [data-mirror-cell="${mirror}"]`);
}
async function openFixture(page, data = payload()) {
  await page.clock.install({ time: new Date(NOW * 1000) });
  await page.route(ENDPOINT, route => route.fulfill({ json: data }));
  await page.goto('/?lang=en');
  await expect(page.locator('body')).toHaveClass('app-ready');
}

test('a recovered development directory starts telemetry once its real project component becomes available', async ({ page }) => {
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  await page.clock.install({ time: new Date(NOW * 1000) });
  await page.route('**/', route => route.fulfill({ contentType: 'text/html', body: html.replace('data-rendered="true"', 'data-rendered="false"') }));
  let broken = true;
  await page.route('**/components/cards/project-status.html', route => broken
    ? route.fulfill({ status: 503, body: '' }) : route.continue());
  let requests = 0;
  await page.route(ENDPOINT, route => { requests += 1; return route.fulfill({ json: payload() }); });
  await page.goto('/');
  await expect(page.locator('body')).toHaveClass('app-ready');
  const host = page.locator('[data-component="components/cards/project-status.html"]');
  await expect(host.locator('[data-component-retry]')).toBeVisible();
  expect(requests).toBe(0);
  broken = false;
  await host.locator('[data-component-retry]').click();
  await expect(cell(page).locator('[data-sync-state]')).toHaveText('Completed');
  expect(requests).toBe(1);
});

test('accepted transfer warnings remain visible in the compact state and localized details', async ({ page }) => {
  const data = payload();
  const group = data.mirrors.mirror1.snapshot.projects.archlinux;
  group.warning = true; group.exit_code = 24;
  group.repositories[0].warning = true; group.repositories[0].exit_code = 24;
  await openFixture(page, data);
  await expect(cell(page).locator('[data-sync-state]')).toHaveText('Completed · warning');
  await cell(page).locator('summary').click();
  await expect(cell(page).locator('[data-group-metrics] [data-metric="state"]')).toHaveText('Completed · warning');
  await page.locator('#language-toggle').click();
  await expect(cell(page).locator('[data-sync-state]')).toHaveText('완료 · 경고');
  await expect(cell(page).locator('summary')).toHaveAccessibleName('Arch Linux 미러 1 상세 정보');
});

test('locale switching preserves search, filter, disclosures, preview navigation and measured values', async ({ page }) => {
  let previewLoads = 0;
  await page.route('https://mirror.keiminem.com/**', route => {
    previewLoads += 1;
    return route.fulfill({ contentType: 'text/html', body: '<!doctype html><html><body>Preview fixture</body></html>' });
  });
  await openFixture(page);
  await page.locator('[data-mirror-trigger="mirror1"]').click();
  await expect(page.frameLocator('[data-mirror-frame]').locator('body')).toHaveText('Preview fixture');
  await page.locator('[data-project-filter="official"]').click();
  await page.locator('#project-search').fill('artix');
  const reading = cell(page, 'artix');
  await reading.locator('summary').click();
  const measured = reading.locator('[data-group-metrics] [data-metric="size_measured_at"] time');
  const originalTime = await measured.getAttribute('datetime');
  const enText = await measured.textContent();
  for (const lang of ['ko', 'en']) {
    await page.locator('#language-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('lang', lang);
    await expect(page.locator('#project-search')).toHaveValue('artix');
    await expect(page.locator('[data-project-filter="official"]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('[data-project]:visible')).toHaveCount(1);
    await expect(reading.locator('details')).toHaveAttribute('open', '');
    await expect(reading.locator('[data-disk-usage]')).toHaveText('3 GiB');
    await expect(reading).toHaveAttribute('data-freshness', 'live');
    await expect(measured).toHaveAttribute('datetime', originalTime);
    if (lang === 'ko') expect(await measured.textContent()).not.toBe(enText);
    else await expect(measured).toHaveText(enText);
    await expect(reading.locator('[data-sync-state]')).toHaveText(lang === 'ko' ? '완료' : 'Completed');
    await expect(reading.locator('summary')).toHaveAccessibleName(lang === 'ko' ? 'Artix 미러 1 상세 정보' : 'Artix Mirror 1 Details');
    await expect(reading).toContainText(lang === 'ko' ? '최근 60초' : 'Last 60 seconds');
  }
  expect(previewLoads).toBe(1);
});

for (const language of ['en', 'ko']) {
  for (const theme of ['light', 'dark']) {
    for (const viewport of [{ width: 320, height: 480 }, { width: 768, height: 600 }, { width: 1280, height: 800 }]) {
      test(`inline cells and open details fit ${viewport.width}px ${language} ${theme} with usable targets`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await page.addInitScript(theme => localStorage.setItem('keimirror-theme', theme), theme);
        await openFixture(page);
        if (language === 'ko') await page.locator('#language-toggle').click();
        const reading = cell(page, 'artix');
        await reading.locator('summary').click();
        const geometry = await page.locator('#projects').evaluate(directory => {
          const rect = node => {
            const { left, right, top, bottom, width, height } = node.getBoundingClientRect();
            return { left, right, top, bottom, width, height };
          };
          const visible = node => node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
          const controls = [...directory.querySelectorAll('a, button, input, summary')].filter(visible);
          const children = [...directory.querySelectorAll('.mirror-cell, .telemetry-detail-body, .telemetry-metrics, .telemetry-repositories')].filter(visible);
          return {
            width: document.documentElement.scrollWidth,
            directoryWidth: rect(directory).width,
            detailWidth: rect(directory.querySelector('details[open] .telemetry-detail-body')).width,
            undersized: controls.filter(node => rect(node).width < 44 || rect(node).height < 44).map(node => node.outerHTML.slice(0, 180)),
            escapes: children.filter(node => rect(node).right > rect(directory).right + 1 || rect(node).left < rect(directory).left - 1).map(node => node.className),
            rows: [...directory.querySelectorAll('[data-project]')].map(row => ({ cells: [...row.querySelectorAll('[data-mirror-cell]')].map(rect) })),
          };
        });
        expect(geometry.width).toBeLessThanOrEqual(viewport.width);
        if (viewport.width === 320) expect(geometry.detailWidth).toBeGreaterThanOrEqual(geometry.directoryWidth - 1);
        expect(geometry.undersized).toEqual([]);
        expect(geometry.escapes).toEqual([]);
        for (const { cells: [one, two] } of geometry.rows) expect(one.right <= two.left + 1 || one.bottom <= two.top + 1).toBe(true);
        const { default: AxeBuilder } = require('@axe-core/playwright');
        const result = await new AxeBuilder({ page }).include('#projects').withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
        expect(result.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
        const names = await new AxeBuilder({ page }).include('#projects').withRules(['label-content-name-mismatch']).analyze();
        expect(names.violations).toEqual([]);
      });
    }
  }
}

test('without JavaScript all inline readings are honestly unknown and all browse links remain usable', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL, viewport: { width: 320, height: 480 } });
  try {
    const page = await context.newPage();
    const requests = [];
    page.on('request', req => requests.push(req.url()));
    await page.goto('/');
    await expect(page.locator('[data-project]')).toHaveCount(10);
    await expect(page.locator('[data-project] a')).toHaveCount(20);
    await expect(page.locator('[data-sync-state]')).toHaveText(Array(20).fill('Unknown'));
    await expect(page.locator('[data-disk-usage]')).toHaveText(Array(20).fill('Unavailable'));
    await expect(page.locator('[data-telemetry-note]')).toContainText('unavailable until a valid snapshot');
    await cell(page, 'gentoo').locator('summary').click();
    await expect(cell(page, 'gentoo').locator('.telemetry-scope')).toContainText('gentoo, gentoo-portage');
    expect(requests.some(url => url.endsWith('/status/mirrors.json'))).toBe(false);
  } finally { await context.close(); }
});

test('disk measurement age is visible without replacing timestamps or inventing missing values', async ({ page }) => {
  const data = payload();
  const old = data.mirrors.mirror1.snapshot.projects.archlinux;
  old.size_measured_at = NOW - 86401;
  old.repositories[0].size_measured_at = NOW - 86401;
  const unknown = metric({ state: 'unknown', exit_code: null, size_bytes: null, size_measured_at: null,
    last_attempt_at: null, last_finished_at: null, last_success_at: null,
    http_bytes_60s: null, traffic_observed_since: null });
  data.mirrors.mirror2.snapshot.projects.archlinux = { ...unknown, repositories: [{ id: 'archlinux', ...unknown }] };
  await openFixture(page, data);
  await expect(cell(page).locator('[data-size-note]')).toContainText('older than 24 hours');
  await expect(cell(page, 'archlinux', 'mirror2').locator('[data-sync-state]')).toHaveText('Unknown');
  await expect(cell(page, 'archlinux', 'mirror2').locator('[data-disk-usage]')).toHaveText('Unavailable');
  await cell(page).locator('summary').click();
  await expect(cell(page).locator('[data-group-metrics] [data-metric="size_measured_at"] time')).toHaveAttribute('datetime', new Date((NOW - 86401) * 1000).toISOString());
});

test('unmeasured bytes and incomplete traffic windows cannot become apparent measured zeros', async ({ page }) => {
  const data = payload();
  for (const mirror of Object.values(data.mirrors)) {
    const group = mirror.snapshot.projects.archlinux;
    for (const metric of [group, ...group.repositories]) {
      metric.size_bytes = 0;
      metric.size_measured_at = null;
      metric.sync_rx_bps = 0;
      metric.rates_observed_at = null;
      metric.http_bytes_60s = 0;
      metric.traffic_observed_since = NOW - 30;
    }
  }
  await openFixture(page, data);
  await expect(cell(page).locator('[data-disk-usage]')).toHaveText('Unavailable');
  await cell(page).locator('summary').click();
  const detail = cell(page).locator('[data-group-metrics]');
  await expect(detail.locator('[data-metric="sync_rx_bps"]')).toHaveText('Unavailable');
  await expect(detail.locator('[data-metric="http_bytes_60s"]')).toHaveText('Unavailable');
});

for (const [reason, change] of [
  ['old snapshot', d => { d.mirrors.mirror1.snapshot.collected_at = NOW - 61; }],
  ['future snapshot', d => { d.mirrors.mirror1.snapshot.collected_at = NOW + 31; }],
  ['failed aggregator fetch with retained snapshot', d => { d.mirrors.mirror1.available = false; }],
  ['old aggregate', d => { d.generated_at = NOW - 61; }],
]) {
  test(`${reason} cannot masquerade as a live completed badge`, async ({ page }) => {
    const data = payload(); change(data);
    await openFixture(page, data);
    await expect(cell(page)).toHaveAttribute('data-freshness', 'stale');
    await expect(cell(page).locator('[data-sync-state]')).toHaveText('Stale');
    await expect(cell(page).locator('[data-freshness-note]')).toContainText('not current');
    await expect(cell(page).locator('[data-disk-usage]')).toHaveText('1 GiB');
    await cell(page).locator('summary').click();
    await expect(cell(page).locator('[data-group-metrics] [data-metric="state"]')).toHaveText('Last observed: Completed');
    await expect(cell(page).locator('[data-repository="archlinux"] > p').first()).toContainText('Last observed: Completed');
  });
}

test('polls every 15 seconds, retains old measurements on failure and recovers without losing focus', async ({ page }) => {
  await page.clock.install({ time: new Date(NOW * 1000) });
  let requests = 0;
  let fail = false;
  await page.route(ENDPOINT, route => {
    requests += 1;
    return fail ? route.fulfill({ status: 503, body: '<h1>proxy failure</h1>' }) : route.fulfill({ json: payload() });
  });
  await page.goto('/');
  await expect(cell(page)).toHaveAttribute('data-freshness', 'live');
  const summary = cell(page).locator('summary');
  await summary.click();
  const measured = await cell(page).locator('[data-group-metrics] [data-metric="size_measured_at"]').textContent();
  expect(requests).toBe(1);
  fail = true;
  await page.clock.fastForward(15001);
  await expect(cell(page)).toHaveAttribute('data-freshness', 'stale');
  await expect(cell(page).locator('[data-sync-state]')).toHaveText('Stale');
  await expect(cell(page).locator('[data-freshness-note]')).toContainText('Update failed');
  await expect(cell(page).locator('[data-disk-usage]')).toHaveText('1 GiB');
  await expect(cell(page).locator('[data-group-metrics] [data-metric="size_measured_at"]')).toHaveText(measured);
  await expect(summary).toBeFocused();
  expect(requests).toBe(2);
  fail = false;
  await page.clock.fastForward(15001);
  await expect(cell(page)).toHaveAttribute('data-freshness', 'live');
  expect(requests).toBe(3);
  await expect(summary).toBeFocused();
  await expect(cell(page).locator('details')).toHaveAttribute('open', '');
  // Identical old snapshots age out even if the endpoint keeps replying 200.
  await page.clock.fastForward(31000);
  await expect(cell(page)).toHaveAttribute('data-freshness', 'stale');
});

test('hidden documents pause requests and demand an immediate fresh snapshot on resume', async ({ page }) => {
  await page.addInitScript(() => {
    window.testHidden = false;
    Object.defineProperty(document, 'hidden', { get: () => window.testHidden });
  });
  let requests = 0;
  await page.clock.install({ time: new Date(NOW * 1000) });
  await page.route(ENDPOINT, route => { requests += 1; return route.fulfill({ json: payload() }); });
  await page.goto('/');
  await expect(cell(page)).toHaveAttribute('data-freshness', 'live');
  await page.evaluate(() => { window.testHidden = true; document.dispatchEvent(new Event('visibilitychange')); });
  await expect(cell(page)).toHaveAttribute('data-freshness', 'paused');
  await expect(cell(page).locator('[data-sync-state]')).toHaveText('Paused');
  await page.clock.fastForward(45000);
  expect(requests).toBe(1);
  await page.evaluate(() => { window.testHidden = false; document.dispatchEvent(new Event('visibilitychange')); });
  await expect.poll(() => requests).toBe(2);
  await expect(cell(page)).toHaveAttribute('data-freshness', 'live');
});

test('a stalled request times out without overlapping and leaves unavailable rather than success', async ({ page }) => {
  let requests = 0;
  await page.clock.install({ time: new Date(NOW * 1000) });
  await page.route(ENDPOINT, async () => { requests += 1; });
  await page.goto('/');
  await expect(cell(page)).toHaveAttribute('data-freshness', 'loading');
  await page.clock.fastForward(7999);
  expect(requests).toBe(1);
  await page.clock.fastForward(2);
  await expect(cell(page)).toHaveAttribute('data-freshness', 'offline');
  await expect(cell(page).locator('[data-sync-state]')).toHaveText('Unavailable');
  await page.clock.fastForward(7000);
  await expect.poll(() => requests).toBe(2);
});

test('completed Gentoo with valid parent success and an unknown child is rejected independently', async ({ page }) => {
  const data = payload();
  Object.assign(data.mirrors.mirror1.snapshot.projects.gentoo.repositories[1], {
    state: 'unknown', exit_code: null, last_success_at: null,
  });
  await openFixture(page, data);
  await expect(cell(page, 'gentoo')).toHaveAttribute('data-freshness', 'offline');
  await expect(cell(page, 'gentoo').locator('[data-sync-state]')).toHaveText('Unavailable');
  await expect(cell(page, 'gentoo', 'mirror2')).toHaveAttribute('data-freshness', 'live');
  await expect(cell(page, 'gentoo', 'mirror2').locator('[data-sync-state]')).toHaveText('Completed');
});

for (const [states, expected, incorrect] of [
  [['syncing', 'failed', 'unknown'], 'syncing', 'failed'],
  [['failed', 'unknown', 'completed'], 'failed', 'unknown'],
  [['unknown', 'completed', 'completed'], 'unknown', 'completed'],
  [['completed', 'completed', 'completed'], 'completed', 'unknown'],
]) {
  test(`group precedence requires ${expected} for ${states.join('/')} and preserves stale data on rejection`, async ({ page }) => {
    const data = payload();
    const project = data.mirrors.mirror1.snapshot.projects.artix;
    project.state = expected;
    project.repositories.forEach((repository, index) => { repository.state = states[index]; });
    await openFixture(page, data);
    const reading = cell(page, 'artix');
    await expect(reading).toHaveAttribute('data-freshness', 'live');
    await expect(reading).toHaveAttribute('data-sync-state', expected);
    await reading.locator('summary').click();
    const measured = reading.locator('[data-group-metrics] [data-metric="collected_at"] time');
    const timestamp = await measured.getAttribute('datetime');
    project.state = incorrect;
    project.size_bytes = 0;
    data.mirrors.mirror1.snapshot.collected_at = NOW + 15;
    data.mirrors.mirror2.snapshot.projects.artix.size_bytes = 4294967296;
    await page.clock.fastForward(15001);
    await expect(reading).toHaveAttribute('data-freshness', 'stale');
    await expect(reading.locator('[data-sync-state]')).toHaveText('Stale');
    await expect(reading.locator('[data-disk-usage]')).toHaveText('3 GiB');
    await expect(measured).toHaveAttribute('datetime', timestamp);
    await expect(reading.locator('[data-group-metrics] [data-metric="state"]')).toHaveText(`Last observed: ${expected[0].toUpperCase()}${expected.slice(1)}`);
    await expect(cell(page, 'artix', 'mirror2')).toHaveAttribute('data-freshness', 'live');
    await expect(cell(page, 'artix', 'mirror2').locator('[data-disk-usage]')).toHaveText('4 GiB');
  });
}

for (const scope of ['project', 'repository']) {
  for (const [reason, overrides] of [
    ['missing exit', { exit_code: null }],
    ['failed exit', { exit_code: 5 }],
    ['unacknowledged warning', { exit_code: 24, warning: false }],
    ['missing success', { last_success_at: null }],
  ]) {
    test(`completion evidence rejects ${scope} with ${reason} independently`, async ({ page }) => {
      const data = payload();
      const project = data.mirrors.mirror1.snapshot.projects.gentoo;
      Object.assign(scope === 'project' ? project : project.repositories[1], overrides);
      await openFixture(page, data);
      await expect(cell(page, 'gentoo')).toHaveAttribute('data-freshness', 'offline');
      await expect(cell(page, 'gentoo').locator('[data-sync-state]')).toHaveText('Unavailable');
      await expect(cell(page, 'gentoo', 'mirror2')).toHaveAttribute('data-freshness', 'live');
      await expect(cell(page, 'gentoo', 'mirror2').locator('[data-sync-state]')).toHaveText('Completed');
    });
  }
}

for (const [name, corrupt] of [
  ['negative bytes', d => { d.mirrors.mirror1.snapshot.projects.archlinux.size_bytes = -1; }],
  ['unsafe bytes', d => { d.mirrors.mirror1.snapshot.projects.archlinux.size_bytes = Number.MAX_SAFE_INTEGER + 1; }],
  ['string bytes', d => { d.mirrors.mirror1.snapshot.projects.archlinux.size_bytes = '1024'; }],
  ['missing metric', d => { delete d.mirrors.mirror1.snapshot.projects.archlinux.size_bytes; }],
  ['negative rate', d => { d.mirrors.mirror1.snapshot.projects.archlinux.sync_rx_bps = -1; }],
  ['missing project', d => { delete d.mirrors.mirror1.snapshot.projects.gentoo; }],
  ['extra project', d => { d.mirrors.mirror1.snapshot.projects.evil = metric(); }],
  ['missing subtree', d => { d.mirrors.mirror1.snapshot.projects.artix.repositories.pop(); }],
  ['duplicate subtree', d => { d.mirrors.mirror1.snapshot.projects.gentoo.repositories[1].id = 'gentoo'; }],
  ['wrong mirror', d => { d.mirrors.mirror1.snapshot.mirror_id = 'mirror2'; }],
  ['invalid state', d => { d.mirrors.mirror1.snapshot.projects.archlinux.state = '<img src=x onerror=alert(1)>'; }],
]) {
  test(`schema rejects ${name} without making other mirror unavailable`, async ({ page }) => {
    const data = payload();
    corrupt(data);
    await openFixture(page, data);
    await expect(cell(page)).toHaveAttribute('data-freshness', 'offline');
    await expect(cell(page).locator('[data-sync-state]')).toHaveText('Unavailable');
    await expect(cell(page).locator('[data-disk-usage]')).toHaveText('Unavailable');
    await expect(cell(page, 'archlinux', 'mirror2').locator('[data-sync-state]')).toHaveText('Completed');
    await expect(page.locator('[data-project] img')).toHaveCount(0);
    await expect(page.locator('[data-project] a')).toHaveCount(20);
  });
}

test('nonfinite JSON and proxy HTML are rejected without displaying their contents', async ({ page }) => {
  await page.clock.install({ time: new Date(NOW * 1000) });
  let body = JSON.stringify(payload()).replace('"size_bytes":1073741824', '"size_bytes":1e309');
  await page.route(ENDPOINT, route => route.fulfill({ contentType: 'application/json', body }));
  await page.goto('/');
  await expect(cell(page)).toHaveAttribute('data-freshness', 'offline');
  body = '<h1>SECRET PROXY ERROR <script>window.injected=true</script></h1>';
  await page.reload();
  await expect(cell(page)).toHaveAttribute('data-freshness', 'offline');
  await expect(page.locator('body')).not.toContainText('SECRET PROXY');
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
});

test('details expose grouped repository scope, real measurement times and qualified traffic units', async ({ page }) => {
  const data = payload();
  const artix = data.mirrors.mirror1.snapshot.projects.artix;
  Object.assign(artix, { state: 'syncing', exit_code: null, sync_rx_bps: 2048, sync_tx_bps: 0, rates_observed_at: NOW - 5 });
  Object.assign(artix.repositories[0], { state: 'syncing', exit_code: null });
  Object.assign(artix.repositories[1], { state: 'failed', exit_code: 23 });
  Object.assign(artix.repositories[2], { warning: true, exit_code: 24 });
  await openFixture(page, data);
  const reading = cell(page, 'artix');
  const disclosure = reading.locator('summary').first();
  await expect(disclosure).toHaveAccessibleName('Artix Mirror 1 Details');
  await disclosure.click();
  await expect(reading.locator('[data-disk-usage]').first()).toHaveText('3 GiB');
  await expect(reading.locator('[data-repository]')).toHaveCount(3);
  await expect(reading.locator('[data-repository="artix-iso"]')).toContainText('Failed');
  await expect(reading.locator('[data-repository="artix-weekly-iso"]')).toContainText('Warning');
  for (const [id, repositories] of Object.entries(GROUPS)) {
    await expect(cell(page, id).locator('[data-repository]')).toHaveCount(repositories.length);
    expect(await cell(page, id).locator('[data-repository]').evaluateAll(nodes => nodes.map(node => node.dataset.repository))).toEqual(repositories);
  }
  const detail = reading.locator('[data-group-metrics]');
  await expect(detail.locator('[data-metric="size_measured_at"] time')).toHaveAttribute('datetime', new Date((NOW - 600) * 1000).toISOString());
  await expect(detail.locator('[data-metric="last_attempt_at"] time')).toHaveAttribute('datetime', new Date((NOW - 180) * 1000).toISOString());
  await expect(detail.locator('[data-metric="last_success_at"] time')).toHaveAttribute('datetime', new Date((NOW - 90) * 1000).toISOString());
  await expect(detail.locator('[data-metric="sync_rx_bps"]')).toHaveText('2 KiB/s');
  await expect(detail.locator('[data-metric="sync_tx_bps"]')).toHaveText('0 B/s');
  await expect(detail.locator('[data-metric="http_bytes_60s"]')).toHaveText('0 B');
  await expect(detail.locator('[data-metric="rsync_bytes_60s"]')).toHaveText('Unavailable');
  await expect(reading).toContainText('Observed sync connections');
  await expect(reading).toContainText('completed response bodies');
  await expect(reading).toContainText('not instantaneous NIC throughput');
  await expect(reading).toContainText('inter-mirror replication');
  await expect(reading).toContainText('Shared hardlinks may be counted once per repository');
  expect(await reading.locator('[aria-live], [role="status"]').count()).toBe(0);
});

test('same-origin snapshot enhances all original project rows with distinct inline mirror readings', async ({ page }) => {
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  const data = payload();
  data.mirrors.mirror2.snapshot.projects.archlinux = metric({
    state: 'syncing', exit_code: null, size_bytes: 2147483648,
    repositories: [{ id: 'archlinux', ...metric({ state: 'syncing', exit_code: null, size_bytes: 2147483648 }) }],
  });
  await openFixture(page, data);
  await expect(cell(page).locator('[data-sync-state]')).toHaveText('Completed');
  await expect(cell(page).locator('[data-disk-usage]')).toHaveText('1 GiB');
  await expect(cell(page, 'archlinux', 'mirror2').locator('[data-sync-state]')).toHaveText('Syncing');
  await expect(cell(page, 'archlinux', 'mirror2').locator('[data-disk-usage]')).toHaveText('2 GiB');
  await expect(page.locator('[data-project]')).toHaveCount(10);
  await expect(page.locator('[data-mirror-cell]')).toHaveCount(20);
  await expect(page.locator('[data-project] a')).toHaveCount(20);
  await expect(cell(page).getByRole('link')).toHaveAccessibleName('Arch Linux Mirror 1');
  expect(requests.filter(url => new URL(url).pathname === '/status/mirrors.json')).toHaveLength(1);
  expect(requests.filter(url => new URL(url).origin !== new URL(page.url()).origin)).toEqual([]);
  expect(requests.filter(url => /telemetry\.js|i18n\.js|\/components\//.test(url))).toEqual([]);
});
