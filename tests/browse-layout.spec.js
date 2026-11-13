const { test, expect } = require('@playwright/test');

for (const width of [320, 1440]) {
  for (const language of ['en', 'ko']) {
    for (const theme of ['light', 'dark']) {
      test(`browse-first compact directory at ${width}px in ${language} ${theme}`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: width === 320 ? 568 : 1000 });
        await page.addInitScript(theme => localStorage.setItem('keimirror-theme', theme), theme);
        await page.goto(`/?lang=${language}`);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        await expect(page.locator('html')).toHaveAttribute('lang', language);
        await expect.soft(page.locator('#project-status-title')).toHaveText(language === 'ko' ? '미러 프로젝트' : 'Mirrored projects');
        const geometry = await page.evaluate(() => {
          const rect = node => {
            const { top, right, bottom, left, width, height } = node.getBoundingClientRect();
            return { top, right, bottom, left, width, height };
          };
          const controls = [...document.querySelectorAll('button, input, .project-links a, .endpoint-address a, .mirror-preview-trigger, .hero-actions a')].filter(node => node.getClientRects().length);
          return {
            pageWidth: document.documentElement.scrollWidth,
            header: rect(document.querySelector('.topbar')),
            directory: rect(document.querySelector('#projects')),
            access: rect(document.querySelector('#mirror-access')),
            first: rect(document.querySelector('[data-project]')),
            rows: [...document.querySelectorAll('[data-project]')].map(row => ({
              row: rect(row),
              links: [...row.querySelectorAll('a')].map(rect),
            })),
            shortTargets: controls.filter(node => rect(node).height < 43.9 || rect(node).width < 43.9).map(node => node.outerHTML),
            overflow: controls.filter(node => rect(node).left < 0 || rect(node).right > innerWidth).map(node => node.outerHTML),
          };
        });
        await testInfo.attach('geometry', { body: JSON.stringify(geometry, null, 2), contentType: 'application/json' });
        expect.soft(geometry.access.top).toBeGreaterThan(geometry.directory.bottom);
        expect.soft(geometry.header.height).toBeLessThanOrEqual(width === 320 ? 128 : 76);
        expect.soft(geometry.first.top).toBeLessThanOrEqual(width === 320 ? 650 : 460);
        expect(geometry.pageWidth).toBeLessThanOrEqual(width);
        expect(geometry.shortTargets).toEqual([]);
        expect(geometry.overflow).toEqual([]);
        for (const { row, links } of geometry.rows) {
          expect.soft(row.height).toBeLessThanOrEqual(width === 320 ? 112 : 76);
          expect(links[0].right).toBeLessThanOrEqual(links[1].left);
          expect(Math.abs(links[0].top - links[1].top)).toBeLessThan(1);
        }
        await page.screenshot({ path: testInfo.outputPath(`home-${width}-${language}-${theme}.png`), fullPage: true });
      });
    }
  }
}
