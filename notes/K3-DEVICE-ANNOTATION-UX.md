# K3 — Device & Annotation UX design

Status: design complete, ready for handoff (2026-08-09)
Author: Kimi K3 (UX/UI leg). Backend model: Sol. Reconciler: Opus.
Scope: **design only** — no `frontend/` changes (CWNG READER owns the reader and is
editing it live). All wireframes are ASCII. All copy strings are short, non-idiomatic,
and need `t()` wrapping.

I could view the screenshots; the baseline descriptions in the brief match what I saw.

---

## 0. The three questions, kept separate

Sol's model gives every annotation three independent device facts. The UI fails if it
conflates them, so each gets its own place and its own visual register:

| Question | Field | Where shown | Register |
|---|---|---|---|
| Where was it made? (history) | `origin_device_id` — immutable | Detail card, static text "From …" | archaeology |
| Where should it be? (intent) | `assigned_device_id` — mutable | List meta line + dropdown | the control |
| Where is it actually? (fact) | `AnnotationDeviceState` rows | Detail card "On:" line | telemetry (P2) |

"Which device is this highlight with?" can have several true answers (a highlight can
sit on two devices at once). The list answers the *intent* question — one value,
always scannable. The *fact* question is plural and lives one click deeper. This is
the load-bearing decision of the whole design; everything else follows from it.

**Unknown is the day-one default, not an edge case.** All ~595 historical rows have
NULL origin and NULL assignment. "Unknown device" is designed as a first-class state
(filter chip, group header, assign target), never as an error.

---

## 1. Surface map

```
/account                         → new "E-readers" card (summary + link)      [P1]
/account/devices                 → NEW PAGE: device registry manager          [P0]
/book/:id/annotations            → Highlights page, redesigned list           [P0]
    ├─ filter chips (by device)                                             [P0]
    ├─ quiet per-row device meta line + assign dropdown                     [P0]
    ├─ selection mode + sticky bulk bar                                     [P0]
    └─ in-place detail card (note, color, delete, origin, delivery)      [P1/P2]
Reader.tsx popover               → contract only, CWNG READER builds it       [P2]
```

**Why Account and not Admin:** annotations are per-user data (`Annotation.user_id`),
and the classic UI already puts Kobo device setup in the *user profile*
(`user_edit.html`: Create/View token, Delete, Force full sync, Resend book) — not in
admin. Admin is instance configuration; a device belongs to a reader's account.
Precedent and data ownership agree.

**Why a new page and not just an Account card:** the Account page is a stack of cards
(Profile / Change password / App passwords). A card fits a summary, but the device
surface must also absorb the classic-UI Kobo setup block (token create/view/delete,
force sync) which the SPA has never had — this is the first SPA-native device surface.
A dedicated `/account/devices` route gives that room; the Account card links to it.

---

## 2. Device manager — `/account/devices`

### 2.1 Page, desktop

