# Kobo highlight loss, cause 1b — a TOC that anchors *into* a document

**Date:** 2026-08-15 · Companion to `KOBO-HIGHLIGHT-LOSS-ROOT-CAUSE-2026-08-15.md`.
Measured on two devices. Everything below is observed unless labelled otherwise.

Cause 1 in the root-cause note is *"the chapter identity Nickel stores for a highlight does not
equal the chapter identity it renders from"*. That note found one way to produce it — a `../` in an
OPF manifest href. **This is a second way, and it is an order of magnitude more common.**

---

## The mechanism

Kobo identifies a chapter as `<book-uuid>!<opf-dir>!<href>` and renders a `Bookmark` only when
`Bookmark.ContentID` **exactly equals** a `content` row with `ContentType=9`. No fuzzy matching.

When a book's TOC points **into** a spine document with an anchor —
`chapter.xhtml#pgepubid00005` — the stored bookmark carries the fragment. The spine document's
`ContentType=9` row does not. They never match, and every highlight in that book is stored but
never drawn.

### The bookmark matches neither the chapter row nor the TOC row

This is the part worth knowing, and it corrects the natural assumption that Nickel reuses the TOC
entry's identity:

| what | ContentID |
|---|---|
| `ContentType=9` (chapter, renders) | `…!OEBPS!…541-h-0.htm.xhtml` |
| `ContentType=899` (TOC entry) | `…!OEBPS!…541-h-0.htm.xhtml#pgepubid00005**-2**` |
| **`Bookmark.ContentID`** | `…!OEBPS!…541-h-0.htm.xhtml#pgepubid00005` |

```
Bookmark rows matching an 899 row: 0 / 3
Bookmark rows matching a 9 row:    0 / 3
```

It is a **third form** — `opf_dir` + the raw TOC href, without the `-N` disambiguating suffix the
899 rows carry. So the bookmark anchors to *the TOC href as written*, and anything in that href
which is not part of the spine document's identity (a fragment, a `../`) breaks the match.
This also applies retroactively to cause 1: those bookmarks were never the 899 form either.

---

## Measured

### Device A — Kobo Clara BW, KEPUBs produced by current `main`

| book | highlights | anchored | TOC links | with `#` |
|---|---|---|---|---|
| 1984 | 6 | **6** | 3 | 0 |
| The Age of Innocence | 3 | **0** | 42 | **42** |

Both OPF manifests are clean — 0 escaping hrefs, 0 fragments. **The defect is entirely in the
NCX**, so the cause-1 containment fix does not address it, and the current pipeline produces
affected books today.

**Why 1984 works is the useful clue.** Its filename is `…_split_0_split_001.html` — calibre had
already split it into per-chapter files, so every TOC href targets a whole document and needs no
anchor. So the real discriminator is *"the TOC targets a position inside a document rather than a
document"*, and pre-split books never do.

### Device B — Kobo Libra Colour, a real long-term library

| volume | bookmarks | anchored | with `#` |
|---|---|---|---|
| The Iliad | 608 | 24 | **584** |
| King in Yellow | 221 | 1 | **220** |
| **The Odyssey** | 400 | **400** | **0** |
| three others | 873 | 873 | 0 |

Perfect correlation across **2,102 bookmarks**. The Odyssey is the control: same
`file:///mnt/onboard/…` VolumeID shape, zero fragments, 400/400 rendering.

### Blast radius

```
kepubs scanned:                       216
TOC points at #fragments:   92  (42.6%)
total fragment links:            23,494
worst: Dune 1780, Iliad 1767, Heretics of Dune 1366
```

Cause 1's escaping-href defect affected 5 of 216 (2.3%). This is the classic Project-Gutenberg
shape: few large spine documents, a fine-grained NCX anchoring into them.

⚠️ **92 is the population at risk, not a confirmed count.** A book with a fragmented TOC can still
anchor fine if the reader highlights in a chapter the TOC happens to target without one.

---

## Recovery: these are repairable, including ones previously written off

The root-cause note recorded a set of orphans as *"book no longer on the device … not repairable by
rewriting"*. That was wrong — retracted in #1658. The census that produced it probed chapter rows
with `ContentID LIKE VolumeID || '%'`, which returns zero for a `file:///mnt/onboard/…` volume
whether or not the book is present. It reports zero for The Odyssey too, and The Odyssey renders
400/400.

