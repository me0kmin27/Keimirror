const { test, expect } = require('@playwright/test');

test('concurrent cold pages load every component before usable Korean controls', async ({ browser }) => {
  const contexts = await Promise.all([0, 1].map(() => browser.newContext()));
  const failures = [];
  try {
    const pages = await Promise.all(contexts.map(context => context.newPage()));
    for (const page of pages) {
      page.on('requestfailed', request => failures.push(`${request.url()}: ${request.failure().errorText}`));
      page.on('pageerror', error => failures.push(error.message));
      page.on('console', message => { if (message.type() === 'error') failures.push(message.text()); });
    }
    await Promise.all(pages.map(async page => {
      await page.goto('/?lang=ko');
      await expect(page.locator('body')).toHaveClass('app-ready');
      expect(failures).toEqual([]);
      await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
      expect(await page.locator('[data-component]').evaluateAll(hosts => hosts.filter(host => !host.innerHTML.trim()).map(host => host.dataset.component))).toEqual([]);
      await page.locator('#theme-toggle').click();
      await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
      await expect(page.locator('nav [data-i18n="navServerStatus"]')).toHaveText('서버상태');
    }));
    expect(failures).toEqual([]);
  } finally {
    await Promise.all(contexts.map(context => context.close()));
  }
});
