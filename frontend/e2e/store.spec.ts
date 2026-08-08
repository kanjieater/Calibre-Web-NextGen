import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const WORKS = [
  {
    provider: 'annas_archive', provider_id: 'children', title: 'Children of Dune',
    authors: ['Frank Herbert'], publish_year: 1976, cover_url: null,
  },
  {
    provider: 'annas_archive', provider_id: 'dune', title: 'Dune',
    authors: ['Frank Herbert'], publish_year: 1965, cover_url: null,
  },
];

const RELEASES = [
  {
    source: 'direct_download', source_id: 'thin', title: 'Dune — scanned PDF',
    format: 'pdf', size: 4000, language: 'en', extra: { language: 'en' },
  },
  {
    source: 'direct_download', source_id: 'chosen', title: 'Dune — EPUB edition',
    format: 'epub', size: 2000, language: 'en', publisher: 'Example Press',
    publish_year: 2024, extra: { language: 'en', publisher: 'Example Press' },
  },
];

async function mockMe(page: Page, opts: {
  feature?: boolean; access?: boolean; auto?: boolean; admin?: boolean;
} = {}) {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      id: 7, name: 'Store Tester', locale: 'en', theme: 'dark',
      role: {
        admin: opts.admin ?? false,
        anonymous: false,
        viewer: true,
        store_access: opts.access ?? true,
        store_auto_approve: opts.auto ?? false,
      },
      features: {
        hide_books: false, mail_configured: false, public_registration: false,
        anon_browse: false, kobo_sync: false, uploading: true,
        store_discover: opts.feature ?? true,
      },
      sidebar: {}, sidebar_order: [], instance_name: 'Test Library',
      display: { books_per_page: 24, random_books: 4 },
      catalog: { default_filter: null },
    }),
  }));
}

async function mockStoreReads(page: Page, active: unknown = { downloads: [] }) {
  await page.route('**/api/v1/store/sources', (route) => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify([
      { name: 'direct_download', display_name: 'Direct download', enabled: true },
      { name: 'prowlarr', display_name: 'Prowlarr', enabled: true },
      { name: 'disabled_source', display_name: 'Disabled', enabled: false },
    ]),
  }));
  await page.route('**/api/v1/store/active', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify(active),
  }));
  await page.route('**/api/v1/store/credentials', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ items: [] }),
  }));
  await page.route('**/api/v1/store/requests', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ requests: [] }),
  }));
  await page.route(/\/api\/v1\/store$/, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      enabled: true, auto_approve: false,
      credential_providers: [{ key: 'annas_archive', label: "Anna's Archive" }],
    }),
  }));
}

async function reachReleases(page: Page) {
  await page.getByRole('search').getByRole('searchbox', { name: 'Search the Store' }).fill('Dune Frank Herbert');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await page.getByRole('button', { name: 'Choose Dune', exact: true }).click();
  await expect(page.getByText('Dune — EPUB edition')).toBeVisible();
}

test('Store stays entirely dark unless both the experiment and access role are on', async ({ page }) => {
  await mockMe(page, { feature: false, access: true });
  let storeCalls = 0;
  await page.route('**/api/v1/store**', (route) => { storeCalls += 1; return route.abort(); });

  await page.goto('/app');
  await expect(page.getByRole('navigation').getByRole('link', { name: 'Store' })).toHaveCount(0);
  await page.goto('/app/store');
  await expect(page.getByTestId('store-page')).toHaveCount(0);
  expect(storeCalls).toBe(0);
});

test('Store also stays dark when the experiment is on but the access role is off', async ({ page }) => {
  await mockMe(page, { feature: true, access: false });
  await page.goto('/app/store');
  await expect(page.getByTestId('store-page')).toHaveCount(0);
  await expect(page.getByRole('navigation').getByRole('link', { name: 'Store' })).toHaveCount(0);
});

