# K3 — Independent review: multi-device annotation slices 1+2, D1–D10 coverage

Reviewer: Kimi K3 (second opinion; did not write this code). Read-only pass, 2026-08-09.
Target: Sol's worktree, branch `design/multi-device-annotations`.
Slice 1: `d6cb55bfb..a4b95368f`. Slice 2: `a4b95368f..2f2139a31` (7 commits, incl. WIP
`bab38b417`). Scope: `cps/`, `tests/` only. Line numbers cite the tree at `2f2139a31`.
Claims below are OBSERVED (read in the extracted tree / both diffs) unless marked REASONED
(static trace, not executed — I ran no code per the read-only brief).

Headline: **no BLOCKER found in either slice.** The headline invariants the operator verified
hold up under the paths they didn't test. Four SHOULD-FIXes, all in places the test suite
structurally cannot see. Slice 2's API surface is solid; the D-coverage gap (D4) is the
finding that actually blocks the UI.

---

## Slice 1

### S1-A — SHOULD-FIX: the registry turns every authenticated Kobo request into a DB write on the hot path

`cps/services/device_registry.py:70-77` — on any request where `now >= device.last_seen_at`
(i.e. effectively every request), `upsert_kobo_device` issues an UPDATE + commit via
`register_kobo_device_best_effort` (`device_registry.py:88-104`). This runs inside
`requires_kobo_auth` (`cps/kobo_auth.py:171-175`) and
`requires_reading_services_auth_and_config` (`cps/readingservices.py:136-140`) — i.e. on
every sync, every annotation PATCH, every position sync, every cover fetch that carries
Kobo auth.

The failure path is genuinely contained (own session, own transaction, `rollback` + `close`
in `finally`, exception swallowed — OBSERVED). What is NOT contained is the *timing*: the
main engine is SQLite with `connect_args={'timeout': 30}` (`cps/ub.py:3246-3247`), so during
any concurrent long write (bulk bookmark ingest, library scan, a 559-row soft-delete) every
Kobo request now queues on the SQLite writer lock for up to seconds where the auth path was
previously read-only. A sync storm during a library write burst multiplies lock pressure in
both directions: the registry's per-request write also competes with the sync's own commits.

Failing scenario: operator runs a large library import while two devices sync; each device
request blocks on the registry's `last_seen` write behind the import's write transactions —
sync latency climbs from ~0ms overhead to multi-second stalls, devices retry, each retry
takes another write. Nothing breaks; everything slows, on exactly the path the brief marked
hottest. Cheap fix: dirty-check — skip the write when `last_seen_at` is younger than a few
minutes (last-seen granularity of "2 hours ago" needs no better).

### S1-B — SHOULD-FIX: the backfill proves the book UUID from the *filename*, never from the row's own book

`cps/services/annotation_content_id.py:62-82` (`normalize_content_id_for_backfill`) extracts
the legacy URI's filename stem and, if it parses as a UUID, adopts it as the book UUID.
`cps/ub.py:2771` selects only `id, content_id` — the row's `book_id` (and one JOIN to
`books.uuid`) is right there and is never consulted.

Failing scenario: a real stored row `file:///mnt/onboard/<uuid-X>.epub#(6)OEBPS/c.xhtml`
where `<uuid-X>` is a well-formed UUID that is NOT the annotation's book UUID (book
re-imported with a new calibre uuid after the device cached the file; a store-delivered file
named by content id). The backfill rewrites it to `<uuid-X>!!OEBPS/c.xhtml` — now
*canonical-shaped but keyed to the wrong book*. Nothing is lost or orphaned today (the only
consumer, `kobo_position.py:126`, uses just the chapter half — OBSERVED), but the row now
confidently carries a wrong key that any future uuid-keyed cross-device matcher will trust,
and the journal records the rewrite as deliberate. This is precisely a "row it cannot
confidently normalise" that the brief says must be LEFT ALONE — and it isn't. Fix is one
JOIN: skip rows whose stem-UUID ≠ the row's book UUID. Rows with human-named files (the
common legacy case) are already left alone — OBSERVED.

### S1-C — SHOULD-FIX: one client-controlled ingest path still writes `content_id` raw

