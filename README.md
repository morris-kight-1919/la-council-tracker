# LA Council Tracker — Operator Console

A local, private web page for reviewing Los Angeles City Council activity you're tracking. It runs only on this Mac — nothing here is on the internet.

## Everyday use

**To open it:**

1. Open a Terminal window in this folder (or double-click `start-gui` if your Mac is set up to run it — otherwise see "Using Terminal" below).
2. Type `./start-gui` and press Return.
3. Wait for a line that says `http://127.0.0.1:5055` and open that in your browser.
4. Leave the Terminal window open while you're using the page.

**To close it:** either press `Control-C` in that same Terminal window, or open a *new* Terminal window in this folder and type `./stop-gui`.

**To pull in the latest City data by hand** (this also happens automatically every morning): type `./run-ingest`.

### Using Terminal, if you're not used to it

- Open the Terminal app (Applications → Utilities → Terminal, or search for "Terminal" with Spotlight).
- Type `cd ` (with a space after it), then drag this project's folder into the window, then press Return. That puts you "in" this folder.
- Now `./start-gui`, `./stop-gui`, and `./run-ingest` will work as described above.

## What the GUI is, right now

It shows your real, live tracking data — Today, Inbox, Watchlist, Council File pages, Calendar, Sources, and Activity all read from the same `corpus.db`/`watchlist.json` the CLI pipeline writes to. What to Watch is a read-only view of the current scoring rules (a full editable rule builder is a larger future piece). Two pages aren't built yet — a Council-Files browse/search-all page, and Settings — and show a plain "coming soon" notice instead of an error.

Want to see the layout with harmless placeholder data instead (e.g. to demo it without touching real records)? Set `SEED_MODE=1` in `.env` and restart.

## If something goes wrong

- **"command not found: ./start-gui"** — you're probably not in the right folder. Use `cd` (see above) to get into this project's folder first.
- **A page won't load** — check the Terminal window you ran `./start-gui` in; any error message will appear there.
- **You want a fresh start** — delete the `.venv` folder and run `./start-gui` again; it rebuilds automatically.

---

## Pipeline reference (the underlying data-tracking system, unaffected by the GUI above)

Everything below documents the existing command-line pipeline that actually tracks LA City Council files day to day — the GUI is a viewer/reviewer built on top of it, not a replacement for it. `./run-ingest` (above) is a convenience wrapper around `scripts/run_daily.sh`, described here.

Tracks Los Angeles City Council files as they move through committee to a council vote, using the public LA City Clerk Connect system (`cityclerk.lacity.org/lacityclerkconnect`). Private/local tool — not published anywhere.

### How it works
- `scripts/cf_client.py` — talks to the two City Clerk Connect endpoints directly (no login/session needed): `fa=ccfi.viewrecord` for a single file's full record (title, activity log, linked PDFs, vote tally), and `fa=vcfi.doSearch` for keyword/committee search (up to 500 results, but multi-word phrases return 0 — see note in `config.json`).
- `scripts/discover.py` — runs the searches configured in `config.json` and adds any newly-found council files to `watchlist.json` (never removes/overwrites existing entries).
- `scripts/check_updates.py` — fetches every file in `watchlist.json`, compares its activity log AND its attached-document list against the last-saved snapshot in `data/<cfnumber>.json`. New activity rows and new documents both get logged to `CHANGELOG.md`; new documents are also downloaded and text-extracted automatically (see `documents.py` below).
- `scripts/documents.py` — downloads a file's attached PDFs into `documents/<cfnumber>/`, extracts text via `pdftotext` with an `ocrmypdf` fallback for scanned/image-only pages (same pattern as the court-docket project). Called automatically by `check_updates.py` whenever a new document appears.
- `scripts/run_daily.sh` — runs the full daily pipeline (discover, mail ingestion, document fetching, change-checking, scoring, retention sweep, health check). Safe to run manually anytime (`./run-ingest`), and also runs automatically every morning at 7am via a LaunchAgent (`com.morriskight.la-council-tracker.daily`, installed in `~/Library/LaunchAgents/`).
- `scripts/committees.json` — full committee-code → committee-name reference (92 committees), for filling in `config.json`'s `"committees"` list.

### Meeting transcripts (a second, independent discovery channel)
- `scripts/primegov_client.py` — talks to LA City's PrimeGov meeting portal (`lacity.primegov.com`, a different, richer system than City Clerk Connect): lists meetings by year, and parses each meeting's agenda into items with council-file numbers, full official-recommendation text (already clean, no PDF needed), and a coarse video-timestamp anchor per item.
- `scripts/transcript.py` — pulls each meeting's real YouTube auto-captions via `yt-dlp`, cleans up the rolling-window duplicate lines, and caches clean timestamped text to `transcripts/<meetingId>.json`.
- `scripts/transcript_search.py` / `scripts/broad_search.py` — keyword-search across actual spoken meeting content (catches things said in public comment or by councilmembers that never appear in any written document). Unlike City Clerk's search, this handles multi-word phrases fine (`"Gibson Dunn"`, `"Kahn Scolnick"`) since it's plain substring search over real text, not a flaky third-party search engine. Hits that map to a specific agenda item get auto-added to `watchlist.json`; hits from general public comment (no item anchor) still get printed with a direct timestamped YouTube link for manual review.

