### Added

- **Guarded duplicate deletion for Bookhelper:** authenticated source and keeper UUID/token preflight, all-user reading/shelf/annotation reference rejection, and two-database SQLite writer exclusion through the supported book deletion; original EPUB recovery remains owned by Bookhelper. Only unreferenced books can be removed.

### Fixed

- **Concurrent Bookhelper tag changes:** tags-only add/remove now recalculates membership under a fresh metadata writer transaction, preserving unrelated tags added by another editor.
- **Bookhelper restore imports:** recognize exact undo provenance filenames and preserve stable identities against reused numeric Calibre IDs.
