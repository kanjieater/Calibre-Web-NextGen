import { test, expect } from '@playwright/test';

test('editor renames a tag from its entity page and the name persists (#914)', async ({ page, isMobile }) => {
  await page.goto('/app/tags');
  // Desktop and mobile projects run concurrently; use different seed rows so
  // each test owns and restores its mutation without racing the other.
  const first = page.locator('a[href*="/tags/"]').nth(isMobile ? 1 : 0);
  const oldName = ((await first.innerText()).split('\n')[0] || '').trim();
  const href = await first.getAttribute('href');
  test.skip(!href || !oldName, 'seed has no tags');
  const renamed = `${oldName} e2e-${Date.now()}`;

  await first.click();
  const rename = page.getByRole('button', { name: `Rename tag ${oldName}` });
  await expect(rename).toBeVisible();
  let restored = false;
  try {
    await rename.click();
    await expect(page.locator('main h1')).toHaveCount(1);
    await expect(page.locator('main h1')).toHaveText(oldName);
    const input = page.getByRole('textbox', { name: 'Tag name' });
    await input.press('Escape');
    await expect(rename).toBeFocused();
    await rename.click();
    await input.fill(renamed);
    const response = page.waitForResponse((res) => res.url().includes('/api/v1/tags/') && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Save tag name' }).click();
    expect((await response).ok()).toBeTruthy();
    await expect(page.locator('main h1')).toHaveText(renamed);
    await expect(page).toHaveTitle(`${renamed} · Calibre-Web NextGen`);
    await expect(page.locator('[aria-live="polite"]')).toHaveText(`Tag renamed to ${renamed}`);

    await page.getByRole('button', { name: `Rename tag ${renamed}` }).click();
    await page.getByRole('textbox', { name: 'Tag name' }).fill(oldName);
    await page.getByRole('button', { name: 'Save tag name' }).click();
    await expect(page.locator('main h1')).toHaveText(oldName);
    restored = true;
  } finally {
    if (!restored) {
      await page.goto(href!);
      const current = (await page.locator('main h1').innerText()).trim();
      if (current === renamed) {
        await page.getByRole('button', { name: `Rename tag ${renamed}` }).click();
        await page.getByRole('textbox', { name: 'Tag name' }).fill(oldName);
        await page.getByRole('button', { name: 'Save tag name' }).click();
      }
    }
  }
});
