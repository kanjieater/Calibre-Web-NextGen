import { test, expect, Page } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

/**
 * Convert target dropdown:
 *   - The "to" field is a dropdown, not a free-text input.
 *   - Target options exclude the currently selected source format.
 *   - Submitting the form POSTs the chosen {from, to} pair.
 *
 * Seed-resilient: queries the API for a book with at least one source format
 * and one different target format, skipping otherwise.
 */

interface ConvertibleBook {
  id: number;
  source: string;
  target: string;
}

async function findConvertibleBook(page: Page): Promise<ConvertibleBook | null> {
  return page.evaluate(async () => {
    const j = (u: string): Promise<any> =>
      fetch(u, { headers: { Accept: 'application/json' } })
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null);

    const items = (await j('/api/v1/books?per_page=20'))?.items ?? [];
    for (const item of items) {
      const detail = await j(`/api/v1/books/${item.id}`);
      const sources = detail?.convert_options?.sources ?? [];
      const targets = detail?.convert_options?.targets ?? [];
      if (sources.length && targets.length) {
        const source = sources[0];
        const target = targets.find((t: string) => t.toLowerCase() !== source.toLowerCase());
        if (target) return { id: item.id, source, target };
      }
    }
    return null;
  });
}

test('convert form offers a target dropdown that excludes the source format and posts the chosen pair', async ({ page }) => {
  await page.goto('/app');
  const book = await findConvertibleBook(page);
  test.skip(book == null, 'seed has no book with convertible source/target pair');

  const errors = collectPageErrors(page);

  // Stub the convert endpoint so we don't mutate the seed library.
  let postedBody: Record<string, string> | null = null;
  await page.route(`/api/v1/books/${book!.id}/convert`, async (route, request) => {
    if (request.method() === 'POST') {
      postedBody = request.postDataJSON();
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, message: 'Queued for conversion to MOBI' }),
      });
    } else {
      await route.continue();
    }
  });

  await page.goto(`/app/book/${book!.id}/edit`, { waitUntil: 'domcontentloaded' });

  // The convert section has two selects and a visible "to" separator.
  const fromSelect = page.locator('select', { hasText: book!.source.toUpperCase() }).first();
  const toSelect = page.locator('select', { has: page.locator('option[value=""]', { hasText: /select format/i }) }).first();
  await expect(fromSelect).toBeVisible({ timeout: 10_000 });
  await expect(toSelect).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('text=to').first()).toBeVisible();

  // The "to" dropdown does not contain the selected source format.
  const toOptions = await toSelect.locator('option').allTextContents();
  expect(toOptions.map((o) => o.toLowerCase())).not.toContain(book!.source.toLowerCase());

  // Choose a target and submit.
  await toSelect.selectOption(book!.target.toUpperCase());
  await page.getByRole('button', { name: /convert/i }).click();

  await expect(page.locator('text=Queued for conversion').first()).toBeVisible({ timeout: 10_000 });
  expect(postedBody).toEqual({
    from: book!.source.toUpperCase(),
    to: book!.target.toUpperCase(),
  });

  assertNoPageErrors(errors);
});
