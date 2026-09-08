import type { ListCustomColumnDefinition, Me } from './api';

/** Apply the reader's View-settings selection to a page's server-owned fields.
 * A missing selection is the first-run default: show every enabled field. */
export function selectedCustomColumns(
  definitions: ListCustomColumnDefinition[] | undefined,
  me: Me | null | undefined,
): ListCustomColumnDefinition[] {
  const fields = definitions ?? [];
  const selected = me?.catalog?.custom_field_ids;
  return Array.isArray(selected)
    ? fields.filter((field) => selected.includes(field.id))
    : fields;
}
