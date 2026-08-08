# Mission: native Store / Discover backed by Shelfmark

Updated: 2026-08-08  Phase: handoff complete  Status: 10/10 outcomes done and verified

## Definition of done

- [x] Local secret/database files are ignored and the fix is committed without reading or staging `.key`.
- [x] A general default-off experimental-feature registry persists in `cwa_settings`; Store/Discover is `dev_only` and invisible in a default install.
- [x] Store access and auto-approval are independent role bits, default off, enforced server-side, and editable in the existing admin user UI.
- [x] Per-user provider credentials use AES-256-GCM with versioned keys, write-only APIs, revoke-without-read administration, secure generated key storage, and logging-filter redaction.
- [x] A server-side Shelfmark adapter covers metadata search, release selection, enabled sources, queue/status/cancel/retry, and request/admin approval APIs with empty example configuration.
- [x] The native Discover flow requires explicit work selection and explicit edition/release selection; no code path auto-picks the first result.
- [x] Duplicate queue responses are presented as benign already-queued state, active downloads show live progress, and enabled sources are surfaced.
- [x] Role-aware request/approval behavior works: no access means no tab/API access; non-auto-approved users request; auto-approved users queue directly; admins review and fulfil/reject.
- [x] Backend, frontend, migration, crypto, permission, negative, concurrency, default-off, restart, i18n, accessibility, and production-build gates pass.
- [x] The feature branch is logically committed with the required identity, pushed only to its feature branch for remote handoff, and the final report labels all evidence OBSERVED or ASSUMED per item.

## Now / next action

Handoff the committed `feature/store-discover` branch and report per item with observed versus assumed evidence.

## How to build/run/verify

- Backend focused tests: `pytest -q <targeted test files>`
- Backend full unit suite: `pytest -q tests/unit`
- Frontend tests/build: `cd frontend && npm test -- --run && npm run build`
- SPA e2e/a11y: `cd frontend && npm run test:e2e -- a11y` plus feature specs on desktop/mobile.
- Real dev stack: deploy only CWNG changes to the operator-provided capped transcoder stack; use authenticated HTTP and browser checks without changing caps or unrelated containers.
- Migration resilience: run against populated SQLite state, restart, and confirm idempotence/default-off behavior.

## Decisions & rationale

- 2026-08-08: operator-approved brief is the spec; its live-verified Shelfmark behavior is not re-researched.
- 2026-08-08: use Shelfmark solely as the acquisition engine; CWNG owns UI, authorization, credential custody, and API mediation.
- 2026-08-08: use the existing role bitmask and `cwa_settings` migration conventions; do not create parallel permission/config systems.
- 2026-08-08: use CWNG-consume-briefing, CWNG-git-manager, ALEX-DEV-OPUS-run-to-done, ALEX-DEV-capabilities, ALEX-ORCHESTRATE-model-routing, CWNG_a11y, and later the SSH/browser skills for live verification.
- 2026-08-08: the MiniMax briefing is stale since 2026-06-12; the explicit operator task does not depend on its backlog ranking.
- 2026-08-08: Store credentials live in app.db so the requested foreign key can reference app.db's user table; SQLite cannot enforce a foreign key across app.db and cwa.db.
- 2026-08-08: the storefront route is `/store`; `/discover` already means random books from the installed library and must remain intact.
- 2026-08-08: remote work/release cards reuse the native shell, BookCover, tokens, query/status primitives, and catalog layout, but do not impersonate library Book records with fake Calibre IDs.
- 2026-08-08: Shelfmark's active list is deployment-wide, so CWNG keeps local ownership mappings, filters progress per user, and authorizes cancel/retry only from an explicit upstream `book_id`; generic queue IDs never become action authority.
- 2026-08-08: per-user provider-key transport remains fail-closed at HTTP 501 because the operator-verified Shelfmark contract does not define a header or payload field. No transport shape was invented.
- 2026-08-08: the isolated Shelfmark instance is in no-auth mode, where its request API returns a controlled 403. Request/approval UI and route behavior are observed in 19/19 browser tests, but a live Shelfmark request transition is not claimed.

## Verification record

- OBSERVED: focused Store/security backend suite 36/36 passed after adversarial review remediation.
- OBSERVED: production TypeScript/Vite build passed; 1,889 modules transformed.
- OBSERVED: Store Playwright suite 19/19 passed on the freshly served dev-stack bundle across desktop and mobile, including axe serious/critical checks and no horizontal overflow.
- OBSERVED: unmocked live UI returned 39 work candidates and 43 releases, selected neither by default, kept Download disabled until an explicit release click, then enabled it; mobile overflow was zero.
- OBSERVED: default live migration was dark (404), both roles were false, the generated key was `0600`, all Store tables existed, enable/role APIs worked, and the stack remained healthy at its original 1.5 CPU/1.5 GB caps. The flag and roles were restored off afterward.
- OBSERVED: live credential write returned only the allowed projection; SQLite held non-plaintext ciphertext, a 12-byte nonce, key version 1, and correct last four; the plaintext was absent from the file log; revoke returned 204.
- OBSERVED: security re-review cleared all high/medium findings after scoped plaintext redaction, user-owned active filtering/actions, strict actionable IDs, response caps/shape checks, credential race recovery, and SQLite rollback/retry fixes.
- OBSERVED: final broad unit suite had 5,664 passed, 90 skipped, and one unrelated failure in `test_ingest_batch_dirty` because the local host cannot open hardcoded read-only `/config/cwa.db`; the same failure reproduced before and after the Store delta.
- ASSUMED/limited: Shelfmark's external queue side effect and CWNG's ownership commit cannot be atomic across systems. Bounded retry/rollback and an honest tracking-failed 503 cover SQLite failures, but a process crash between systems remains possible.

## Open questions for the operator

- None. The build brief resolves the product decisions needed to proceed.