test('requires an explicit work and explicit release, then submits their stable identities', async ({ page }) => {
  await mockMe(page);
  await mockStoreReads(page);
  let releaseCalls = 0;
  let acquireBody: Record<string, any> | null = null;
  await page.route('**/api/v1/store/search?*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ books: WORKS }),
  }));
  await page.route('**/api/v1/store/releases?*', (route) => {
    releaseCalls += 1;
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ releases: RELEASES, sources_searched: ['direct_download'] }),
    });
  });
  await page.route('**/api/v1/store/acquire', async (route) => {
    acquireBody = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ mode: 'request', status: 'queued' }) });
  });

  await page.goto('/app/store');
  await page.getByRole('search').getByRole('searchbox', { name: 'Search the Store' }).fill('Dune Frank Herbert');
  await page.getByRole('button', { name: 'Search', exact: true }).click();

  await expect(page.getByRole('button', { name: 'Choose Children of Dune' })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByRole('button', { name: 'Choose Dune', exact: true })).toHaveAttribute('aria-pressed', 'false');
  expect(releaseCalls).toBe(0);

  await page.getByRole('button', { name: 'Choose Dune', exact: true }).click();
  expect(releaseCalls).toBe(1);
  const request = page.getByRole('button', { name: 'Request', exact: true });
  await expect(request).toBeDisabled();
  await expect(page.getByRole('button', { name: /Dune — EPUB edition/ })).toHaveAttribute('aria-pressed', 'false');

  await page.getByRole('button', { name: /Dune — EPUB edition/ }).click();
  await expect(request).toBeEnabled();
  await request.click();
  await expect(page.getByTestId('store-acquire-status')).toContainText('Request sent for approval.');

  expect(acquireBody).toMatchObject({
    work: { provider: 'annas_archive', provider_id: 'dune' },
    release: {
      provider: 'annas_archive', book_id: 'dune', source: 'direct_download',
      source_id: 'chosen', title: 'Dune — EPUB edition', format: 'epub', size: 2000,
    },
  });
  await expect(page.getByRole('button', { name: 'Prowlarr' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Disabled' })).toHaveCount(0);
});

test('changing work clears the selected release and ignores delayed old-work releases', async ({ page }) => {
  await mockMe(page);
  await mockStoreReads(page);
  await page.route('**/api/v1/store/search?*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ books: WORKS }),
  }));
  await page.route('**/api/v1/store/releases?*', async (route) => {
    const url = new URL(route.request().url());
    const id = url.searchParams.get('book_id');
    if (id === 'children') await new Promise((resolve) => setTimeout(resolve, 400));
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ releases: id === 'dune' ? RELEASES : [{ ...RELEASES[0], source_id: 'children-release', title: 'Children edition' }] }),
    });
  });

  await page.goto('/app/store');
  await reachReleases(page);
  await page.getByRole('button', { name: /Dune — EPUB edition/ }).click();
  await expect(page.getByRole('button', { name: 'Request', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: 'Choose Children of Dune' }).click();
  await expect(page.getByRole('button', { name: 'Request', exact: true })).toBeDisabled();
  await expect(page.getByText('Children edition')).toBeVisible();
});

test('the exact duplicate 500 is benign and active progress remains live', async ({ page }) => {
  await mockMe(page, { auto: true });
  await mockStoreReads(page, {
    downloads: [{ book_id: 'download-1', title: 'Dune', status: 'downloading', progress: 42, format: 'epub' }],
  });
  await page.route(/\/api\/v1\/store$/, (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      enabled: true, auto_approve: true,
      credential_providers: [{ key: 'annas_archive', label: "Anna's Archive" }],
    }),
  }));
  await page.route('**/api/v1/store/search?*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ books: WORKS }),
  }));
  await page.route('**/api/v1/store/releases?*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ releases: RELEASES }),
  }));
  await page.route('**/api/v1/store/acquire', (route) => route.fulfill({
    status: 500, contentType: 'application/json',
    body: JSON.stringify({ error: 'Release is already in the download queue' }),
  }));

  await page.goto('/app/store');
  await reachReleases(page);
  await page.getByRole('button', { name: /Dune — EPUB edition/ }).click();
  await page.getByRole('button', { name: 'Download', exact: true }).click();
  await expect(page.getByTestId('store-acquire-status')).toContainText('Already queued.');
  await expect(page.getByText('Already queued.')).not.toHaveAttribute('role', 'alert');
  await expect(page.getByRole('progressbar', { name: 'Download progress' })).toHaveAttribute('aria-valuenow', '42');
});

