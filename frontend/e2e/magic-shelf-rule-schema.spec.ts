import { test, expect } from '@playwright/test';
import { collectPageErrors, assertNoPageErrors } from './utils';

type RuleField = { id: string; label: string; operators: string[] };
type RuleSchema = {
  fields: RuleField[];
  operators: { type: string; label?: string }[];
};

const DATE_FIELDS = ['pubdate', 'timestamp'];

async function selectOptions(select: import('@playwright/test').Locator) {
  return select.locator('option').evaluateAll((options) =>
    options.map((option) => ({ value: (option as HTMLOptionElement).value, text: option.textContent?.trim() || '' })),
  );
}

test('Classic and New UI consume the canonical rule schema and date rules preview', async ({ page, request }) => {
  const errors = collectPageErrors(page);
  const schemaResponse = await request.get('/api/v1/magicshelves/rule-schema');
  expect(schemaResponse.status()).toBe(200);
  const schema = await schemaResponse.json() as RuleSchema;
  const expectedFieldIds = schema.fields.map(({ id }) => id);

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
  expect(classicFields.map(({ value }) => value)).toEqual(expectedFieldIds);
  expect(classicFields.every(({ text }) => text.length > 0)).toBe(true);

  for (const fieldId of DATE_FIELDS) {
    await classicFilter.selectOption(fieldId);
    const classicOperator = page.locator('#builder .rule-operator-container select').first();
    const classicOperatorValues = (await selectOptions(classicOperator)).map(({ value }) => value);
    expect(classicOperatorValues).toContain('in_last_days');
    expect(classicOperatorValues).toContain('not_in_last_days');
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
  await expect(page.locator('main#main h1')).toBeVisible();
  const spaField = page.locator('main#main select:has(option[value="pubdate"])').first();
  const spaOperator = page.locator('main#main select:has(option[value="in_last_days"])').first();
  const spaFields = await selectOptions(spaField);
  expect(spaFields.map(({ value }) => value)).toEqual(expectedFieldIds);
  expect(spaFields.every(({ text }) => text.length > 0)).toBe(true);

  for (const fieldId of DATE_FIELDS) {
    await spaField.selectOption(fieldId);
    await spaOperator.selectOption('in_last_days');
    const previewResponse = page.waitForResponse((response) =>
      response.url().includes('/magicshelf/preview') && response.request().method() === 'POST',
    );
    await page.locator('main#main input[type="number"]').first().fill('30');
    const response = await previewResponse;
    expect(response.status()).toBe(200);
    const payload = await response.json() as { success: boolean; count: number };
    expect(payload.success).toBe(true);
    expect(payload.count).toBeGreaterThanOrEqual(0);
  }

  assertNoPageErrors(errors);
});
