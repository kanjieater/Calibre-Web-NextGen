# Bookhelper duplicate quarantine — DramaCD-style release contract

**Status:** required design and acceptance criteria for PR #11; **not implemented
at this head**. Do not merge the current hard-delete path as the first
automatic duplicate-removal release.

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

The guarded NextGen route must replace the current post-commit
`helper.delete_book(..., book_format="")` **permanent file delete** with a
crash-resumable, application-owned quarantine of the *complete* original
Calibre book directory, including EPUBs, alternate formats, cover, OPF and
unrecognized files:

1. Validate canonical paths and required persistent quarantine mount. Reject
   symlinks, traversal, collisions, duplicate ownership or unsupported
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
   Commit the removal of the active Calibre record. Only *after this commit*,
   remove the now-orphaned original path when full quarantined bytes have been
   verified and the journal proves ownership. A crash in between preserves the
   bytes, and recovery finishes the owned step without a second delete.
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

## Release and confidence ladder

- **First full-library scan:** inspection/report only; compute verified counts,
  raw-EPUB-identical groups, equivalent-but-repackaged groups, blockers, expected
  bytes to quarantine, and source IDs. No actual removal.
- **Initial approved small batch:** human reviews plan SHA and executes a
  bounded transaction batch. Compare removed-ID counts, intact quarantine
  directories, hash inventory, active survivor metadata and undo. Use this to
  validate against a representative cwa-scale snapshot, not only mock HTTP.
- **Automatic maintenance:** once a reviewed deployment proves the cutover and
  quarantine API, the existing Bookhelper scheduled pass automatically
  quarantines only the strict **raw-SHA-256 + compatible identity** tier. It
  never needs a per-batch opt-in switch, but rechecks per-book safety live.
- **Expansion:** only after an auditable history of successful operations and
  exercised rollback may equivalent-text/different-ZIP cases be considered
  for automatic quarantine. Ambiguous or explicitly different editions
  remain visible and untouched.

Required tests: transaction journal before/after each file and DB step,
crash after quarantine publication before DB commit, crash after DB commit
before source cleanup, cross-mount partial copy, hash mismatch, path/symlink
attacks, destination collision, duplicate retry, retained unrelated book,
concurrent app.db user writer, changed source/keeper UUID and end-to-end
restart/restore with Book-sync stopped. Tests must use actual NextGen
app.db + metadata.db, a temporary library, and CWA's production image as
appropriate; a fake-CWA 204 response is not proof of recovery.
