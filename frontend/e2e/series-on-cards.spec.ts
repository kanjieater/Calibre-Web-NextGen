import { test, expect, Page } from '@playwright/test';

/*
 * Fork issue #657 (also #673, and #855's follow-up) — the new UI dropped the
 * series name/number that the classic view showed under each book cover. Users
 * with series-heavy libraries navigate by series and could no longer tell which
 * series a book belonged to, or its position, without opening every book.
 *
 * The series name + position are restored on the book cards in the general
 * lists (main library grid, search, shelves). They are deliberately NOT repeated
 * in the series-detail view, where every card is the same series and the
 * position already shows as the #N badge — a repeated name there would be noise.
 *
 * Seed-resilient: probes the API for a book that has a series and skips (not
 * fails) when the library has none, so it stays green on any seed.
 */

interface Probe {
  bookId: number | null;
  title: string | null;
  series: string | null;
}

async function probeSeriesBook(page: Page): Promise<Probe> {
  return await page.evaluate(async () => {
    const r = await fetch('/api/v1/books?per_page=250', { credentials: 'same-origin' });
    if (!r.ok) return { bookId: null, title: null, series: null };
    const d = await r.json();
    const hit = (d.items || []).find(
      (b: { series?: string | null }) => b.series != null && b.series !== '',
    );
    return hit
      ? { bookId: hit.id, title: hit.title, series: hit.series }
      : { bookId: null, title: null, series: null };
  });
}

test.describe('#657 series on book cards', () => {
  test('a book that has a series shows its series name on the card', async ({ page }) => {
    await page.goto('/app/');
    const { bookId, title, series } = await probeSeriesBook(page);
    test.skip(!bookId, 'seed has no book with a series');

    // Search for the exact title so the target card is rendered in a small
    // result grid (the full library grid is virtualized, so a specific book may
    // not be in the initial window). Search results use the same BookCard in
    // general-list mode, so the series line is the thing under test here.
    await page.goto(`/app/?q=${encodeURIComponent(title!)}`);
    const card = page.locator(`a[href$="/book/${bookId}"]`).first();
    await expect(card).toBeVisible();
    const seriesLine = card.getByTestId('book-card-series');
    await expect(seriesLine).toBeVisible();
    await expect(seriesLine).toContainText(series!);
  });

  test('series-detail view does not repeat the series name on every card', async ({ page }) => {
    await page.goto('/app/');
    // Discover a series id so we can open its detail view.
    const seriesId = await page.evaluate(async () => {
      const r = await fetch('/api/v1/series?per_page=1', { credentials: 'same-origin' });
      if (!r.ok) return null;
      const d = await r.json();
      const first = (d.items || [])[0];
      return first ? first.id : null;
    });
    test.skip(!seriesId, 'seed has no series');

    await page.goto(`/app/series/${seriesId}`);
    // In the series-detail grid the redundant name line is suppressed; the #N
    // position badge carries the ordering instead.
    await expect(page.getByTestId('book-card-series')).toHaveCount(0);
  });
});
