import { test, expect } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

type RuleField = { id: string; label: string; operators: string[] };
type RuleSchema = {
  fields: RuleField[];
  operators: { type: string; label?: string }[];
};

const DATE_FIELDS = ['pubdate', 'timestamp'];

async function selectOptions(page: import('@playwright/test').Page, label: string) {
  return page.getByLabel(label).locator('option').evaluateAll((options) =>
    options.map((option) => ({ value: (option as HTMLOptionElement).value, text: option.textContent?.trim() || '' })),
  );
}

test('Classic and New UI consume the canonical rule schema and date rules preview', async ({ page, request }) => {
  const errors = collectPageErrors(page);
  const schemaResponse = await request.get('/api/v1/magicshelves/rule-schema');
  expect(schemaResponse.status()).toBe(200);
  const schema = await schemaResponse.json() as RuleSchema;
  const expectedFields = schema.fields.map(({ id, label }) => ({ value: id, text: label }));

  for (const fieldId of DATE_FIELDS) {
    const field = schema.fields.find((item) => item.id === fieldId);
    expect(field, `${fieldId} missing from canonical schema`).toBeTruthy();
    expect(field!.operators).toContain('in_last_days');
  }

  await page.goto('/magicshelf');
  const classicFilter = page.locator('#builder .rule-filter-container select').first();
  const classicFields = (await classicFilter.locator('option').evaluateAll((options) =>
    options.map((option) => ({ value: (option as HTMLOptionElement).value, text: option.textContent?.trim() || '' })),
  )).filter((option) => option.value !== '-1');
  expect(classicFields).toEqual(expectedFields);

  for (const fieldId of DATE_FIELDS) {
    await classicFilter.selectOption(fieldId);
    const classicOperator = page.locator('#builder .rule-operator-container select').first();
    await expect(classicOperator).toContainText('In the past N days');
    await expect(classicOperator).toContainText('Not in the past N days');
    await classicOperator.selectOption('in_last_days');
    await page.locator('#builder .rule-value-container input').first().fill('30');
    const previewResponse = page.waitForResponse((response) =>
      response.url().includes('/magicshelf/preview') && response.request().method() === 'POST',
    );
    await page.locator('#preview-btn').click();
    const response = await previewResponse;
    expect(response.status()).toBe(200);
    const payload = await response.json() as { success: boolean; count: number };
    expect(payload.success).toBe(true);
    expect(payload.count).toBeGreaterThanOrEqual(0);
  }

  await page.goto('/app/magic');
  await expect(page.getByRole('heading', { name: 'New smart shelf' })).toBeVisible();
  expect(await selectOptions(page, 'Rule field')).toEqual(expectedFields);

  for (const fieldId of DATE_FIELDS) {
    await page.getByLabel('Rule field').selectOption(fieldId);
    await page.getByLabel('Rule operator').selectOption('in_last_days');
    const previewResponse = page.waitForResponse((response) =>
      response.url().includes('/magicshelf/preview') && response.request().method() === 'POST',
    );
    await page.getByPlaceholder('value').fill('30');
    const response = await previewResponse;
    expect(response.status()).toBe(200);
    const payload = await response.json() as { success: boolean; count: number };
    expect(payload.success).toBe(true);
    expect(payload.count).toBeGreaterThanOrEqual(0);
  }

  assertNoPageErrors(errors);
});
