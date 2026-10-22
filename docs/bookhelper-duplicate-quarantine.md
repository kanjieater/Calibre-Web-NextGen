# Bookhelper duplicate quarantine — DramaCD-style release contract

**Status:** filesystem quarantine and guarded-delete implementation are on
PR #11. **Draft and unverified in the real deployment**: full production-image
transaction/race/restart/restore tests and the separate one-time Book-sync
legacy-writer retirement are still required before merging or deployment.
The guarded duplicate route never falls back to permanent file deletion.

## Scope and ownership

Copy the *proven pattern* from `kanjieater/dramacd`:
`src/audio_fingerprint.rs` uses exact compressed-audio payload hashes plus
independent release-identity contradictions;
`src/ingest_txn.rs` moves confirmed duplicates intact into
`maintenance/potentialDupes/` with a persistent transaction journal, private
staging name, ownership marker, collision refusal, cross-mount copy fallback,
and restart recovery. A confirmed duplicate is **not automatically destroyed**.

For ebooks, Bookhelper makes the candidate/keeper decision and writes the
human review plan, SHA-256 evidence, survivor preservation receipt and old-ID
alias. NextGen alone owns **both** the Calibre database mutation and the
filesystem quarantine. Neither service contacts Book-sync at runtime, and
Bookhelper never mounts or moves files in the Calibre library.

Use an application-owned persistent `maintenance/potentialDupes/` destination,
*outside* the active Calibre library. It need not be called `.recycle`, and
must not be placed where CWA/cwa-scale might index it as a normal ebook.

## First automatic removal tier

For a conservative first release, auto-quarantine only when all checks pass:

1. Source and survivor represent the same work, author, **volume and edition**,
   with no explicit metadata/ISBN/OPF-identifier contradiction. The known-
   different-identity veto applies even when file hashes happen to match,
   as in DramaCD.
2. The exact EPUB archive **raw SHA-256 matches** the keeper's EPUB archive.
   No auto-removal from normalized spine text alone in the first tier.
   Bookhelper already captures `bytes_sha256` and normalized text/media
   evidence; distinguish these in the machine-readable plan and ledger.
3. Every extra source format, unique tag/identifier/cover and eligible manual
   metadata field is preserved on the survivor, verified before source removal.
   Any untransferable user-owned data or personal reading/shelf/annotation
   reference blocks removal. Existing NextGen all-user and UUID checks remain
   mandatory.
4. Live CWA preflight and the fresh recheck happen under the actual guarded
   SQLite writer reservations. Both source **and keeper UUID** are validated.
   A stale token or uncertain write does not authorize quarantine.
5. The quarantine transaction has durable evidence that the *entire original
   source book directory* is recoverable, not merely that Bookhelper saved one
   EPUB or a format subset.

Content-equivalent but different ZIP bytes may still be identified as
`confirmed_candidate` and preserved non-destructively. Promotion of that
broader tier to automatic quarantine needs independent illustration, embedded
identity, missing-extra-content and restart/undo parity tests and an explicit
owner-reviewed policy revision. No title-only/fuzzy auto-removal.

## Quarantine transaction and reconciliation

The guarded NextGen route replaces the earlier post-commit
`helper.delete_book(..., book_format="")` **permanent file delete** with a
crash-resumable, application-owned quarantine of the *complete* original
Calibre book directory, including EPUBs, alternate formats, cover, OPF and
unrecognized files:

1. Validate canonical paths and required **pre-created writable persistent**
   quarantine directory/mount. Missing or symlinked destinations must fail
   closed; never create a fallback inside ephemeral container storage. Reject
   traversal, collisions, duplicate ownership or unsupported
   backend (including Google Drive) rather than silently hard-deleting.
2. Write/fsync a journal with transaction ID, exact source/keeper UUIDs and
   IDs, expected evidence digest, original relative path, chosen unique
   quarantine destination and full per-file hashes. A staging directory
   must never be mistaken for a published quarantined book.
3. Publish a complete verified quarantine copy/rename with a transaction-owned
   marker **before committing deletion of the Calibre row**. Cross-mount copy
   must use a hidden staging dir, verify file hashes and atomically rename the
   stage; do not remove the original yet. A failure leaves the live book
   untouched and the journal resumable.
4. Revalidate identity/reference state with both database writer slots held.
   Commit removal of the active Calibre record, then **reacquire the Calibre
   SQLite writer reservation**, verify neither the original numeric ID nor
   original directory path has been reused, and hold the reservation until
   safe orphan cleanup finishes. The complete original bytes must already
   exist in verified quarantine. A race or crash preserves the quarantine
   and leaves cleanup pending, never deleting a newly reoccupied path.
