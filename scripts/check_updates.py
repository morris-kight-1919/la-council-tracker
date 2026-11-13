#!/usr/bin/env python3
"""Fetch every watched council file, diff its activity log against the last
saved snapshot, and append any new activity rows to CHANGELOG.md."""
import datetime
import json
import os
import time

from cf_client import fetch_record
from documents import download_and_extract, is_public_comment
from recommendation_diff import capture_and_diff
from classify_activity import classify
from run_log import logged_run
from stages import derive_stage, detect_transition
from migrate_to_db import ingest_document
import db


def _parse_activity_date(s):
    """Activity dates are MM/DD/YYYY. Comparing them as strings makes
    '12/29/2021' look newer than '08/07/2026' -- and this feeds the staleness
    line in the digest, which is exactly the signal that has to be
    trustworthy. Parse before comparing."""
    try:
        return datetime.datetime.strptime((s or "").strip(), "%m/%d/%Y").date()
    except ValueError:
        return None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")
DATA_DIR = os.path.join(ROOT, "data")
CHANGELOG_PATH = os.path.join(ROOT, "CHANGELOG.md")


def load(path, default):
    if os.path.exists(path):
        return json.load(open(path))
    return default


def activity_key(a):
    return (a["date"], a["activity"])


def doc_key(d):
    return d["url"]


def queue_transition(cf, record, move):
    """Queue a stage change to the outbox. Dedupe on (cf, from, to, date) --
    a stable upstream identity, never a hash of rendered text."""
    conn = db.init()
    key = f"stage:{cf}:{move['from']}->{move['to']}:{move['as_of']}"
    summary = f"{move['from']} → {move['to']}"
    if move.get("hearing_date"):
        summary += f" (hearing {move['hearing_date']})"
    payload = {
        "cfnumber": cf, "title": record.get("title"),
        "from": move["from"], "to": move["to"], "as_of": move["as_of"],
        "detail": move["detail"], "hearing_date": move.get("hearing_date"),
        "url": record.get("url"),
    }
    conn.execute(
        "INSERT OR IGNORE INTO outbox (created_at, kind, cfnumber, priority, summary, "
        "payload_json, dedupe_key) VALUES (?,?,?,?,?,?,?)",
        (datetime.datetime.now().isoformat(timespec="seconds"), "stage_change", cf,
         move["priority"], summary, json.dumps(payload), key))
    conn.commit()


def diff_and_process(cf, record, prior):
    """Diff a freshly-fetched record against a known prior snapshot, ingest
    any new documents, and detect stage transitions (queuing them). Returns
    (fresh_activities, enriched_docs, move) -- caller writes the snapshot
    and any changelog output.

    Shared by check_updates.py's batch run and watch.py's on-demand
    re-check, so re-running watch.py on an already-watched file can no
    longer silently swallow activity or documents that arrived since the
    last check by overwriting the snapshot without ever diffing against it
    (forensic audit finding #2, 2026-08-08)."""
    prior_doc_keys = {doc_key(d) for d in prior.get("documents", [])}
    new_docs = [d for d in record.get("documents", []) if doc_key(d) not in prior_doc_keys]
    enriched_docs = process_new_docs(cf, new_docs) if new_docs else []

    prior_keys = {activity_key(a) for a in prior.get("activities", [])}
    fresh = [a for a in record["activities"] if activity_key(a) not in prior_keys]

    move = detect_transition(prior.get("activities"), record["activities"])
    if move:
        queue_transition(cf, record, move)

    return fresh, enriched_docs, move


def render_entry_block(cf, title, url, fresh, enriched_docs):
    """Markdown for one file's changelog entry -- shared so watch.py's
    on-demand log entries look identical to check_updates.py's batch ones."""
    lines = [f"\n### {cf} — {title}\n({url})\n"]
    for a in fresh:
        category, priority = classify(a["activity"])
        lines.append(f"- **[{priority.upper()}/{category}] {a['date']}** — {a['activity']}")
    for d in enriched_docs:
        tag = " **[PUBLIC COMMENT]**" if is_public_comment(d["title"]) else ""
        lines.append(f"- **New document**{tag}: [{d['title']}]({d['url']}) ({d.get('date', '?')})")
        if d.get("gist"):
            lines.append(f"  > {d['gist']}...")
        elif d["extraction"].get("error"):
            lines.append(f"  (extraction failed: {d['extraction']['error']})")
        if d.get("rec_diff"):
            lines.append(f"  **Recommendation text changed:**\n  ```\n{d['rec_diff']}\n  ```")
    return "\n".join(lines)


def append_changelog(now, header_note, entry_blocks):
    """Prepend a timestamped block (one or more entries) to CHANGELOG.md,
    after its top header."""
    lines = [f"\n## {now}\n\n_{header_note}_\n"]
    lines.extend(entry_blocks)
    block = "\n".join(lines) + "\n"

    existing = ""
    if os.path.exists(CHANGELOG_PATH):
        existing = open(CHANGELOG_PATH).read()
    header = "# LA Council File Changelog\n"
    body = existing[len(header):] if existing.startswith(header) else existing
    with open(CHANGELOG_PATH, "w") as f:
        f.write(header + block + body)


