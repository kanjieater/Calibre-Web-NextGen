import { expect, test, type Page } from '@playwright/test';

/*
 * #325 — the in-reader "Highlights and notes" drawer.
 *
 * Classic has an annotations panel inside the reader (`#annotationsView`); the
 * SPA had none, so seeing your own highlights meant leaving the book for
 * /book/<id>/annotations and losing your place. This is the "see" half of the
 * operator's ask, the composer being the "do" half.
 *
 * Same three surface facts as reader-notes.spec.ts apply — synthetic drags hang
 * over the epub.js iframe, highlights paint into a marks-pane overlay in the
 * PARENT document, and the first page is usually a text-free cover.
 */

async function openReaderOnEpub(page: Page): Promise<number | null> {
  const res = await page.request.get('/api/v1/books?page=1&per_page=100&sort=new');
  const books = await res.json();
  // Lowest id: sibling specs upload fixtures that become the newest book.
  const candidates = [...(books.items || books.books || [])]
    .sort((a: { id: number }, b: { id: number }) => a.id - b.id);
  for (const bk of candidates) {
    const detail = await (await page.request.get(`/api/v1/books/${bk.id}`)).json();
    if (!(detail.formats || []).some((f: { format: string }) => f.format.toLowerCase() === 'epub')) continue;
    await page.goto(`/app/read/${bk.id}`);
    const rendered = await page.locator('iframe').waitFor({ state: 'visible', timeout: 8000 })
      .then(() => true).catch(() => false);
    if (rendered) { await page.waitForTimeout(4000); return bk.id; }
  }
  return null;
}

async function pageUntilText(page: Page): Promise<boolean> {
  for (let i = 0; i < 12; i++) {
    const frame = page.frames().find((f) => f !== page.mainFrame());
    const len = frame
      ? await frame.evaluate(() => (document.body?.innerText || '').trim().length).catch(() => 0)
      : 0;
    if (len > 300) return true;
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(1200);
  }
  return false;
}

async function selectSomeText(page: Page): Promise<string> {
  const frame = page.frames().find((f) => f !== page.mainFrame())!;
  return await frame.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node: Node | null = null;
    while ((node = walker.nextNode())) if ((node.textContent || '').trim().length > 60) break;
    if (!node) return '';
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.min(70, (node.textContent || '').length));
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange', { bubbles: true }));
    const box = range.getBoundingClientRect();
    for (const type of ['mousedown', 'mouseup'])
      document.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + 5, clientY: box.y + 5 }));
    return String(sel);
  });
}

const setNote = (page: Page, text: string) => page.evaluate((v) => {
  const ta = document.querySelector('textarea')!;
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!.call(ta, v);
  ta.dispatchEvent(new Event('input', { bubbles: true }));
}, text);

const annotationIds = (page: Page, bookId: number) => page.evaluate(async (id) => {
  const r = await fetch(`/annotations/${id}/data.json`, { credentials: 'include' });
  return ((await r.json()).annotations || []).map((a: { annotation_id: string }) => a.annotation_id);
}, bookId);

async function restoreAnnotations(page: Page, bookId: number, keep: string[]) {
  await page.evaluate(async ([id, known]) => {
    const csrf = (await (await fetch('/api/v1/auth/csrf', { credentials: 'include' })).json()).csrf_token;
    const rows = (await (await fetch(`/annotations/${id}/data.json`, { credentials: 'include' })).json()).annotations || [];
    for (const a of rows) {
      if ((known as string[]).includes(a.annotation_id)) continue;
      await fetch(`/annotations/${id}/${a.annotation_id}`, {
        method: 'DELETE', credentials: 'include', headers: { 'X-CSRFToken': csrf },
      });
    }
  }, [bookId, keep] as [number, string[]]);
}

const drawer = (page: Page) => page.getByRole('navigation', { name: 'Highlights and notes' });

test.describe('reader highlights & notes drawer (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('lists a highlight with its note, and jumps to it', async ({ page }) => {
    const bookId = await openReaderOnEpub(page);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);
    const preExisting = await annotationIds(page, bookId!);

    // Empty state is honest before anything exists.
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    if (preExisting.length === 0) {
      await expect(drawer(page)).toContainText('No highlights yet');
    }
    await page.keyboard.press('Escape');

    // Make one, with a note.
    expect(await selectSomeText(page)).not.toBe('');
    await page.getByRole('button', { name: 'Add note' }).click();
    const NOTE = `Panel note ${Date.now()}`;
    await setNote(page, NOTE);
    await page.getByRole('button', { name: 'Save note' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length + 1);

    // It appears in the drawer, with its note, without a reload.
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page)).toBeVisible();
    await expect(drawer(page)).toContainText(NOTE);
    const rows = drawer(page).locator('li');
    await expect(rows).toHaveCount(preExisting.length + 1);

    // Jumping closes the drawer and moves the book.
    await rows.last().locator('button').click();
    await expect(drawer(page)).toBeHidden();

    // Survives a reload — the drawer is populated from the server, not memory.
    await page.reload();
    await page.waitForTimeout(5000);
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page)).toContainText(NOTE);

    await restoreAnnotations(page, bookId!, preExisting);
  });

  test('the drawer reflects a removed highlight without a reload', async ({ page }) => {
    const bookId = await openReaderOnEpub(page);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);
    const preExisting = await annotationIds(page, bookId!);

    await selectSomeText(page);
    await page.getByRole('button', { name: 'Yellow' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length + 1);

    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page).locator('li')).toHaveCount(preExisting.length + 1);
    await page.keyboard.press('Escape');

    // Delete it through the reader, then re-open the drawer.
    await page.evaluate(() => {
      const g = document.querySelector('.cwng-hl')!;
      const box = g.getBoundingClientRect();
      for (const type of ['mousedown', 'mouseup', 'click'])
        g.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 }));
    });
    await page.getByRole('button', { name: 'Remove highlight' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length);

    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page).locator('li')).toHaveCount(preExisting.length);

    await restoreAnnotations(page, bookId!, preExisting);
  });
});
