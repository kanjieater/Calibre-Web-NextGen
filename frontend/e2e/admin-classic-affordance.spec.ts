import { test, expect } from '@playwright/test';

test('advanced server links disclose the intentional classic-view transition (#909)', async ({ page }) => {
  await page.goto('/app/admin');
  await expect(page.getByText('Pages marked below open in the classic view. Changes there apply to the whole server.')).toBeVisible();
  const cards = page.locator('a[href*="/admin/"], a[href$="/cwa-settings"], a[href$="/cwa-stats-show"]')
    .filter({ hasText: 'Opens in classic view' });
  await expect(cards).toHaveCount(8);

  const duplicates = page.getByRole('link', { name: 'Duplicate books' });
  await expect(duplicates).toHaveAttribute('href', /\/app\/duplicates$/);
  await expect(duplicates).not.toContainText('Opens in classic view');
});
