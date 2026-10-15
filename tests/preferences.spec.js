const { test, expect } = require('@playwright/test');

for (const failure of ['getItem', 'setItem']) {
  test(`${failure} failure preserves working controls and stored startup preferences`, async ({ page }) => {
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
    await page.addInitScript(failure => {
      localStorage.setItem('keimirror-theme', 'dark');
      localStorage.setItem('keimirror-language', 'ko');
      Storage.prototype[failure] = () => {
        throw new DOMException('Unavailable storage', failure === 'setItem' ? 'QuotaExceededError' : 'SecurityError');
      };
    }, failure);
    await page.goto('/');
    await expect(page.locator('body')).toHaveClass('app-ready');
    await expect(page.locator('html')).toHaveAttribute('lang', failure === 'setItem' ? 'ko' : 'en');
    if (failure === 'setItem') await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.locator('#theme-toggle').click();
    await page.locator('#theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', failure === 'setItem' ? 'light' : 'dark');
    await page.locator('#language-toggle').click();
    await page.locator('#language-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('lang', failure === 'setItem' ? 'ko' : 'en');
    expect(errors).toEqual([]);
  });
}

test('normal preferences survive reload and Korean query overrides saved English', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('body')).toHaveClass('app-ready');
  await page.locator('#theme-toggle').click();
  await page.locator('#language-toggle').click();
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
  await page.locator('#language-toggle').click();
  await page.goto('/?lang=ko');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
});

test('mirror preview and sponsor controls still work with blocked storage', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', { get() { throw new Error('Blocked'); } });
  });
  await page.route('https://mirror.keiminem.com/**', route => route.fulfill({ body: '<p>Mirror fixture</p>', contentType: 'text/html' }));
  await page.goto('/');
  await expect(page.locator('body')).toHaveClass('app-ready');
  const mirror = page.locator('[data-mirror-trigger="mirror1"]');
  await mirror.click();
  await expect(page.locator('[data-mirror-list]')).toBeVisible();
  await expect(mirror).toHaveAttribute('aria-pressed', 'true');
  await mirror.click();
  await expect(page.locator('[data-mirror-list]')).toBeHidden();
  await page.locator('[data-sponsor-toggle]').first().click();
  await expect(page.locator('[data-sponsor-modal]')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-sponsor-modal]')).toBeHidden();
});

test('unsupported stored values fall back to valid defaults', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('keimirror-theme', 'sepia');
    localStorage.setItem('keimirror-language', 'xx');
  });
  await page.goto('/');
  await expect(page.locator('body')).toHaveClass('app-ready');
  await expect(page.locator('html')).not.toHaveAttribute('data-theme');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});

test('blocked storage does not prevent Korean startup or repeated preference changes', async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', {
      get() { throw new DOMException('Storage blocked', 'SecurityError'); },
    });
  });
  await page.goto('/?lang=ko');
  await expect(page.locator('body')).toHaveClass('app-ready');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
  await page.locator('#language-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await page.locator('#language-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
  for (const theme of ['light', 'dark']) {
    await page.locator('#theme-toggle').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  }
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).not.toHaveAttribute('data-theme');
  expect(errors).toEqual([]);
});
