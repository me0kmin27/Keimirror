const { test, expect } = require('@playwright/test');

test('the homepage and mirror links remain usable without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false });
  const page = await context.newPage();
  try {
    await page.goto('http://127.0.0.1:18763/');
    await expect(page.locator('h1')).toBeVisible();
    await expect(page.locator('a[href="https://mirror.keiminem.com/"]').first()).toBeVisible();
    await expect(page.locator('a[href="https://mirror2.keiminem.com/"]').first()).toBeVisible();
    await expect(page.locator('a[href="mailto:me0kmin27@keiminem.com"]')).toBeVisible();
  } finally {
    await context.close();
  }
});

test('initial rendering makes no component or third-party requests', async ({ page }) => {
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  await page.goto('/');
  await expect(page.locator('body')).toHaveClass('app-ready');
  await expect(page.locator('h1')).toBeVisible();
  expect(requests.filter(url => new URL(url).origin !== 'http://127.0.0.1:18763')).toEqual([]);
  expect(requests.filter(url => new URL(url).pathname.startsWith('/components/'))).toEqual([]);
});
