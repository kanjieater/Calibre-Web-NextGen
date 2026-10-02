# Custom-column sorting and display

Administrators can enable existing scalar integer, decimal and date custom
columns under **Admin → UI Configuration → Custom columns → Sort by**. Create
these columns in Calibre first. Text, enumeration and multiple-value columns
are excluded because this feature needs one numeric or date value per book.
No columns are enabled by default. The existing custom-column ignore expression
takes precedence: hidden fields are excluded from sorting and list responses.
Hiding a field does not erase the administrator's enabled selection; removing the
ignore expression makes that enabled field available again.

An enabled column adds ascending and descending choices to compatible library,
search, category and global-library lists. The New UI also keeps those choices
when a saved advanced filter is applied. Table headers can sort the corresponding
custom field. Classic catalog and table views use the same validated column IDs.
Missing values appear last in both directions, and book IDs break ties so paging
has a stable order. Discover, Hot and manually ordered shelves retain their
specialized ordering.

The New UI shows enabled fields on book cards and in its table. Open the library's
**View settings → Custom fields on book cards** to hide fields or give them a
personal display name. An empty display name restores the administrator's name.
Signed-in readers save these preferences to their own account; they apply to
other New UI cards and table columns. Guest field selections and display labels stay in that
browser and apply to compatible New UI cards and table columns. Display labels are plain text and limited to 80 characters.

Removing or changing an enabled column makes a stale sort fall back to a built-in
order. Removed fields are pruned from the next display-preference save, so a
reader can keep changing the remaining fields. A temporary failure to read column definitions uses that safe order
without erasing a reader's saved choice, so it can resume when the library is
available again. This feature reads existing Calibre values; it does not change
the column definitions or book metadata.

This extends [@kanjieater's contribution](https://github.com/new-usemame/Calibre-Web-NextGen/pull/2115)
and the existing Magic Shelf custom-column resolver.

Temporarily hiding an enabled field with the ignore expression also preserves a
reader's existing display selection and label while other visible fields are
edited. Hidden fields cannot be submitted as new selections. Removing or
unconfiguring a field discards its saved choice. Once a reader has saved an
explicit selection, newly enabled fields require that reader to select them;
they do not appear automatically.

Field saves are bound to the account that initiated the edit. A queued save is
canceled when the app's account has changed, and the server rejects a request
whose expected account differs from the authenticated session. This keeps an
older edit from changing a later signed-in account.

Each signed-in save carries the visible field IDs that the page knew about. Changes apply within that scope; previously selected live fields outside it keep their labels and selection when an administrator restores them during an open page. A newly hidden or removed field can cause a stale save to be refused; the UI refreshes current definitions so the reader can retry. An account-change refusal refreshes the current account too. Label edits save when their input loses focus, including when closing the settings panel; Escape closes the panel and returns focus when focus was inside it. The panel stays near its button and is clamped within the viewport.