`cps/services/annotation_portable.py:144-145` — `apply_portable` does
`if payload.get("content_id"): row.content_id = payload.get("content_id")`. No validation,
no normalization, no book-UUID check. Callers: the KOReader push
(`cps/progress_syncing/protocols/koreader_annotations.py:110`) and portable import.

So the API boundary is enforced on three of four write paths (webreader create →
`annotations.py:814-816`; Kobo PATCH upsert → `annotation_sync/__init__.py:170-180`; sqlite
ingest → `annotations.py:391-398` — all OBSERVED rejecting), but the fourth — the same
ingest family that created the two-shape mess — can still write an arbitrary string into the
column the migration just cleaned. The backfill is idempotent so it would mop up *on next
startup*, but between write and restart the row is live and unnormalized. Fix: run the value
through `normalize_content_id(..., book_uuid=book.uuid)` in `apply_portable` and reject the
field (not the annotation) on `ContentIdError`.

### S1-D — SHOULD-FIX: the equal-clock no-op also swallows legitimate retries and same-second edits

`cps/services/annotation_sync/__init__.py:197-201` — `client_time == stored` returns `None`
unconditionally, with a comment explaining ties can't be honestly broken without an actor
key. True for *conflicts*. But Kobo's `clientLastModifiedUtc` is second-precision
(`readingservices.py:340` types it as a plain string; observed formats are second-granular),
and the no-op doesn't check payload identity.

Failing scenario: user edits a note on device twice within one second (or the device retries
a PATCH after the first attempt's Hardcover fan-out failed transiently — the retry carries
the same clock). Second PATCH: `client_time == stored` → no-op. In case one, a real edit is
silently dropped server-side while the device believes it synced (it gets the proxied 200).
In case two, the fan-out retry never happens. The honest tie-break that requires no actor
key: apply when the incoming content fields differ from stored (a retry of identical content
is a genuine no-op; a same-second change is not).

### Q1 verdicts (the paths the wrapper test can't see)

- **Session poisoning: nothing found.** The registry never touches `ub.session`; it builds
  its own session on the shared engine and closes it in `finally` (`device_registry.py:88-104`
  — OBSERVED). A failed registry flush cannot dirty the request's session.
- **Module-load import errors: nothing found.** Both call sites place the import *inside*
  the try (`kobo_auth.py:172-174`, `readingservices.py:137-139`); an ImportError is caught
  like any other. Module top-level imports are stdlib + `sqlalchemy.orm` only.
- **Connection exhaustion: nothing found.** Session-per-call, closed in `finally`.
- **Slow write on hot path:** see S1-A — contained for correctness, open for timing.

### Q3 residual (defensive parsing)

Parsing itself: nothing found. Non-string/empty/naive/garbage/explicit-null all reject with
a warning; "Z" and offsets handled; no input I constructed crashes it or becomes now();
stored values are normalized to naive UTC before comparison (`annotation_sync/__init__.py:
190-194`) so no naive/aware throw. One observation: the "undated update" drop
(`annotation_sync/__init__.py:195`) is safe today only because the sole caller is the Kobo
PATCH path and Kobo's contract always sends the field — if a firmware ever omits it, that
annotation silently stops accepting local updates after its first clocked write. NIT at most;
worth a comment.

### Q4 verdict

A real allowlist — reject, don't sanitise — with exactly one hole: S1-C. `_chapter`
(`annotation_content_id.py:29-38`) rejects traversal, backslashes, control chars, absolute
paths; the only rewrite is the documented legacy→canonical conversion, gated on a proven
book UUID. NIT: the webreader rejection surfaces as `{"error": "bad_anchor"}`
(`annotations.py:1066-1068`) even when the anchor is fine and the content_id is the problem
— misleading client error. Also `content_id: ""` now hard-400s instead of being treated as
absent (`annotations.py:808-816`); the SPA reader never sends it, but it's a behaviour
change for a degenerate-but-harmless input.

### Q5 — the test that passes without the implementation

`test_registry_failure_does_not_break_reading_services_request`
(`tests/unit/test_multi_device_annotations_safe_slice.py:42-53`). Under a hook-only revert
(registry module present, call site removed) it still passes: it asserts the request
proceeds, which is trivially true when nothing is injected. It is **not vacuous in the
shipped tree** — it monkeypatches `device_registry.sessionmaker` to raise and pins the
swallow. Its blind spot is the mirror image: it proves containment of a failure, but cannot
detect the hook being deleted outright. Acceptable as a guard; name it for what it is.

