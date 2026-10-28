const { test, expect } = require('@playwright/test');

const projectPaths = {
  'Arch Linux': '/archlinux/', Gentoo: '/gentoo/', 'Kali Linux': '/kali/',
  CachyOS: '/cachyos/', Artix: '/artix/', OPNsense: '/opnsense/',
  EndeavourOS: '/endeavouros/', 'Fyra Labs': '/fyralabs/', Manjaro: '/manjaro/', Proxmox: '/proxmox/',
};

test('every project opens its verified directory on both mirrors, not the root', async ({ page }) => {
  await page.goto('/');
  for (const [name, path] of Object.entries(projectPaths)) {
    const row = page.locator('[data-project]').filter({ has: page.getByRole('heading', { name, exact: true }) });
    await expect(row.locator('.project-links a')).toHaveCount(2);
    await expect(row.locator('.project-links a').nth(0)).toHaveAttribute('href', `https://mirror.keiminem.com${path}`);
    await expect(row.locator('.project-links a').nth(1)).toHaveAttribute('href', `https://mirror2.keiminem.com${path}`);
  }
});

test('English query overrides saved Korean just as Korean query overrides English', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('keimirror-language', 'ko'));
  await page.goto('/?lang=en');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});

test('theme labels remain readable without platform emoji fonts', async ({ page }) => {
  await page.goto('/?lang=en');
  for (const label of ['Auto', 'Light', 'Dark']) {
    await expect(page.locator('#theme-toggle')).toHaveText(label);
    await page.locator('#theme-toggle').click();
  }
});
