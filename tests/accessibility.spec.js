const { test, expect } = require('@playwright/test');
const { default: AxeBuilder } = require('@axe-core/playwright');

for (const language of ['en', 'ko']) {
  for (const theme of ['light', 'dark']) {
    test(`WCAG checks pass for ${language} ${theme} including sponsor dialog`, async ({ page }) => {
      await page.addInitScript(theme => localStorage.setItem('keimirror-theme', theme), theme);
      await page.goto(`/?lang=${language}`);
      await expect(page.locator('html')).toHaveAttribute('lang', language);
      await expect(page.locator('body')).toHaveClass('app-ready');
      const audit = async () => {
        const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
        expect(result.violations.map(v => ({ id: v.id, impact: v.impact, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
      };
      await audit();
      await page.locator('[data-sponsor-toggle]').first().click();
      await expect(page.locator('[data-sponsor-modal]')).toBeVisible();
      await audit();
    });

    test(`visible labels pass label-content-name-mismatch for ${language} ${theme}`, async ({ page }) => {
      await page.addInitScript(theme => localStorage.setItem('keimirror-theme', theme), theme);
      await page.goto(`/?lang=${language}`);
      await expect(page.locator('body')).toHaveClass('app-ready');
      const result = await new AxeBuilder({ page }).withRules(['label-content-name-mismatch']).analyze();
      expect(result.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
    });

    test(`project and language accessible names include localized labels in ${language} ${theme}`, async ({ page }) => {
      await page.addInitScript(theme => localStorage.setItem('keimirror-theme', theme), theme);
      await page.goto(`/?lang=${language}`);
      await expect(page.locator('body')).toHaveClass('app-ready');
      const rows = page.locator('[data-project]');
      await expect(rows).toHaveCount(10);
      await expect(rows.locator('.project-links a')).toHaveCount(20);
      for (const row of await rows.all()) {
        const project = await row.locator('h3').innerText();
        for (const [index, link] of (await row.locator('.project-links a').all()).entries()) {
          const mirror = `${language === 'ko' ? '미러' : 'Mirror'} ${index + 1}`;
          await expect(link.locator('[data-i18n]')).toHaveText(mirror);
          await expect(link).toHaveAccessibleName(`${project} ${mirror}`);
        }
      }
      const toggle = page.locator('#language-toggle');
      await expect(toggle).toHaveText(language === 'en' ? '한국어' : 'English');
      await expect(toggle).toHaveAccessibleName(language === 'en' ? 'Switch to Korean (한국어)' : '영어로 전환 (English)');
    });
  }
}

test('IDs are unique and every internal section link resolves', async ({ page }) => {
  await page.goto('/');
  const violations = await page.evaluate(() => {
    const ids = [...document.querySelectorAll('[id]')].map(el => el.id);
    const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index);
    const missing = [...document.querySelectorAll('a[href^="#"]')]
      .map(a => a.getAttribute('href')).filter(href => href.length > 1 && !document.getElementById(href.slice(1)));
    return { duplicates, missing };
  });
  expect(violations).toEqual({ duplicates: [], missing: [] });
});