### Q6 — device-visible behaviour

Nothing found in response bytes/status/headers. `handle_annotations` still always proxies
upstream (`readingservices.py:388`); GET untouched; the auth wrappers add only the wrapped
registry call. Two server-side behaviour shifts worth naming (neither returns different
bytes to the device): (1) a stale/equal-clock re-PATCH no longer un-hides a locally
soft-deleted annotation — previously any upsert hit `ann.hidden = False`
(`annotation_sync/__init__.py:205`); under LWW this is the intent, but it is a change in
what a device resync produces. (2) S1-A's timing delta. Both deliberate or contained.

---

## Slice 2

### Q7 — `origin_device_id` immutability

**Immutable in fact — because nothing writes it at all.** Grep-verified across `cps/` at
`2f2139a31`: the only occurrences are the column (`ub.py:1020`), the migration DDL
(`ub.py:2806,2818,2833`), and the count query (`annotations.py:125`). No path — reassign,
soft-delete, restore, bulk, backfill, `_upsert_annotation`, `ingest_bookmarks`,
`apply_portable` — writes or clears it. The invariant holds, vacuously.

The flip side is the finding: **no producer exists** (SHOULD-FIX as a planning gap, not a
code bug). `origin_count` in the delete preflight is 0 for every device in production; my
UX's remove-dialog copy ("4 highlights were made on this device") and the detail card's
"From {device}" line have no data source until an ingest path attributes origin. If that's
a later slice, fine — but then slice 2 shipped a dead counter and nobody should demo the
preflight numbers yet.

Related trap (NIT, will bite someone): `Annotation` now carries **two** provenance fields
with mirrored names — `device_origin_id` (free-text String, `ub.py:1013`, KOReader
echo-suppression, written by `annotation_portable.py:171`) and `origin_device_id` (FK to
`device`, `ub.py:1020`, the registry model). Different types, different semantics, same
words in different order. A future join on the wrong one will silently return nothing.
Rename or reconcile before a third field joins the family.

### Q8 — dangling origin references after soft-delete

Nothing found. The only device↔annotation join in the tree is the OUTER join in
`list_annotation_devices` (`annotations.py:96-105`) — an aggregate that cannot drop rows.
`_load_user_annotations` (`annotations.py:461-474`) has no device join; `_data_json_row`
(`annotations.py:646-672`) emits no device fields; exports touch neither. No read path can
hide an annotation whose origin device is soft-deleted. (Caveat: there is also no read path
that *resolves* an origin label — see D4 — so "renders safely" is currently vacuous rather
than demonstrated.)

### Q9 — mid-bulk failure state

Traced clean. Per-item `ub.session_commit` means items 1–2 are durably committed before item
3 raises; `bulk_reassign_annotations` rolls back the failed item and continues
(`annotations.py:1007-1024`); a post-rollback SQLAlchemy session is expired, not poisoned,
so items 4–10 process normally. `session_commit`'s deliberate non-catch of `IntegrityError`
(`ub.py:3378-3384` docstring — OBSERVED) propagates into bulk's generic handler and is
converted to per-item `database_error`. Correct per-item semantics.

Two residue items the tests don't reach:

- **NIT:** in the single-PATCH route, `reassign_annotation` runs with `commit=None`
  (flush-only) and an `IntegrityError` from that flush is caught by neither
  `except AssignmentError` nor `except ValueError` (`annotations.py:1084-1105`) — Flask
  returns an HTML 500. Concrete trigger: two concurrent assigns of the same annotation to
  the same device racing `uq_annotation_device_state` (browser double-click + optimistic
  retry). Should be a JSON 409.
- **NIT:** `_commit_required` converts a rolled-back commit to `RuntimeError`
  (`annotations.py:66-69`), which no route catches — rename/delete/restore/edit all surface
  it as an HTML 500 instead of a JSON error. Bulk handles this shape correctly; the single
  routes don't.

### Q10 — rename validation and dedup