**Census — how many fragmented bookmarks would re-anchor if the fragment were stripped:**

```sql
select
  sum(case when b.ContentID like '%#%' then 1 else 0 end) as fragmented,
  sum(case when b.ContentID like '%#%'
            and exists(select 1 from content c
                       where c.ContentType='9'
                         and c.ContentID = substr(b.ContentID,1,instr(b.ContentID,'#')-1))
       then 1 else 0 end) as repairable
from Bookmark b;
```

On device B: **fragmented 810, repairable 810.** Every one.

**Repair** (device in USB mass-storage mode, Nickel not running): `UPDATE Bookmark SET ContentID`
to the fragment-stripped value, scoped by `VolumeID`. Verify each target has a `ContentType='9'`
row first; hash every other volume's rows before and after to prove isolation. `BookmarkID` is the
PK, so rewriting `ContentID` cannot collide.

🚨 **Ordering, unchanged from the root-cause note:** repairing makes previously-invisible rows
*eligible* for the annotation-download deletion path. **Fix the server first, and confirm the
device's annotation traffic actually reaches it** — see below, that is now a per-device check.

---

## Why the obvious fix is wrong

**Stripping fragments from the NCX is not a fix.** It would collapse The Age of Innocence's 42
navigation targets onto 7 documents, and Dune's 1,780 onto a handful. The fragment is load-bearing
for navigation; you would trade invisible highlights for a broken table of contents.

**Server-side normalisation does not fix rendering either.** Normalising the `content_id` we store
is right for our own records, but the row Nickel draws from lives on the device.

What is left is **splitting spine documents at TOC anchor targets during conversion** — making
books look like the working example above.

⚠️ **Hazard:** regenerating a KEPUB shifts KoboSpan ids, invalidating the anchors of highlights
users already hold. Applying this across ~43% of a live library is a mass re-anchoring event.
Needs a design pass and a story for books that already carry highlights.

---

## Annotation routing is a per-device property, not a server setting

The guard that refuses the annotation download can only protect a device whose annotation traffic
actually reaches the server. That traffic is governed by `reading_services_host`, and:

- **Do not hand-edit it.** Doing so on device A produced `FailedSync {"reason":"WebRequestErr"}` on
  every sync until it was reverted (see #1659).
- **A device adopts the right host by itself when it performs a full `Init`.** Observed directly:
  device A ran syncs for hours with no `Init` line and never adopted it; after a firmware update
  forced an `Init`, it adopted the host on its own and the guard began firing for its books —
  with the config key still holding the stock value.

**So "is this device protected?" is answered by the server log, not by the config**: look for
`Not proxying annotation download for locally-owned book …` naming a book that device holds.

---

## Open: local deletion is not yet explained

On device A, after the guard fired for a book, that book's 3 local `Bookmark` rows were deleted
while 6 rows in another book survived untouched (same row ids, unchanged). All 3 were captured
server-side first, with full text, colour and KoboSpan anchors — so nothing was lost.

**Two explanations fit equally and are confounded here**, because the deleted set and the
unanchored set are the same three rows:

1. the refusal response did not prevent Nickel deleting the local rows;
2. Nickel pruned them for being unanchored, independent of the download.

⚠️ And device B points the **opposite** way: there the *anchored* rows died (87 of 88, after
repair) and the unanchored ones survived. **No single model explains both devices yet. Do not cite
either device as evidence that the guard holds or fails.**

**To separate them:** in one session, highlight both a fragment-TOC book and a clean-TOC book, then
sync once. Only the fragmented book losing rows means pruning; both losing rows means the download
path.

---

## Firmware, and a caution about attributing to it

Both devices above were on 4.45-series firmware at the time of the measurements. An earlier draft
of these findings attributed several of them to a 4.42 build; the device had **self-updated**
mid-session and the version was not re-read before writing up. **Re-read `.kobo/version` at the
moment of measurement rather than relying on an earlier reading** — a Kobo will update itself as
soon as it has Wi-Fi, and "the firmware I checked an hour ago" is not the firmware under test.