**How it all connects**: however a file gets found — manual entry, City Clerk keyword search, or transcript search — it lands in the same `watchlist.json`, and `check_updates.py` tracks it the same way from then on: status changes AND newly-attached documents, both archived locally.

### Usage
```bash
# One-off check of everything currently in watchlist.json + config.json searches
./scripts/run_daily.sh

# Add files manually — just edit watchlist.json:
#   "24-0114": {"title": "...", "added": "2026-08-07", "source": "manual"}

# Look up a specific file directly
python3 scripts/cf_client.py 24-0114

# Search without saving anything
python3 scripts/cf_client.py search "encampment"
```

### Files
- `config.json` — search terms (`keywords`) and committee codes (`committees`) used by `discover.py` to auto-add files
- `watchlist.json` — every file currently being tracked, manual + auto-discovered
- `data/<cfnumber>.json` — last-known-good snapshot of each tracked file, used for diffing
- `CHANGELOG.md` — append-only log of new activity, most recent run first
- `corpus.db` — SQLite+FTS5 database (documents, public comments, transcripts, emails, outbox) that both the CLI tools and the GUI read from

### Other scripts
- `scripts/expiring_soon.py` — flags watched files within 30 days of (or past) their procedural expiration date. Note: many keyword-discovered files turn out to be old, already-closed/expired business — worth periodically pruning the watchlist of anything long-expired to keep daily checks fast and signal-heavy.
- `scripts/recommendation_diff.py` — tracks a file's official recommendation text across meeting appearances (via PrimeGov) and diffs consecutive versions. See `TEXT_DIFFING_EXPLAINED.md` for the full concept.
- `scripts/classify_activity.py` — tags each activity-log line by type/priority (vote, committee action, scheduling, routine) so high-signal events stand out in the changelog.
- `scripts/ai_summarize.py` / `scripts/summarize_pending.py` — one-paragraph AI summaries of archived documents via local Ollama. **Deliberately not wired into the automatic daily run** — it competes for CPU with the Morris Kight transcription job running on this same machine. Run `summarize_pending.py` manually when there's spare capacity.
- `scripts/build_index.py` — builds `index.html`, one searchable local page across every watched file, archived document, and cached transcript.
- `scripts/public_comments.py` — rollup of every archived "Communication(s) from Public" document across the whole tracker, since named individuals/orgs often surface there first.
- `scripts/vote_patterns.py` — aggregates per-councilmember voting records across everything watched; writes `data/vote_patterns.json`.
- `scripts/weekly_digest.py` — condenses the last 7 days of `CHANGELOG.md` into `WEEKLY_DIGEST.md`.
- `scripts/health_check.py` — reads existing logs/timestamps (recomputes nothing) and reports whether the pipeline is actually healthy: last run + exit status per script, feed staleness, and the recommendation-diff match rate. Run this whenever you want to know if something's actually wrong versus just quiet.
- `scripts/eml_ingest.py` / `scripts/fetch_ens_links.py` / `scripts/applemail_ingest.py` — the ENS (City Early Notification System) email channel: ingest exported/backlogged `.eml` files or read directly from Apple Mail, then fetch the documents/agendas those emails point to.
- `scripts/queue_relevance.py` / `scripts/score_corpus.py` — keyword-tiered relevance scoring across the whole corpus, feeding an `outbox` of items worth a human's attention.
- `scripts/send_digest.py` — renders (and can send) a digest of everything queued in the outbox. Sending is currently via Apple Mail/AppleScript and has a known formatting bug — dry-run output works fine.

### Hardening notes (from `City Watch.md`, a lessons-learned document from a prior agent's run at this same problem)

Several of this document's warnings mapped directly onto real weak points here, so:

- **Every fetch now stamps `retrieved_at`** (CF records, documents, transcripts) — a health check needs to know how stale data actually is, not just whether the last HTTP call succeeded.
- **`recommendation_diff.py`'s meeting match is a fuzzy date-window search, not a stable-identifier join** — exactly the failure class the document calls out (a join that silently returns nothing forever, indistinguishable from "no changes"). Every match attempt is now logged to `data/recommendation_match_log.jsonl`, and `health_check.py` flags a low match rate over a meaningful sample.
- **`vote_patterns.py` quarantines rather than resolves ambiguity** — name variants (e.g. "KEVIN DE LEON" vs "KEVIN DELEON") are flagged as a group, never silently merged or left silently split; "(VACANT)" seat rows are excluded from the member table rather than polluting it.
- **Every downloaded PDF is hashed on arrival** (`documents/manifest.json`) — agencies can revoke or change links without notice; work from a local, hashed copy.
- **Every script logs to `run_log.jsonl`** (timestamp, exit status, counts), including failures. `health_check.py` reads that log plus feed staleness and gives a single HEALTHY / NEEDS ATTENTION verdict — the whole point being that "no changes" and "the pipeline is broken" must never look the same.
- **`watch.py`'s re-check path was fixed (2026-08-08)** to diff against the prior snapshot properly instead of silently absorbing anything that changed since the last automated check — see the forensic-audit note in project memory if picking this back up in a future session.

### Notes
- No CourtListener-style API token needed — this site is fully public, plain HTML.
- Be a polite scraper: `check_updates.py` sleeps 1s between files; don't crank up `discover.py` search frequency beyond daily.
- A file's own "Expiration Date" (visible in its record) is when the file dies procedurally if no council action is taken — worth watching for files nearing that date.
