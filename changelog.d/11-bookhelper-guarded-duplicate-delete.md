### Added

- **Guarded duplicate deletion for Bookhelper:** authenticated source and keeper UUID/token preflight, all-user reading/shelf/annotation reference rejection, and two-database SQLite writer exclusion through book removal. The complete original book directory is staged into CWA-owned persistent `maintenance/potentialDupes/` with per-file SHA-256 verification and a restart-safe journal, never hard-deleted. Only unreferenced books can be quarantined.

### Fixed

- **Concurrent Bookhelper tag changes:** tags-only add/remove now recalculates membership under a fresh metadata writer transaction, preserving unrelated tags added by another editor.
- **Bookhelper restore imports:** recognize exact undo provenance filenames and preserve stable identities against reused numeric Calibre IDs.

- **Quarantine recovery:** authenticated bounded reconciliation after database-commit interruptions, collision/path/ownership checks and an explicit transaction ID returned to Bookhelper's reason ledger.