```
┌────────────────────────────────────────────────────────────────────┐
│ ‹ Account                                                          │
│ 📱 E-readers                                                       │
│                                                                    │
│ ┌────────────────────────────────────────────────────────────────┐ │
│ │  Libra Colour                              [ Rename ]  [ ⋯ ]   │ │
│ │  Kobo Libra Colour · FW 4.45.23684                             │ │
│ │  312 highlights · Last seen 2 hours ago                        │ │
│ ├────────────────────────────────────────────────────────────────┤ │
│ │  Clara HD                                  [ Rename ]  [ ⋯ ]   │ │
│ │  Kobo Clara HD · FW 4.38.23170                                 │ │
│ │  4 highlights · Last seen 34 days ago · Not seen lately        │ │
│ └────────────────────────────────────────────────────────────────┘ │
│                                                                    │
│ Kobo setup                                            (P2 section) │
│ ┌────────────────────────────────────────────────────────────────┐ │
│ │ Sync URL        [ Show ]  [ Copy ]  [ Delete ]                 │ │
│ │ [ Force full sync ]      [ Resend a book… ]                    │ │
│ └────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

Columns/fields per device row (it's a card list, not a table — rows are touch targets):

- **Friendly label** — primary text, user-editable via Rename (inline: label becomes
  an input, Save/Cancel, Esc cancels, Enter saves). Server-initial default from model
  ("Kobo Libra Colour", dedup suffix "… 2"). Depends on **D2, D10**.
- **Model · firmware** — muted second line, straight from the Kobo headers.
- **{n} highlights · Last seen {when}** — count links nowhere; it's context, not
  navigation. `{when}` is relative ("2 hours ago", "34 days ago").
- **"Not seen lately"** — muted text (not red, not an icon badge) when
  `last_seen` > 30 days. A backup reader sitting in a drawer is normal; the UI must
  not alarm about it.
- **⋯ menu**: one item, "Remove device". (Future items — force sync this device,
  view unsynced — slot in here without redesign.)

Type icons: decorative only (`aria-hidden`), label text carries the meaning. Kobo and
KOReader get an e-reader glyph, webreader a browser glyph, unknown future types a
generic glyph + model text. Never icon-only.

### 2.2 Empty state

```
┌────────────────────────────────────────────────────────────────────┐
│ 📱 E-readers                                                       │
│                                                                    │
│              No e-readers yet.                                     │
│        Devices appear here after their first sync.                 │
│                                                                    │
│              [ Set up Kobo sync ]        ← scrolls to card below   │
│                                                                    │
│ Kobo setup                                                         │
│ ┌────────────────────────────────────────────────────────────────┐ │
│ │ No sync URL yet.            [ Create sync URL ]                │ │
│ └────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

