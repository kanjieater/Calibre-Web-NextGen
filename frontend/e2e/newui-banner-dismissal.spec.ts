import { test, expect, type BrowserContext, type Page } from '@playwright/test';

async function openClassic(page: Page, context: BrowserContext) {
  await page.goto('/app');
  await context.addCookies([{ name: 'cwng_prefer_spa', value: '0', url: new URL(page.url()).origin }]);
  await page.goto('/');
}

async function clearDismissals(page: Page) {
  await page.evaluate(() => {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith('cwng_newui_banner_dismissed_')) localStorage.removeItem(key);
    }
  });
}

test('closing the classic New-UI nudge keeps it dismissed after reload (#907)', async ({ page, context }) => {
  await openClassic(page, context);
  await clearDismissals(page);
  await page.reload();

  const banner = page.locator('#cwng-newui-banner');
  await expect(banner).toBeVisible();
  await page.locator('.cwng-newui-dismiss').click();
  await expect(banner).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('cwng_newui_banner_dismissed_v1'))).toBe('1');

  await page.reload();
  await expect(banner).toBeHidden();
});

test('an old per-version dismissal migrates without one more nudge (#907)', async ({ page, context }) => {
  await openClassic(page, context);
  await clearDismissals(page);
  await page.evaluate(() => localStorage.setItem('cwng_newui_banner_dismissed_v4.1.13', '1'));

  await page.reload();
  await expect(page.locator('#cwng-newui-banner')).toBeHidden();
  expect(await page.evaluate(() => localStorage.getItem('cwng_newui_banner_dismissed_v1'))).toBe('1');
});