Nothing found in validation. Probed against `rename_annotation_device`
(`annotations.py:107-118`): empty, 61-char, leading/trailing whitespace, whitespace-only
(fails the `label != label.strip()` check), embedded control chars including `\n`, `\t`,
`\x7f` — all rejected. 60 code points ≤ String(160) column; duplicates allowed per D2 by
design. Auto-label dedup (`device_registry.py:53-60`) is a bounded loop over a per-user
label set, terminates by construction, and the ≤55-char `label_base` cap keeps generated
labels inside the 60-char user contract. One NIT: `used_labels` includes soft-deleted
devices' labels, so a replacement device after a delete is born as "Model 2" — mildly
confusing, arguably correct (doesn't resurrect a retired identity).

### Q11 — downgrade lossiness

Lossy in a way that should be said out loud, though acceptable for a manual escape hatch.
`downgrade_device_management_slice` (`ub.py:2825-2835`) drops both state tables and all
three columns: a user loses **every assignment** (all rows → Unknown), all origin
attribution, routing revisions, delivery telemetry, and the retired-assignment undo
snapshots. Re-upgrade starts blank (NULL / revision 1) — nothing is restored. "Reversible"
is schema-true, data-false; the docstring should say so in one sentence. Slice 1's
downgrade additionally drops the registry itself (devices, identities, labels); its
content-id journal restore with the refuse-if-edited guard (`ub.py:2844-2856`) is genuinely
non-destructive — good. NIT: the two downgrades have an unstated order dependency — slice 2
must go first, or slice 1's `DROP TABLE device` strands the FK columns. No code wires
either downgrade (manual only — OBSERVED, no callers).

### Q12 — WIP commit `bab38b417` residue

Nothing found. The WIP touched exactly two things: the label-suffix format (" (n)" → " n")
and the `DeviceRetiredAssignment` model + its `create_all`/downgrade wiring. Both reached
the final tree in completed form — the model is fully used by soft-delete/restore, and the
missing `DROP INDEX` statements were added by the later hardening commit (`d96f1142c`). The
suffix change is pinned by `test_initial_labels_receive_plain_dedup_suffix`. No stub or
half-wired table survived.

### Additional slice-2 NITs (unasked, found while tracing)

- `routing_revision` NULL-base is inconsistent: reassign uses `(row.routing_revision or 1) + 1`
  (`annotations.py:998`), soft-delete/restore use `(… or 0) + 1` (`annotations.py:148,168`).
  Identical for any persisted row (column is `NOT NULL DEFAULT 1`); they diverge only for a
  created-and-reassigned-within-one-session row. Cosmetic.
- Reassign's no-op path (same target) repairs `desired=True` but leaves a stale
  `delivery_status="failed"` / `last_error_code`, while the move path resets both
  (`annotations.py:949-958` vs `966-970`). No reconciler consumes this yet (P2), but the
  asymmetry will be a puzzle later.
- `_device_json` (`annotations.py:77-88`) isoformats naive datetimes with no UTC suffix —
  the UI can't tell the zone from the payload. Contract wart, cheap to fix.
- A malformed non-null non-string bulk `assigned_device_id` (e.g. an int) fails every item
  with `device_not_found` under an HTTP 200 instead of 400-ing the request
  (`annotations.py:1122-1136` → `933-947`). Compliant with D5's letter; the UI's
  total-failure toast covers it. Fine to leave.

---

## D1–D10 coverage

Sol built without reading `notes/K3-DEVICE-ANNOTATION-UX.md`; this is the reconciliation.
Verdicts cite the satisfying endpoint/field at `2f2139a31`.

| # | Verdict | Evidence / gap |
|---|---|---|
| **D1** device list | **SATISFIED** | `GET /api/annotations/devices` (`annotations.py:185-191`) returns per-device `{public_id, label, type, model, firmware, first_seen, last_seen, annotation_count, active}` (`_device_json`, `annotations.py:77-88`). Renames vs spec: `device_id`→`public_id`, `deleted`→`active` (inverted) — trivial adapter. Two caveats: `annotation_count` counts *assigned* only (the list's outer join, `annotations.py:96-105`), and it is **user-global, not per-book** — the filter chips' per-book counts ("Unknown device 559") cannot come from here; they need D4's per-row fields to aggregate client-side. |
| **D2** rename | **SATISFIED** | `PATCH /api/annotations/devices/<public_id>` (`annotations.py:194-207`), 1–60 chars, reject-not-truncate, control-char rejection, duplicates allowed. Exactly the spec. |
| **D3** soft-delete + preflight | **SATISFIED** | `DELETE /api/annotations/devices/<id>` (`annotations.py:219-228`) retains the label, snapshots then clears assignments (`annotations.py:133-155`); preflight is a separate `GET .../delete-preflight` returning `{origin_count, assigned_count}` (`annotations.py:210-216`) — better than my spec (dialog fetches before rendering). Caveat: `origin_count` is always 0 until an origin producer exists (Q7), so the dialog's first paragraph will read "0 highlights were made on this device" against reality. |
| **D4** per-row device fields in the annotation list | **ABSENT** — the finding of this pass | `_data_json_row` (`annotations.py:646-672`) emits no `origin_device_id`, no `assigned_device_id`, no `anchor_status`; no list endpoint carries an envelope `devices` map. Only the PATCH *response* gained `assigned_device_id` + `routing_revision` (`annotations.py:1107-1119`). **What the UI cannot do:** the P0 meta line (`[ Libra Colour ▾ ] · 12%`), the filter chips (neither the per-row value nor per-book chip counts), the Unknown-device group, the assign dropdown's *current* value, and filter-scoped Select-all. P0 items 2–4 of my doc are unbuildable; the bulk mechanics (D5) exist but the UI can't show what it's operating on, and Undo can't name previous values. The gap is in Sol's backend, not my assumption — D4 was explicit, including the envelope-map-vs-denormalised-labels detail. `anchor_status` was flagged in my doc as "Sol must say which" — unanswered. |
| **D5** single + bulk reassign | **SATISFIED** (with adapter) | Single: `PATCH /annotations/<book>/<aid>` accepts `assigned_device_id` (nullable — un-assign works, so Undo-to-Unknown works) plus optional `expected_routing_revision` (`annotations.py:1084-1092`). Bulk: `POST /api/annotations/assignments/bulk` (`annotations.py:1122-1136`) — per-item `{annotation_id, ok, error_code}` with HTTP 200 on mixed results, 500 cap, successes committed (Q9 traced). Deviations from my spec, all survivable: path and shape differ (items carry `book_id` — this accidentally *enables* cross-book bulk, an improvement); `error_code` vs my `error`; per-item optimistic-lock key is optional, so bulk from list data is last-wins (compatible with my Undo design). |
| **D6** restore | **SATISFIED** | `POST /api/annotations/devices/<id>/restore` (`annotations.py:231-241`) returns `{restored_assignment_count, assignment_conflict_count}` — everything the Undo toast needs. One-shot semantics (snapshots consumed even on conflict, `annotations.py:157-181`) — fine for an 8-second Undo window. |
| **D7** per-row delivery states | **ABSENT** (P2, expected) | `AnnotationDeviceState` exists (`ub.py:1083-1103`) and reassign/soft-delete/restore maintain `desired`, but no read API exposes per-row `device_states`. The "On:" line cannot be built. Expected this slice; not a defect. |
| **D8** chapter titles | **ABSENT** (P2, expected) | Nothing. Group-by-Chapter stays blocked, as my doc predicted (raw `content_id` chapter filename is not human-readable). |
| **D9** SPA Kobo token endpoints | **ABSENT** (P2, expected) | Nothing. The Kobo setup card must take the graceful-degradation path I spec'd (link to the classic page). |
| **D10** server-side initial label | **SATISFIED** | `upsert_kobo_device` labels from the model header with a plain dedup suffix ("Kobo Libra Colour 2", `device_registry.py:53-60`); >55-char models fall back to "Kobo" rather than truncating into the user contract. Model changes on a known device update `model` but not the label — correct (label is user-domain). |

### The bottom line for the reconciler

P0 splits in two. **Buildable now:** the Devices page (D1+D2+D10), rename, remove with
counted confirmation (D3, with the origin_count-always-0 caveat), restore/Undo (D6), and
the assignment *mechanics* single + bulk (D5). **Not buildable:** the entire Highlights-list
redesign — meta line, chips, groups, current-value dropdown, partial-failure UX targeting —
because the list payload carries no device fields (D4 ABSENT). Shipping D4 (per-row
`origin_device_id` + `assigned_device_id` + an envelope devices map on the list endpoint)
unblocks everything else in one move. Note also that until an origin *producer* ships
(Q7), every per-row origin would read NULL — so D4 and origin-attribution-on-ingest are the
same unblocking leg, and my "Unknown device is the day-one default" design assumption holds
for exactly as long as that leg is pending.