def process_new_docs(cf, new_docs):
    """Download + extract each newly-seen document, return enriched list
    with a short text gist for the changelog. Also attempts a
    recommendation-text diff for hearing-type documents (see
    TEXT_DIFFING_EXPLAINED.md)."""
    enriched = []
    for d in new_docs:
        print(f"    downloading: {d['title']}")
        result = download_and_extract(cf, d)
        gist = None
        if result.get("text"):
            gist = " ".join(result["text"].split())[:280]

        rec_diff = None
        try:
            rec_diff = capture_and_diff(cf, d["title"], d.get("date"))
        except Exception as e:
            print(f"      (recommendation-diff check failed: {e})")

        # Ingest into the corpus. Without this the DB silently drifts from
        # what's on disk -- documents get downloaded and never become
        # searchable, which looks like nothing is wrong.
        try:
            conn = db.init()
            n_doc, n_com = ingest_document(conn, cf, d, result)
            conn.commit()
            if n_doc:
                extra = f", {n_com} comment(s)" if n_com else ""
                print(f"      ingested into corpus{extra}")
            # Text is safely stored; the PDF is disposable.
            if result.get("pdf_path") and os.path.exists(result["pdf_path"]):
                os.remove(result["pdf_path"])
        except Exception as e:
            print(f"      (corpus ingest failed: {e})")

        enriched.append({**d, "extraction": result, "gist": gist, "rec_diff": rec_diff})
    return enriched


def main():
    watchlist = load(WATCHLIST_PATH, {})
    if not watchlist:
        print("Watchlist is empty. Run discover.py or add files manually to watchlist.json.")
        return

    with logged_run("check_updates") as counts:
        os.makedirs(DATA_DIR, exist_ok=True)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        new_entries = []  # (cfnumber, title, [new activities], [new docs w/ gist], url)
        fetch_errors = 0
        most_recent_activity_date = None  # staleness proxy: max activity date actually seen

        for cf, meta in watchlist.items():
            snapshot_path = os.path.join(DATA_DIR, f"{cf}.json")
            prior = load(snapshot_path, None)

            try:
                record = fetch_record(cf)
            except Exception as e:
                print(f"  ERROR fetching {cf}: {e}")
                fetch_errors += 1
                continue

            if record is None:
                print(f"  {cf}: not found (bad number or removed)")
                continue

            if record["activities"]:
                latest = _parse_activity_date(record["activities"][0]["date"])
                if latest and (most_recent_activity_date is None
                               or latest > most_recent_activity_date):
                    most_recent_activity_date = latest

            prior_docs = prior.get("documents", []) if prior else []
            prior_doc_keys = {doc_key(d) for d in prior_docs}
            new_docs = [d for d in record.get("documents", []) if doc_key(d) not in prior_doc_keys]

            if prior is None:
                # First time seeing this file — save baseline, but still archive
                # its current documents locally (that's the point of the archive).
                enriched_docs = process_new_docs(cf, new_docs) if new_docs else []
                print(f"  {cf}: baseline saved ({len(record['activities'])} activities, {len(enriched_docs)} document(s) archived)")
                json.dump(record, open(snapshot_path, "w"), indent=1)
            else:
                fresh, enriched_docs, move = diff_and_process(cf, record, prior)

                if fresh or enriched_docs:
                    new_entries.append((cf, record["title"], fresh, enriched_docs, record["url"]))
                    print(f"  {cf}: {len(fresh)} new activity row(s), {len(enriched_docs)} new document(s)")
                else:
                    print(f"  {cf}: no change")

                # Stage transition -- "where the file is" changed, which is
                # the notification a human actually wants, versus a raw
                # activity line.
                if move:
                    print(f"    STAGE: {move['from']} -> {move['to']}")

                json.dump(record, open(snapshot_path, "w"), indent=1)

            time.sleep(1)  # be polite to the server

        counts["files_checked"] = len(watchlist)
        counts["fetch_errors"] = fetch_errors
        counts["files_changed"] = len(new_entries)
        counts["most_recent_activity_date"] = (most_recent_activity_date.isoformat()
                                               if most_recent_activity_date else None)

        # City Watch.md Section I: silence is ambiguous -- state what the
        # data was current through, not just "nothing happened."
        staleness_note = (f"Newest activity seen across watched files: {most_recent_activity_date}."
                           if most_recent_activity_date else "No activity dates seen this run.")

        if new_entries:
            entry_blocks = [render_entry_block(cf, title, url, fresh, enriched_docs)
                             for cf, title, fresh, enriched_docs, url in new_entries]
            append_changelog(now, staleness_note, entry_blocks)
            total_activities = sum(len(e[2]) for e in new_entries)
            total_docs = sum(len(e[3]) for e in new_entries)
            print(f"\nWrote {total_activities} new activity row(s) and {total_docs} new document(s) to CHANGELOG.md")
        else:
            print(f"\nNo changes since last check. {staleness_note}")

        if fetch_errors:
            print(f"\n{fetch_errors} file(s) failed to fetch this run -- see errors above.")


if __name__ == "__main__":
    main()
