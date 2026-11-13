# Pipeline Review — against City Watch.md

*Opus review, 2026-08-07. Scope: what should change in the LA Council Tracker, with priority on (a) external-drive storage, (b) email notifications, (c) committee→council stage tracking, (d) full public-comment capture.*

Every claim below was verified against the live pipeline during this review. Numbers are measured, not estimated, unless labelled a projection.

---

## 0. BLOCKING: storage must move to the external drive before anything else

**Assumption for this report: a 5 TB Thunderbolt volume will be attached.** Nothing is mounted at time of writing — `/Volumes/` contains only `Macintosh HD` — so the drive's volume name is the one input still needed.

### Why this is first
Measured footprint today:

| Path | Size | Note |
|---|---|---|
| `documents/` | **649 MB** | 66 watched files, most of them *closed/expired* and no longer growing |
| `transcripts/` | 4.7 MB | ~17 meetings cached |
| `data/` | 472 KB | snapshots + versions |
| `scripts/` + docs | ~350 KB | code — small, belongs on internal disk |

That's **~10 MB per council file**, and the current watchlist is deliberately small and mostly inert. A live watchlist in the low hundreds, accumulating documents daily and back-filling public comment, reaches multiple gigabytes within months and tens of gigabytes over years. Internal disk has 76 GB free and already carries the 118 GB Morris Kight archive. It does not fit, and the failure would arrive gradually rather than announcing itself.

### What 5 TB changes: stop optimising for space, start optimising for reconstructability
At the measured rate, 5 TB is roughly **four orders of magnitude** more than this pipeline will plausibly consume. Capacity stops being a design constraint, which means two policies should flip:

- **Never prune raw material to save space.** Keep every PDF, every transcript, every version, indefinitely. Retire inert files from *polling*, never from *storage* (Section IV: label, don't delete).
- **Stop overwriting snapshots.** `check_updates.py` currently overwrites `data/<cf>.json` on every run, which destroys the record of what the pipeline knew and when. With this much room, write **append-only versioned snapshots** (`data/<cf>/<retrieved_at>.json`) and treat the newest as current. This directly answers Section IX — *"you cannot reconstruct what you knew when"* — and makes questions like *"when did this document first appear?"* answerable from the archive rather than guessed at. It is a small change now and impossible to backfill later.

### The design
A single `DATA_ROOT`, resolved from `config.json` (overridable by a `LA_TRACKER_DATA_ROOT` env var). Every path in every script derives from it — no exceptions, no relative fallbacks. Code stays on the internal disk (small, versionable, trivially re-clonable); all data lives on the external volume.

```
Internal — code only, ~350 KB        External 5 TB volume — all data
~/Desktop/MAX/la-council-tracker/    /Volumes/<DRIVE>/la-council-tracker/
  scripts/                             .tracker_volume_id   <- mount sentinel
  README.md                            documents/           <- 649 MB and growing
  config.json → DATA_ROOT              transcripts/
                                       data/                <- versioned snapshots
                                       outbox.jsonl         <- pending sends
                                       sent_log.jsonl
                                       run_log.jsonl
                                       CHANGELOG.md
                                       index.html
```

Migration is a copy, a hash check of all 133 PDFs against `documents/manifest.json`, then delete-on-internal only after verification passes.

### The mount guard — the part that actually matters
This is a City Watch.md Section I/V problem in disguise. If the drive is unmounted and a script runs anyway, `os.makedirs()` **cheerfully recreates the whole tree on the internal disk**. You then have two partial archives, the health check reports green, and the corruption is silent and cumulative — the exact failure class this whole document warns about.

So the storage layer must:
1. Resolve `DATA_ROOT` from config/env, never a relative fallback.
2. On every run, verify the path exists **and** that it is an actual mount point (not an empty directory left behind by an unmount) — check for a sentinel file written at setup, e.g. `DATA_ROOT/.tracker_volume_id`.
3. **Refuse to run and exit non-zero** if that check fails. Never create the tree. Log the refusal to `run_log.jsonl` so the health check reports `NEEDS ATTENTION` rather than silence.
4. `health_check.py` reports mount status and free space as first-class fields.

**I need one thing from you to finish this: plug in the drive and tell me its volume name** (or just the path you want, e.g. `/Volumes/Archive/la-council-tracker`). Everything else is mechanical, and I'll migrate the existing 649 MB with hash verification against `documents/manifest.json`.

---

## 1. Email notifications — Section VI is the most emphatic passage in the document, and it is right

City Watch.md on this: *"Ours sends on a bare run, and it is a permanent hazard we have to remember around forever. Do not reproduce that mistake — it is trivially avoidable on day one and unfixable later without breaking every caller."*

Since nothing sends today, we get to do this correctly for free. Three rules, in order of importance:

### 1a. The seen-pointer must be split in two — this is an architecture change, not a feature
Right now data capture and "what's new" are **fused**: `check_updates.py` fetches a file, diffs it against `data/<cf>.json`, writes the changelog, then overwrites the snapshot. The snapshot *is* the seen-pointer.

Bolt email onto that as-is and you get the exact failure Section VI describes: the snapshot advances when the fetch succeeds, so **a failed send drops those records permanently and silently.** They are never in a future digest, because as far as the pipeline is concerned they are no longer new.

The fix is an outbox:
- `check_updates.py` writes each change to an append-only `outbox.jsonl` **in addition to** updating the snapshot. Data capture keeps its own pointer and stays fast.
- A separate `send_digest.py` reads unsent outbox entries, renders, sends, and marks them sent **only on confirmed SMTP success**.
- A failed send leaves them pending; the next run picks them up. Nothing is lost.

### 1b. A bare invocation must never send
`send_digest.py` dry-runs by default and prints exactly what it *would* send. Sending requires `--send`. This is one line of code now and impossible to retrofit later without breaking every caller.

### 1c. Log what was sent, not that a send occurred
`sent_log.jsonl` records the stable IDs of every item in each digest, so *"did this item go out on Tuesday?"* is answerable from the log alone. Dedupe on stable upstream identifiers — `(cfnumber, activity_date, activity_text)` and document URL — never on rendered text or a hash of the output.

### 1d. Two corollaries worth building in now
- **The digest states its own coverage.** Every email ends with which sources it drew on and how current each was — we already compute this (`retrieved_at`, staleness). Per Section I, *"No new records"* and *"No new records; permit data current through Aug 1"* are completely different messages.
- **A stale last-sent date is not a fault.** Quiet weeks legitimately leave the pointer alone. `health_check.py` must not alarm on it, or it will cry wolf and get ignored.

---

## 2. Committee → council tracking: the current model is backward-looking, and it shouldn't be

City Watch.md's Preface is the sharpest idea in the document: *"a city watch tool is... an instrument for noticing things earlier than the official notice... Your job is to push visibility to the left of the notice."*

Measured against that, the tracker currently only ever tells you about things **after they happen**, because it reads City Clerk's activity log, which is a record of the past.

### 2a. Add an explicit stage model
`classify_activity.py` tags individual lines but there is no notion of *where a file is*. Add a derived stage per file:

```
INTRODUCED → REFERRED_TO_COMMITTEE → IN_COMMITTEE → COMMITTEE_REPORTED
           → SCHEDULED_FOR_COUNCIL → COUNCIL_VOTED → FINAL / EXPIRED
```

Store the stage plus its transition history on each snapshot. Notify on **transitions**, not on raw activity lines — that alone cuts the noise dramatically, and it makes "which of my files are sitting in committee right now?" a query instead of a manual read.

### 2b. Scan *upcoming* agendas — this is the leftward push, and the plumbing already exists
`primegov_client.list_upcoming_meetings()` is already written and working, and PrimeGov publishes agendas **before** meetings happen. Nothing currently reads them.

A scanner that checks upcoming agendas for watched CF numbers converts the tool from retrospective to prospective:

> **CF 24-0909 is on the Council agenda for Aug 12 — 5 days out.** *(Sepulveda Basin / Encampment Fires)*

That is the notification with actual value, because you can still act on it. "It was adopted last Tuesday" is a filing, not an alert. This is the single highest-value change in this report and it is mostly wiring.

### 2c. Prune the watchlist
`expiring_soon.py` showed **30 of 66 watched files are already past expiration** — dead business that can never change stage. They add fetch time and dilute every digest. Retire them to an `archived` state: keep the data, stop polling. (Do not delete — Section IV.)

---

## 3. Public comment: the archive already contains 8× more than the tooling exposes

This one is quantified and stark. Measured across the 42 archived public-comment documents:

| | Count |
|---|---|
| Individual comments actually inside those PDFs | **328** |
| Distinct commenter names | **315** |
| What `public_comments.py` currently surfaces | **42** (one 300-char excerpt per PDF) |

**22 of the 42 PDFs contain multiple comments each**, and the current code shows only the first ~300 characters of each file. Roughly **87% of the public comment already sitting on disk is invisible to the tooling.** Nothing needs to be re-downloaded; it is a parsing gap, not a collection gap.

### 3a. Parse to structured records
The web-form submissions are highly regular — 39 of 42 documents (93%) — and split cleanly on:

```
Name: <commenter>   Date Submitted: <timestamp>
Council File No: <cf>   Comments for Public Posting: <body>
```

Emit one row per comment: `cfnumber, commenter, submitted_at, body, source_pdf, sha256`. That immediately supports the questions you actually want to ask — *who comments repeatedly, which organizations show up, what's the volume on a given file, does the same lobbyist appear across files.* The Manatt letter on behalf of an oil company already surfaced in a spot check; that pattern is exactly what per-commenter aggregation finds systematically.

### 3b. Handle the other 7% honestly, per Section IV
3 of 42 are **attached letters** with no form structure (typically the organizational/legal ones — often the most substantive). Do not force them through the same parser. Flag them `parse_mode: "letter"` with a `needs_review` marker, keep the full text, and exclude them from per-commenter counts rather than fabricating a commenter name.

### 3c. Note on sourcing
I checked whether a richer machine-readable source exists: `cityclerk.lacity.org/publiccomment/?cfnumber=…` returns **a submission form, not a read API** (HTTP 200, "Public Comment Form"). The PDF attachments are the only route. Current collection method is correct.

---

## 4. Two real defects found during this review

### 4a. The transcript→agenda-item join is confidently wrong, and has already corrupted the watchlist
**Severity: high.** This is a textbook Section III failure — *"Ambiguous matches are worse than no match, because they are confidently wrong."*

`transcript_search.nearest_item()` attributes a keyword hit to the nearest *preceding* `video_seconds` anchor. Measured anchor granularity:

| Meeting | Agenda items | Distinct video anchors |
|---|---|---|
| 17750 | 2 | 1 |
| 18309 | 54 | 20 |
| 18006 | 31 | 20 |

Anchors are coarse and cluster seconds apart — they mark when items were *called*, not the span of discussion. So any hit during a long general-comment period gets attributed to whatever item was called before it.

Verified instance, meeting 18309 @ 8459s. The transcript reads:

> *"…we urge the council to please execute the four contracts such that Los Angeles **LAFLA**, the Los legal aid council of Los Angeles can receive their funding."*
> *"Okay. Next speaker."* — *"I'd like to speak to item 17."* — *"Okay. So, you have one minute for **general**."*

That is unambiguously the **general public comment period**. The pipeline attributed it to **CF 17-0447-S2 — the oil and gas drilling ordinance** — and auto-added that file to the watchlist, where it remains, and where it pulled down a large share of that 649 MB.

**Fix:** require corroboration before attributing — the CF number or a distinctive title term must also appear near the hit — otherwise record the hit as `unattributed` with its timestamped YouTube link (still useful) and **never auto-add to the watchlist on an anchor match alone.** Section III's rule: assert on joins, treat ambiguity as failure.

### 4b. `None` titles propagate into the watchlist
`broad_search` logged `-> likely CF 13-0005-S638: None`, and that entry is now in `watchlist.json` with `title: None`, which crashes any consumer doing `title[:70]` (it threw during this review). The agenda parser returns `None` for items whose markup doesn't match, and nothing validates before writing. **Fix:** validate before insert; fall back to the CF record's own title; never write a null title.

---

## 5. What should *not* change

Worth stating plainly so effort goes where it's needed:

- `cf_client.py` record parsing — accurate, including per-member roll calls.
- `documents.py` download/extract/hash — clean; 133/133 PDFs extracted with zero failures, native text throughout, no OCR needed. Section VII's table-OCR machinery is genuinely not needed here (these are prose documents). Revisit only if budget tables start appearing.
- `run_log.py` / `health_check.py` — sound; extend with mount status and free space, don't rebuild.
- `recommendation_diff.py` — the fuzzy join is now logged and monitored, which is the right posture for a match that can't use a stable identifier.
- `classify_activity.py`, `expiring_soon.py`, `weekly_digest.py` — fine as building blocks; they get *consumed* by the stage model and digest rather than replaced.

---

## Recommended build order

1. **Storage layer + mount guard + migration.** Blocks everything; the archive grows every run it's deferred. *Needs the drive name from you.*
2. **Fix 4a and 4b.** Small, and 4a is actively polluting the watchlist and wasting disk.
3. **Public-comment parser.** Highest value per unit of work — 328 records already paid for and sitting on disk.
4. **Outbox + `send_digest.py --send`.** Correct send semantics established before the first email ever goes out.
5. **Stage model + upcoming-agenda scanner.** Turns the tool prospective; the email from step 4 becomes genuinely actionable.
6. **Watchlist pruning.** Cheap, and makes every digest sharper.

Steps 2–6 are all straightforward once step 1 exists. Step 1 needs a mounted drive.
