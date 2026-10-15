const { test, expect } = require('@playwright/test');

test('server status follows Korean query language and repeated language switching', async ({ page }) => {
  await page.goto('/?lang=ko');
  const status = page.locator('nav [data-i18n="navServerStatus"]');
  await expect(status).toHaveText('서버상태');
  await page.locator('#language-toggle').click();
  await expect(status).toHaveText('Server status');
  await page.locator('#language-toggle').click();
  await expect(status).toHaveText('서버상태');
});

const dashboardUrl = 'https://grafana.keiminem.com/public-dashboards/32d4cf1d87a24a31b5540995402ab6ee';

for (const width of [1280, 375, 320]) {
  test(`server status sits beside Donate and opens the public dashboard safely (${width}px)`, async ({ page, context }) => {
    await page.setViewportSize({ width, height: 900 });
    await page.goto('/');
    const status = page.getByRole('navigation').getByRole('link', { name: 'Server status', exact: true });
    await expect(status).toBeVisible();
    await expect(status).toHaveClass('button');
    await expect(status).toHaveAttribute('href', dashboardUrl);
    await expect(status).toHaveAttribute('target', '_blank');
    expect((await status.getAttribute('rel')).split(/\s+/)).toEqual(expect.arrayContaining(['noopener', 'noreferrer']));
    expect(await status.evaluate(node => node.previousElementSibling.matches('[data-sponsor-toggle]'))).toBe(true);
    const donate = page.getByRole('navigation').getByRole('button', { name: 'Donate', exact: true });
    const donateBox = await donate.boundingBox();
    const statusBox = await status.boundingBox();
    expect(Math.abs(statusBox.y - donateBox.y)).toBeLessThan(2);
    expect(statusBox.x).toBeGreaterThanOrEqual(donateBox.x + donateBox.width);
    expect(statusBox.x + statusBox.width).toBeLessThanOrEqual(width);

    // Keep CI independent of Grafana availability; live access is checked separately.
    await context.route(dashboardUrl, route => route.fulfill({ contentType: 'text/html', body: '<title>Dashboard navigation fixture</title>' }));
    const popupPromise = page.waitForEvent('popup');
    await status.click();
    const popup = await popupPromise;
    await popup.waitForLoadState();
    await expect(popup).toHaveURL(dashboardUrl);
    expect(await popup.evaluate(() => window.opener)).toBeNull();
    await expect(page).toHaveURL('http://127.0.0.1:18763/');
  });
}