5. Keep a small durable transaction receipt and the published quarantined
   directory; reconcile `submitted`, `published`, `db_committed`,
   `source_cleanup_pending` and `complete`. HTTP success only means
   source removal was committed and the original is durably quarantined.
   Never infer that a timeout is a successful or retryable destructive write.
6. Preserve the files indefinitely at first. Do not introduce unattended
   retention/purge; a later independent owner decision may set retention only
   after long-running restore evidence.

A rollback/undo API or Bookhelper-controlled import must consume the
quarantined **original** bytes, revalidate every hash and metadata snapshot,
and either restore a valid book or explicitly return a recoverable failure.
The original Calibre numeric ID may be unavailable: Bookhelper's paired
UUID-qualified aliases remain the identity bridge.

Keep a *single purpose-specific* quarantine journal and Bookhelper's existing
receipt. Do not add Redis, a new database, a shared Book-sync coordinator, or
a generic retention framework.

## Automatic release, evidence and recovery contract

The owner does **not** require an initial manual dry run or per-batch
approval. Once the NextGen quarantine implementation and Bookhelper writer
ownership cutover have passed engineering tests, the **default existing
Bookhelper maintenance loop** automatically processes strict raw-EPUB-hash,
identity-compatible duplicate groups without further user actions.

Quarantine is **not permanent deletion**: the redundant row disappears from
CWA's active catalog while its complete source directory remains in the
separate persistent `maintenance/potentialDupes/` destination.

NextGen receives one verified pair at a time from Bookhelper and:
- Reserves both CWA/app.db and Calibre metadata.db SQLite writer slots and
  rechecks source UUID, keeper UUID, user references and token.
- Stages a complete original-directory copy on the destination filesystem,
  fsyncs a source/keeper UUID-qualified journal and content hashes, and
  publishes with an owned marker using atomic rename before the Calibre
  delete commit.
- Removes the Calibre row with no protected user-state purge, then clears the
  orphan original only after confirming the published bytes. There is no
  `helper.delete_book` permanent-cleanup fallback in the guarded route.
- Returns an explicit quarantine transaction ID on confirmed success. For an
  interrupted commit/cleanup, a bounded authenticated reconcile endpoint
  checks the actual Calibre database for a live owner of *either* the original
  numeric ID or directory path and finishes cleanup only when neither is in
  use. A separate authenticated read-only quarantine-status endpoint verifies
  the full original directory hashes, source/keeper UUID, operation ID and
  committed journal state. Bookhelper requires this independent attestation
  before resolving an uncertain HTTP delete as successful. A missing CWA book
  row alone is never sufficient proof.
- Blocks and records unsupported Google Drive libraries, missing persistent
  quarantine mount, reserved path/traversal, symlink, corrupted file, hash
  mismatch, changed source or keeper identity, current user references,
  collision or ambiguous replay.

Bookhelper's existing durable receipt records **why** it chose that source:
stable decision rule/version/reason code, date, both IDs/UUIDs, exact raw
EPUB hashes, identity/edition evidence, all preserved data, CWA guard result,
quarantine transaction ID and undo state. NextGen's private journal holds
only physical ownership/path/full file inventory and cleanup state. Inspect
both when a suspect match is found. `bookhelper duplicates pause` stops
subsequent quarantines immediately without disabling matching/preservation.
`history --receipt` shows the full reason; `undo --receipt` restores the
original hash-verified EPUB and metadata through CWA. A ZFS snapshot remains
the broader recovery fallback.

No automatic purge/retention window in this initial release. Same-text,
different-ZIP EPUBs remain review-only even if normalized spine hashes
match. Explicitly different volume, work, edition or ISBN always vetoes
automatic quarantine, regardless of file hash.

**Acceptance tests (before deploy, not a manual operator approval gate):**
- Actual NextGen/Bookhelper production-container integration with Book-sync
  stopped, exact-hash quarantine, reason ledger and restored aliases.
- Two actual SQLite DB connections plus a concurrent writer racing delete.
- Source+keeper UUID recycle, user annotation/shelf/member references,
  pending/failed/uncertain API calls, crash between staging/DB commit/cleanup,
  cross-mount partial copies, symlink/path collision and restart reconciliation.
- Per-file full-directory SHA-256 validation; no recovered book is called
  verified unless original EPUB ZIP bytes and metadata match its receipt.
- Confirm `BOOKHELPER_DUPLICATE_QUARANTINE_DIR` points to a persistent
  writable mount outside the active Calibre root. Fail closed if absent.

## Current implementation caveat

These requirements are implemented in the PR's new
`cps/duplicate_quarantine.py` helper, the guarded endpoint and direct
filesystem tests, but **the current branch still needs a passing complete
test run and real-image integration before merge/deploy.** Do not mistake
a fake CWA's success response for a production-grade commit.
