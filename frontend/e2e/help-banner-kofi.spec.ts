import { test, expect } from '@playwright/test';

const HELP_DISMISS_KEY = 'cwng_help_banner_dismissed_v1';
const KOFI_DISMISS_KEY = 'cwng_kofi_banner_dismissed_v1';

test('help dismissal reveals a durable, independently dismissible Ko-fi banner', async ({ page }) => {
  await page.goto('/app');
  await page.evaluate(([helpKey, kofiKey]) => {
    localStorage.removeItem(helpKey);
    localStorage.removeItem(kofiKey);
  }, [HELP_DISMISS_KEY, KOFI_DISMISS_KEY]);
  await page.reload();

  const banner = page.getByRole('status').filter({
    hasText: /Need to report an issue\? Try the new|Less than Netflix to keep us afloat/,
  });
  const supportLink = page.getByRole('link', { name: 'Join on Ko-fi →' });

  await expect(banner).toContainText('Need to report an issue? Try the new');
  await expect(supportLink).toHaveCount(0);

  await banner.getByRole('button', { name: /Dismiss(?: help announcement)?/ }).click();
  await expect(banner).toContainText('Less than Netflix to keep us afloat');
  await expect(supportLink).toHaveAttribute('href', 'https://ko-fi.com/calibrewebnextgen');
  await expect(supportLink).toHaveAttribute('target', '_blank');
  await expect(supportLink).toHaveAttribute('rel', 'noopener noreferrer');
  expect(await page.evaluate((key) => localStorage.getItem(key), HELP_DISMISS_KEY)).toBe('1');

  await page.reload();
  await expect(supportLink).toBeVisible();
  await expect(page.getByText('Need to report an issue? Try the new')).toHaveCount(0);

  await page.getByRole('button', { name: 'Dismiss Ko-fi support message' }).click();
  await expect(banner).toHaveCount(0);
  expect(await page.evaluate((key) => localStorage.getItem(key), KOFI_DISMISS_KEY)).toBe('1');

  await page.reload();
  await expect(banner).toHaveCount(0);
});
