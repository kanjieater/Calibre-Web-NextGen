import { test, expect, type Locator } from '@playwright/test';

const isTouchProject = () => ['mobile', 'ipad-touch'].includes(test.info().project.name);

async function expectRevealed(locator: Locator, revealed: boolean, message: string) {
  await expect.poll(
    () => locator.evaluate((node) => getComputedStyle(node).opacity),
    { message },
  ).toBe(revealed ? '1' : '0');
}

test('book-card actions keep a shared baseline for touch, mouse, and keyboard', async ({ page }) => {
  await page.goto('/app');

  const details = page.locator('a[aria-label^="Open details for"]');
  await expect(details.first()).toBeVisible();
  expect(await details.count(), 'the catalog fixture needs at least two books').toBeGreaterThan(1);

  const firstCard = details.nth(0).locator('..');
  const secondCard = details.nth(1).locator('..');
  const firstTitle = details.nth(0).locator('p').first();
  const secondTitle = details.nth(1).locator('p').first();
  const firstRead = firstCard.locator('a[aria-label^="Read "]');
  const secondRead = secondCard.locator('a[aria-label^="Read "]');

  await expect(firstRead).toHaveCount(1);
  await expect(secondRead).toHaveCount(1);

  // Deterministic reporter data shape: one one-line title beside one title that
  // reaches the two-line clamp. This changes fixture text only; the production
  // BookCard layout and media-query behavior remain untouched.
  await firstTitle.evaluate((node) => { node.textContent = 'Short'; });
  await secondTitle.evaluate((node) => {
    node.textContent = 'A deliberately long title that must occupy the complete two-line card-title allowance';
  });

  for (const theme of ['light', 'dark'] as const) {
    await page.evaluate((value) => document.documentElement.setAttribute('data-theme', value), theme);
    await page.waitForTimeout(250);

    const titleHeights = await Promise.all([
      firstTitle.evaluate((node) => node.getBoundingClientRect().height),
      secondTitle.evaluate((node) => node.getBoundingClientRect().height),
    ]);
    expect(
      Math.abs(titleHeights[0] - titleHeights[1]),
      `${theme}: one- and two-line titles reserve the same two-line block`,
    ).toBeLessThanOrEqual(1);

    const actionBottoms = await Promise.all([
      firstRead.evaluate((node) => node.getBoundingClientRect().bottom),
      secondRead.evaluate((node) => node.getBoundingClientRect().bottom),
    ]);
    expect(
      Math.abs(actionBottoms[0] - actionBottoms[1]),
      `${theme}: Read now actions share a bottom baseline`,
    ).toBeLessThanOrEqual(1);

    if (isTouchProject()) {
      await expectRevealed(firstRead, true, `${theme}: Read now stays visible without hover`);
      const quickEdit = page.locator('a[aria-label^="Edit "]').first();
      await expect(quickEdit).toHaveCount(1);
      await expectRevealed(quickEdit, true, `${theme}: the adjacent quick-edit action is touch-reachable`);
    } else {
      await page.evaluate(() => (document.activeElement as HTMLElement)?.blur());
      await page.mouse.move(0, 0);
      await expectRevealed(firstRead, false, `${theme}: desktop starts with the clean hover treatment`);
      await firstCard.hover();
      await expectRevealed(firstRead, true, `${theme}: mouse hover reveals Read now`);
      await page.mouse.move(0, 0);
      await firstRead.focus();
      await expectRevealed(firstRead, true, `${theme}: keyboard focus reveals Read now`);
    }
  }
});