Copy: `No e-readers yet.` / `Devices appear here after their first sync.` /
`Set up Kobo sync`. The Kobo setup card always renders (even P0, as a link to the
classic page if D9 isn't built — graceful degradation, not absence).

### 2.3 What a user does here

1. **Rename** — inline edit, the most common action (two identical "Kobo Libra
   Colour" entries are why the label exists).
2. **See counts + last seen** — answers "is my old reader still syncing?".
3. **Remove a stale device** — designed destructive flow, below.
4. (P2) **Kobo token lifecycle** without leaving the SPA.

### 2.4 Remove device — the designed confirmation

Removing a device does **not** delete annotations. It clears that device's
assignments and orphans its origins. The dialog says exactly that, with the real
numbers (from **D3**'s preflight response):

```
┌──────────────────────────────────────────────────────┐
│ Remove Clara HD?                                  ✕  │
│                                                      │
│ 4 highlights were made on this device. They are      │
│ not deleted. They will show "Deleted device" as      │
│ where they came from.                                │
│                                                      │
│ 2 highlights assigned to this device will become     │
│ "Unknown device".                                    │
│                                                      │
│ This device will no longer sync.                     │
│                                                      │
│                [ Cancel ]   [ Remove device ]        │
└──────────────────────────────────────────────────────┘
```

- `role="alertdialog"`, `aria-describedby` → the counts paragraph. **Cancel is
  default-focused**; Remove is destructive-styled. Esc closes. Focus returns to the
  ⋯ button on close.
- On success: toast `Clara HD removed.` + **Undo** if soft-delete restore exists
  (**D6**); without D6 the toast has no Undo and the dialog copy gains one more
  line: `This cannot be undone.` (Build D6; it's cheap on top of soft-delete.)
- Zero-count devices skip the counts paragraph entirely — dialog shrinks to the
  sync line. Same component, conditional paragraph; no "0 highlights" noise.

### 2.5 Account card (P1)

```
┌─ 📱 E-readers ──────────────────────────────────────┐
│ Libra Colour · 312 highlights                       │
│ Clara HD · 4 highlights                             │
│ [ Manage e-readers ]                                │
└─────────────────────────────────────────────────────┘
```

Empty: `No e-readers yet.` + the same link. This is pure discoverability for the
new page; three lines of JSX given D1.

---

## 3. Highlights page — the redesigned list

### 3.1 Toolbar, desktop

```
│ ‹ Back to book                                                     │
│ 🖊 Highlights — Dorian Gray PREFIX Probe2              595         │
│                                                                    │
│ Filter: ( All 595 ) ( Unknown device 559 ) ( Libra Colour 34 ) ( Clara HD 2 ) │
│ Group:  [ Book order ▾ ]        [ Select ]    [ Export ▾ ] [ Import ]        │
```

Fixes from the baseline critique, deliberately:

- **Import/export split.** `Export ▾` is a menu button (Markdown / CSV / JSON) with
  `aria-haspopup="menu"`. `Import` stands alone. View controls (Filter, Group,
  Select) cluster left; data-in/out actions cluster right. Opposite operations no
  longer share one undifferentiated pill row.
- **Filter chips** = the device facet. Single-select toggle buttons in a
  `radiogroup` (`aria-label="Filter by device"`). Counts in every chip. >6 devices:
  chips collapse to `[ Device: All ▾ ]` — the same dropdown the mobile layout uses
  (§6.2), so there's one overflow pattern, not two.
- **Dead space:** the filter row occupies the previously empty band under the title.
  No left rail is added; at these widths a rail would squeeze the list.

### 3.2 The row — solving the 3px collision

The coloured bar keeps exactly one meaning: **highlight colour** (it already has
`role="img"` + colour-name label — SC 1.4.1). Device never touches the bar. Device
is **text in a meta line**, and the device name *is* the assign dropdown trigger:

```
│▌ Live PATCH on fresh image                                         ⋯ │
│▌ sub-project 2 proof                                                 │
│▌ [ Unknown device ▾ ] · 5%                                           │
```

```
│▌ alpha                                                               ⋯ │
│▌ [ Libra Colour ▾ ] · 12%                                            │
```

- `▌` = 3px colour bar. Quote clamped to 2 lines, note clamped to 1 line (full text
  in the detail card). **List = index; detail card = content.**
- Meta line (13px, muted — token must hold ≥4.5:1 on caliBlur, §6.4):
  `[ device ▾ ] · {progress}%`. Device first, always same position — that's what
  makes 100+ rows scannable. Progress shows only when present; consistent slot, no
  more floating `5%` orphans.
- `Unknown device` renders in the same control, italic-muted. It is a value, not an
  error. Clicking it opens the same dropdown — the fix is one gesture.
- `⋯` overflow: `Show in book` (only when anchor resolvable, §5.3), `Edit`, `Delete`.
  Always visible on touch; hover/focus-within on desktop; a real button with a real
  name (`More actions for highlight: {text…}`).
- Density: ≈54–72px/row all-in vs ≈73px today, *and* the list virtualises (render
  ±2 viewports; 595 rows ≈ 40 DOM nodes). Build note, not a user-facing setting.

### 3.3 The assign dropdown (single)

Trigger: the device name button (`aria-haspopup="listbox"`, accessible name
`Device: Libra Colour` / `Device: unknown`). Popup:

```
┌──────────────────────────────────┐
│ Assign to device                 │  ← aria-label on the listbox
│ ──────────────────────────────── │
│   Unknown device                 │
│ ✓ Libra Colour                   │  ← current assignment
│     Kobo Libra Colour            │  ← model, muted 12px
│   Clara HD                       │
│     Kobo Clara HD                │
└──────────────────────────────────┘
```

- **Friendly labels, never ids** — the operator's explicit requirement. Model line
  disambiguates duplicate labels. Deleted-but-referenced devices can't appear here
  (they're not assignable); a row *showing* "Deleted device" has static text, not
  this dropdown.
- Standard listbox keyboard: ↑/↓ move, Enter/Space choose, Esc closes → focus back
  to trigger, character typeahead. Items ≥36px tall.
- Choosing applies immediately — no Save. Optimistic update + toast
  `Assigned to Libra Colour.  [ Undo ]` (§4.3). "Unknown device" is a normal option
  (un-assign = set NULL; depends on **D5** accepting NULL).

### 3.4 Group by

`[ Group: Book order ▾ ]` — options: **Book order** (default; existing
`chapter_progress` sort — this is why a consistent progress slot earns its place),
**Device**, (P2) **Chapter** (needs **D8** chapter titles; the raw `content_id`
chapter filename is not human-readable, so this is not free).

Grouped by Device:

```
│ ── Unknown device ───────────────────────────── 559 ── │
│▌ alpha …                                               │
│▌ beta …                                                │
│ ── Libra Colour ────────────────────────────── 34 ──── │
│▌ Live PATCH on fresh image …                           │
```

Sticky group headers with counts. In selection mode each header gains
`Select all in group`. Meta line drops the device control inside groups (redundant)
and keeps progress. Unknown sorts first — it's the group that needs action.

---

## 4. Bulk reassignment — the 559 feature

Per-row reassignment of 559 rows is a punishment. The hero flow is three gestures:

```
[ Filter: Unknown device ] → [ Select ] → [ Select all 559 ] → [ Assign to device ▾ ]
```

### 4.1 Selection mode, desktop

Off by default — 595 always-visible checkboxes is noise. `Select` in the toolbar
toggles mode; Esc exits. In mode, a **sticky bar under the toolbar** (not the
catalog's floating bottom bar — at 595 rows Select-all must stay adjacent to the
filter chips that scoped it):

```
┌────────────────────────────────────────────────────────────────────┐
│ ☐ Select all 559      559 selected     [ Assign to device ▾ ] [ Done ] │
├────────────────────────────────────────────────────────────────────┤
│ ☑ ▌ alpha …                                                        │
│ ☐ ▌ beta …                                                         │
```

- `Select all {n}` scopes to the **current filter** — with the Unknown chip active
  it selects 559, not 595. The chip row is the query; the bar reports the count.
- Rows: each is a `<label>` wrapping its checkbox — whole row toggles. Checkbox
  20px visual inside a ≥48px row.
- A11y reuse: the catalog's `BulkBar` already proves the pattern — `useAnnouncer()`
  for `{n} selected`, Esc handling, dropdown sub-menus. Same announcements here;
  different anchoring (sticky top) for the reasons above. `role="region"`,
  `aria-label="{n} selected"`.

### 4.2 Selection mode, 390px mobile — solved, not hand-waved

The problem: checkbox column + colour bar + text + ⋯ in 358px of content width.
The answer: **the row is the checkbox.**

```
┌──────────────────────────────────┐ 390px
│ ☰  Highlights — Dorian Gray  595 │
│ [ Device: Unknown (559) ▾ ]      │ ← chips collapse to one dropdown chip
│ [ Select ]              [ ⋯ ]    │ ← Export/Import fold into ⋯
├──────────────────────────────────┤
│ 559 selected      Select all     │ ← sticky bar, compact
│ [ Assign to device ▾           ] │ ← full-width primary action
├──────────────────────────────────┤
│ ☑ ▌ alpha                        │
│ ☐ ▌ beta                    12%  │
└──────────────────────────────────┘
```

- Entry: toolbar `Select`, **or long-press any row** (enters mode with that row
  checked). One-time hint under the toolbar in mode: `Tip: long-press a highlight
  to select.` (dismissable, `aria-hidden` after dismissal).
- In mode: checkbox visual 20px at left, hit area = the whole row ≥48px; colour
  bar shifts right of the checkbox; ⋯ overflow is hidden in mode (actions are on
  the bar now). Progress stays right-aligned. Nothing exceeds 390px; nothing needs
  horizontal scroll.
- The sticky bar's controls: count text, `Select all` text-button, full-width
  `Assign to device ▾` below them (two-row bar). `Done` top-right as text button.
  All targets ≥44px in mode.

### 4.3 Apply, undo, failure

**Non-destructive ⇒ no confirmation modal.** Reassignment changes intent, deletes
nothing (A→B keeps A's copy), and is itself undoable. Modals for reversible bulk
actions are friction theatre; the safety net is Undo.

- **Optimistic** apply: rows update instantly; toast
  `{n} assigned to Libra Colour.  [ Undo ]`, 8s, `aria-live="polite"`, action
  button ≥44px. Undo re-issues with previous values (incl. NULL — **D5**).
- **Total failure:** rows roll back, toast `Couldn’t assign. [ Retry ]`.
- **Partial failure** (the 559 case makes this a certainty, not a tail risk):

```
┌────────────────────────────────────────────────────┐
│ 551 of 559 assigned to Libra Colour.               │
│ 8 failed.                              [ Retry ]   │
└────────────────────────────────────────────────────┘
```

  The 8 failures **stay selected** — Retry is one tap and re-fires only the still-
  selected set. Each failed row's meta line gains `Not assigned —` (muted warning
  text with a small icon, `aria-live` off; the toast already announced it). The
  state survives until next successful apply or mode exit. Depends on **D5**
  returning per-item results; an all-or-nothing endpoint makes this UX impossible,
  which is why D5 specifies per-item.

### 4.4 Keyboard pass (bulk)

`Select` → focus moves to `Select all {n}`. List uses **roving tabindex**: one Tab
stop for the whole list, ↑/↓ between rows, Space toggles, Home/End jump, Esc exits
mode. 595 individual Tab stops would be hostile; this is the standard grid pattern
applied to a list. Announcements via `useAnnouncer`: `559 selected.`, `{n} assigned
to Libra Colour.`.

---

## 5. Editing one annotation — the detail card

### 5.1 In-place expansion (no modal)

Enter/click a row → it expands in place. Modals over a 595-row list lose scroll
position and cost focus-management complexity; expansion keeps context.

```
│▌ ┌──────────────────────────────────────────────────────────────┐
│▌ │ “Live PATCH on fresh image”                        [ Close ] │
│▌ │                                                              │
│▌ │ Device   [ Libra Colour                              ▾ ]     │
│▌ │ Color    (● Yellow) (○ Red) (○ Green) (○ Blue)               │
│▌ │ Note     ┌────────────────────────────────────────────┐      │
│▌ │          │ sub-project 2 proof                        │      │
│▌ │          └────────────────────────────────────────────┘      │
│▌ │          [ Save note ]                                       │
│▌ │                                                              │
│▌ │ From Libra Colour (Kobo) · May 3, 2026                       │
│▌ │ On: Libra Colour ✓ delivered · Clara HD — pending    (P2)    │
│▌ │                                   [ Delete highlight ]       │
│▌ └──────────────────────────────────────────────────────────────┘
```

### 5.2 Mutable vs immutable — communicated by shape, never by nagging

Per the standing rule (699 design, and it matches Sol): **position/anchors and
origin are immutable; colour, note, assignment, deletion are mutable.**

- Immutable things are **static text**: the quote is a `<blockquote>`, origin is a
  plain line `From Libra Colour (Kobo) · May 3, 2026`. No disabled inputs, no
  tooltips saying "you can't", no banners. A user learns the rule from the
  interface's grammar: *controls change things; text is history.*
- Mutable things are **controls**: Device dropdown (applies immediately + Undo),
  Colour as a 4-item `radiogroup` of named swatches (applies immediately + Undo;
  the 4-colour Kobo-roundtrippable set already enforced server-side),
  Note as textarea + explicit `Save note` (explicit save beats autosave here:
  unambiguous, and screen-reader users get one clean `Note saved.` announcement
  instead of a chatter of dirty-state updates).
- `Delete highlight` → soft-delete + Undo toast (same pattern as assign). The
  backend already soft-deletes (`hidden=True`).
- PATCH endpoint for colour/note exists today (`annotations_edit`); the only new
  field is `assigned_device_id` (**D5**).

### 5.3 The stranded anchor (re-generated KEPUB)

The row always renders — the text is data and is safe. Two additions, P2:

- List meta line gains `· Not in current file` (with a small ⚠, named
  `Warning: this highlight can’t be shown in the book` for screen readers) and the
  ⋯ menu's `Show in book` is removed for that row.
- Detail card gains one quiet paragraph:

  `The book file changed since this highlight was made. It can’t be shown in the
  book. The text and note are kept.`

  plus a `Copy highlight text` button. No auto-repair promises, no scary red.
  Depends on **D4**'s `anchor_status` flag (today the frontend cannot distinguish
  "no CFI yet" from "can never resolve" — Sol must say which).

### 5.4 Notes (deliverable 4)

- **1:1 note↔highlight is sufficient.** This design never needs more; a longer
  thought is a longer textarea.
- **List:** note renders under the quote (as today), clamped to 1 line, italic.
  Rows without a note show nothing — no `Add note` button per row (595 buttons =
  noise). Adding happens from the detail card or the reader.
- **Detail card:** the textarea above. Empty state of the field is placeholder
  `Add a note…`; `Save note` disabled until dirty; clearing + saving deletes the
  note (PATCH `note_text: null`, already supported).
- **Reader (contract for CWNG READER):** the selection popover gains the same
  4-swatch colour group + `Add note` expanding to the same textarea; tapping an
  existing highlight opens the popover in edit mode. Same copy, same field name,
  same PATCH. They own the pixels; this is the seam.
- Display consistency: note text is user content — render as text, never as HTML.

---

## 6. A11y + responsive spec (WCAG 2.2 AA, caliBlur)

### 6.1 Focus order, Highlights page

1. Skip link → `Back to book` → H1 (existing `RouteA11y` announces the page).
2. Filter chips: one Tab stop, `role="radiogroup"` `aria-label="Filter by device"`,
   arrows move, each chip named `Libra Colour, 34 highlights`.
3. `Group by` select → `Select` toggle → `Export` menu → `Import`.
4. List: one Tab stop (roving tabindex); ↑/↓ rows, Enter expands, (in mode) Space
   toggles, Home/End.
5. Detail card internal order: Device → Colour radios → Note textarea → Save note →
   Delete highlight → Close. Esc collapses, focus returns to the row.
6. Dialogs (Remove device): focus trapped, Cancel default focus, Esc closes, focus
   restored to invoker.

### 6.2 Accessible names (the no-unlabelled-chip rule)

| Element | Name |
|---|---|
| Colour bar | `role="img"`, `aria-label="Red"` (exists; keep) |
| Device dropdown trigger | `Device: Libra Colour` / `Device: unknown`, `aria-haspopup="listbox"` |
| Row checkbox | `Select highlight: Live PATCH on fresh image` (quote, ~60 chars) |
| ⋯ button | `More actions for highlight: {text…}` |
| Sticky bulk bar | `role="region"`, `aria-label="{n} selected"` |
| Colour swatches | `role="radiogroup"` `aria-label="Colour"`; items `Yellow`…`Blue` |
| Stale text | plain text `Not seen lately` — no icon-only semantics anywhere |
| `Not in current file` | text + icon `aria-hidden`; the text is the name |

Device identity is **never colour-coded** — devices have no colour channel at all.
That channel belongs to highlight colour alone (§3.2).

### 6.3 Touch targets

Rows ≥48px in selection mode (whole row is the target). Chips ≥32px tall. Dropdown
items ≥36px. ⋯ and Close: 36px visual, 44px hit-slop under `pointer: coarse`.
Toast action buttons 44px. WCAG 2.2 SC 2.5.8 floor (24px) is met everywhere;
44px is the target for anything primary on touch.

### 6.4 Contrast, motion, misc

- Meta-line muted text and chip counts must verify ≥4.5:1 against caliBlur's dark
  surface (SC 1.4.3) — flag to implementer: check the muted token, don't assume.
- `prefers-reduced-motion`: row expansion and toast entry are instant.
- Toasts: `aria-live="polite"` via the existing `useAnnouncer`; never `assertive`
  for success, `assertive` acceptable for total failure.
- Long-press has a keyboard equivalent (the `Select` button) — never the only path.
- i18n: every string above goes through `t()`; plurals via the `{n}` placeholder
  form already used in `BulkBar`.

---

## 7. Dependencies on Sol (named, blocking)

| # | Need | Blocks |
|---|---|---|
| **D1** | `GET /api/v2/devices` → `{device_id, label, type, model, firmware, first_seen, last_seen, annotation_count, deleted}` per user | Devices page, chips, dropdowns |
| **D2** | `PATCH /api/v2/devices/<id>` `{label}` (1–60 chars; duplicate labels allowed — UI disambiguates via model) | Rename |
| **D3** | `DELETE /api/v2/devices/<id>` as **soft-delete** (retain label for origin display; clear `assigned_device_id` where it pointed here) + preflight counts `{origin_count, assigned_count}` | Remove dialog, "Deleted device" state |
| **D4** | Annotation list payload: per-row `origin_device_id`, `assigned_device_id`, `anchor_status: "ok"\|"unresolved"`; envelope-level `devices: {id: {label, model, type}}` map (no per-row denormalised labels) | Meta line, dropdowns, stranded UI |
| **D5** | Single: extend `PATCH /annotations/<book>/<aid>` with `assigned_device_id` (nullable). Bulk: `POST /annotations/<book>/reassign` `{annotation_ids, assigned_device_id}` → **per-item** `{results: [{annotation_id, ok, error?}]}`, HTTP 200 with item failures | All assignment UX |
| **D6** | `POST /api/v2/devices/<id>/restore` (undo for soft-delete) | Remove Undo (P1) |
| **D7** | Per-row `device_states: [{device_id, status}]` (or per-annotation endpoint) | "On:" line (P2) |
| **D8** | Chapter title per row (or chapters map) | Group-by-Chapter (P2) |
| **D9** | SPA-JSON Kobo token endpoints: create/view/delete token, force sync. **Do not** port classic's manual book-ID "Resend" field — the SPA version uses the existing `MetadataTypeahead` book picker | Kobo setup card (P2) |
| **D10** | Server-side initial label from model + dedup suffix ("Kobo Libra Colour 2") | Day-zero labels |

Soft-delete (D3) is strongly preferred over hard-delete: it keeps origin labels
resolvable *and* powers Undo. If hard-delete ships instead, origins render
`Deleted device` and Remove loses its Undo — the design survives, but it's worse.

Historical rows: NULL origin + NULL assignment ⇒ `Unknown device` everywhere in this
UI. No migration needed for the design to work.

---

## 8. Priorities — what ships first

**P0 — the operator's core ask, nothing else:**
1. Devices page: list + counts + last-seen + inline rename (D1, D2, D10).
2. Highlights list: density fix, device meta line with Unknown state, filter chips,
   Import/Export split (D4).
3. Single assign dropdown from the meta line, optimistic + Undo (D4, D5).
4. Bulk: selection mode, filter-scoped Select-all, bulk assign, Undo, and
   per-item partial-failure UX (D5). *Ships with P0 or not at all — single-row
   assign alone fails the 559 case.*

**P1 — next train:**
5. Detail card: note add/edit, colour change (API exists today), delete + Undo.
6. Remove device with counted confirmation (D3; + Undo if D6).
7. Group by Device.
8. Account "E-readers" card (D1 only — cheap discoverability).
9. Export parity: origin/assigned device columns in CSV/JSON/MD so exports stay
   truthful about provenance.

**P2 — when the muscle is built:**
10. Kobo setup card SPA parity (D9) — token create/view/delete, force sync,
    typeahead-based Resend.
11. "On:" delivery line (D7) and `Not in current file` stranded-anchor UI (D4 flag).
12. Group by Chapter (D8). Reader popover contract (handoff to CWNG READER).

**Cut, deliberately:**
- **Coloured per-row device badges** — the noise failure the brief warns about; the
  colour channel is already owned (highlight colour).
- **Delivery status in list rows** — telemetry belongs in the detail card.
- **Manual device-id entry anywhere** — including the classic "Resend book ID"
  field; its SPA port uses a book picker.
- **Multi-assignment editing UI** — assignment is single-valued intent by model;
  multi-device *fact* is display-only ("On:" line).
- **Per-device highlight pages** — filter chips + URL params cover the need.
- **A density setting** — one good density beats a toggle.

---

## 9. Answers to the baseline critique

1. **Density** → 2-line quote clamp, 1-line note clamp, ≈54–72px rows,
   virtualisation; list is an index, detail card holds content (§3.2).
2. **Dead space** → filter chip row lives in the empty band under the title (§3.1).
3. **3px bar collision** → bar keeps colour, device becomes a text control on the
   meta line; two indicators never share one strip (§3.2).
4. **Progress noise** → progress moves into a fixed meta-line slot, shown only when
   present; it stays because Book-order grouping needs it visible (§3.2, §3.4).
5. **No grouping** → Group by Device ships P1; Chapter P2 pending real chapter
   titles (D8). Book order stays default — it's a reading view first.
6. **Import/export row** → split into `Export ▾` menu vs standalone `Import`, with
   view controls pulled left (§3.1).