test('a generic active-row id never becomes a cancel or retry action', async ({ page }) => {
  await mockMe(page, { auto: true });
  await mockStoreReads(page, {
    downloads: [{ id: 'queue-row', title: 'Queued edition', status: 'downloading', progress: 12 }],
  });
  await page.goto('/app/store');
  await expect(page.getByText('Queued edition')).toBeVisible();
  await expect(page.getByRole('button', { name: /Cancel Queued edition|Retry Queued edition/ })).toHaveCount(0);
});

test('credential plaintext leaves the controlled input after the immediate write and never returns from GET', async ({ page }) => {
  await mockMe(page);
  await mockStoreReads(page);
  const secret = 'test-only-provider-value';
  let posted = '';
  await page.route('**/api/v1/store/credentials/annas_archive', async (route) => {
    posted = route.request().postDataJSON().credential;
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({ provider: 'annas_archive', configured: true, last4: 'alue', updated_at: '2026-08-08T00:00:00Z' }),
    });
  });

  await page.goto('/app/store');
  const input = page.getByLabel("Anna's Archive");
  await input.fill(secret);
  await input.locator('..').getByRole('button', { name: 'Save', exact: true }).click();
  await expect(input).toHaveValue('');
  expect(posted).toBe(secret);
  await expect(page.locator('body')).not.toContainText(secret);
});

test('Store work/release state has no serious or critical axe violations', async ({ page }) => {
  await mockMe(page);
  await mockStoreReads(page);
  await page.route('**/api/v1/store/search?*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ books: WORKS }),
  }));
  await page.route('**/api/v1/store/releases?*', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ releases: RELEASES }),
  }));
  await page.goto('/app/store');
  await reachReleases(page);
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  expect(results.violations.filter((violation) => ['critical', 'serious'].includes(violation.impact || ''))).toEqual([]);
  const width = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(width).toBeLessThanOrEqual(1);
});

test('admin can review requests without Store access and can only revoke provider names', async ({ page }) => {
  await mockMe(page, { admin: true, access: false, feature: true });
  let approved = false;
  let revoked = false;
  await page.route('**/api/v1/admin/users', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{
      id: 8, name: 'Reader', email: '', kindle_mail: '', locale: 'en', default_language: 'all', is_guest: false,
      roles: { viewer: true, store_access: true, store_auto_approve: false },
      store_credential_providers: ['annas_archive'],
    }] }),
  }));
  await page.route('**/api/v1/admin/experimental', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ items: [{
      key: 'store_discover', name: 'Store / Discover', description: 'Search external acquisition sources through a configured Shelfmark service.',
      default: false, dev_only: true, enabled: true,
    }] }),
  }));
  await page.route('**/api/v1/store/admin/requests', (route) => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({ requests: [{
      id: 31, requester: { id: 8, name: 'Reader' }, work: WORKS[1], release: RELEASES[1], status: 'pending',
    }] }),
  }));
  await page.route('**/api/v1/store/admin/requests/31/fulfil', async (route) => {
    approved = true;
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });
  await page.route('**/api/v1/admin/store/credentials/8/annas_archive', async (route) => {
    revoked = true;
    await route.fulfill({ status: 204, body: '' });
  });

  await page.goto('/app/admin');
  await expect(page.getByRole('heading', { name: 'Store requests' })).toBeVisible();
  await expect(page.getByText('Requested by Reader')).toBeVisible();
  await expect(page.getByLabel('Store access')).toBeChecked();
  await expect(page.getByLabel('Store downloads without approval')).not.toBeChecked();
  await page.getByRole('button', { name: 'Approve' }).click();
  await expect.poll(() => approved).toBe(true);
  page.once('dialog', (dialog) => dialog.accept());
  await page.getByRole('button', { name: 'Revoke annas_archive' }).click();
  await expect.poll(() => revoked).toBe(true);
  await expect(page.getByText(/can never reveal or copy/)).toBeVisible();
});
