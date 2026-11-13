#!/usr/bin/env python3
"""Add council files to the watchlist by URL or number, and pull everything
for them immediately.

    python3 watch.py https://cityclerk.lacity.org/lacityclerkconnect/index.cfm?fa=ccfi.viewrecord&cfnumber=26-0386
    python3 watch.py 26-0386
    python3 watch.py 26-0386 24-0909 20-0263
    python3 watch.py --list
    python3 watch.py --remove 26-0386

Paste a URL straight from the browser -- quoting is handled, and a bare
number works too. Adding a file fetches its record, archives and ingests its
documents (text into the corpus, PDF discarded), parses any public comments,
and reports its current lifecycle stage.
"""
import argparse
import datetime
import json
import os
import re
import sys

import db
from cf_client import fetch_record
from check_updates import append_changelog, diff_and_process, render_entry_block
from documents import download_and_extract
from migrate_to_db import ingest_document
from run_log import logged_run
from stages import derive_stage

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")

CF_IN_URL = re.compile(r"cfnumber=([0-9]{2}-[0-9]{4}(?:-S[0-9]+)?)", re.I)
CF_BARE = re.compile(r"^([0-9]{2}-[0-9]{4}(?:-S[0-9]+)?)$")


def parse_cf(token):
    """Accept a full viewrecord URL or a bare council-file number."""
    token = token.strip().strip('"').strip("'")
    m = CF_IN_URL.search(token)
    if m:
        return m.group(1).upper()
    m = CF_BARE.match(token)
    if m:
        return m.group(1).upper()
    # last resort: any CF-shaped token in the string
    m = re.search(r"\b([0-9]{2}-[0-9]{4}(?:-S[0-9]+)?)\b", token)
    return m.group(1).upper() if m else None


def load_watchlist():
    if os.path.exists(WATCHLIST_PATH):
        return json.load(open(WATCHLIST_PATH))
    return {}


def save_watchlist(wl):
    json.dump(wl, open(WATCHLIST_PATH, "w"), indent=1)


def add(cf, fetch_docs=True):
    wl = load_watchlist()
    already = cf in wl

    record = fetch_record(cf)
    if record is None:
        print(f"  {cf}: NOT FOUND — check the number/URL. Nothing added.")
        return False

    title = record.get("title") or f"(untitled council file {cf})"
    if not already:
        wl[cf] = {"title": title,
                  "added": datetime.date.today().isoformat(),
                  "source": "manual"}
        save_watchlist(wl)

    stage = derive_stage(record.get("activities") or [])
    print(f"\n  {'ALREADY WATCHED' if already else 'ADDED'}  {cf}")
    print(f"    {title}")
    print(f"    stage: {stage['stage']}" + (f"  (as of {stage['as_of']})" if stage["as_of"] else ""))
    if stage.get("detail"):
        print(f"    latest: {stage['detail'][:110]}")
    if record.get("council_district"):
        print(f"    council district: {record['council_district']}")
    if record.get("expiration"):
        print(f"    expires: {record['expiration']}")
    if record.get("vote"):
        v = record["vote"]
        print(f"    last vote: {v.get('vote_action')} {v.get('vote_given')} on {v.get('meeting_date')}")

    docs = record.get("documents") or []
    print(f"    documents on record: {len(docs)}")

    snap_dir = os.path.join(ROOT, "data")
    os.makedirs(snap_dir, exist_ok=True)
    snapshot_path = os.path.join(snap_dir, f"{cf}.json")
    prior = json.load(open(snapshot_path)) if os.path.exists(snapshot_path) else None

    if prior is not None:
        # Already has a real baseline -- diff against it and log exactly
        # like check_updates.py, instead of just overwriting the snapshot.
        # Re-running watch.py on an already-watched file used to silently
        # swallow any activity/documents that arrived since the last
        # automated check: the snapshot got overwritten with no diff, no
        # CHANGELOG entry, and no stage-transition check, so the next
        # check_updates.py run would find "no change" against a baseline
        # that had quietly absorbed it (forensic audit finding #2, 2026-08-08).
        if fetch_docs:
            fresh, enriched_docs, move = diff_and_process(cf, record, prior)
        else:
            fresh, enriched_docs, move = [], [], None
        if fresh or enriched_docs:
            block = render_entry_block(cf, title, record["url"], fresh, enriched_docs)
            append_changelog(datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                              "Recorded via manual watch.py re-check.", [block])
            print(f"    {len(fresh)} new activity row(s), {len(enriched_docs)} new "
                  f"document(s) -- logged to CHANGELOG.md")
        else:
            print("    no change since last check")
        if move:
            print(f"    STAGE: {move['from']} -> {move['to']}")
    elif fetch_docs and docs:
        # First time this file has a baseline -- archive everything on
        # record now (matches check_updates.py's own first-seen behaviour;
        # nothing to diff against yet, so no changelog entry).
        conn = db.init()
        have = {r[0] for r in conn.execute("SELECT source_url FROM documents")}
        todo = [d for d in docs if d.get("url") and d["url"] not in have]
        n_doc = n_com = 0
        for d in todo:
            res = download_and_extract(cf, d)
            if res.get("error") or not res.get("text"):
                continue
            a, b = ingest_document(conn, cf, d, res)
            n_doc += a
            n_com += b
            # text is in the corpus; the file is disposable
            if res.get("pdf_path") and os.path.exists(res["pdf_path"]):
                os.remove(res["pdf_path"])
        conn.commit()
        print(f"    ingested: {n_doc} new document(s)"
              + (f", {n_com} public comment(s)" if n_com else "")
              + (f", {len(docs) - len(todo)} already had" if len(todo) < len(docs) else ""))

    json.dump(record, open(snapshot_path, "w"), indent=1)
    return True


def remove(cf):
    """Stop watching a file. Its documents/text stay in the corpus -- only
    the watchlist.json entry goes away. Returns True if it was removed,
    False if it wasn't being watched. Shared by the CLI (--remove) and the
    GUI's watch-toggle so there's one implementation, not two."""
    wl = load_watchlist()
    if cf not in wl:
        return False
    wl.pop(cf)
    save_watchlist(wl)
    return True


def main():
    p = argparse.ArgumentParser(description="Watch council files by URL or number.")
    p.add_argument("targets", nargs="*", help="viewrecord URLs and/or CF numbers")
    p.add_argument("--no-docs", action="store_true", help="add without fetching documents")
    p.add_argument("--list", action="store_true", help="show the watchlist")
    p.add_argument("--remove", metavar="CF", help="stop watching a file (data is kept)")
    args = p.parse_args()

    if args.list:
        wl = load_watchlist()
        print(f"{len(wl)} file(s) watched:\n")
        for cf, meta in sorted(wl.items()):
            print(f"  {cf:<15} {(meta.get('title') or '')[:76]}")
            print(f"  {'':<15} added {meta.get('added')} · {meta.get('source')}")
        return

    if args.remove:
        cf = parse_cf(args.remove)
        if remove(cf):
            print(f"Stopped watching {cf}. Its documents and text remain in the corpus.")
        else:
            print(f"{cf} isn't in the watchlist.")
        return

    if not args.targets:
        p.print_help()
        return

    with logged_run("watch") as counts:
        added = bad = 0
        for token in args.targets:
            cf = parse_cf(token)
            if not cf:
                print(f"  couldn't find a council file number in: {token[:70]}")
                bad += 1
                continue
            if add(cf, fetch_docs=not args.no_docs):
                added += 1
        counts.update(requested=len(args.targets), added=added, unparseable=bad)
        print(f"\nWatchlist now {len(load_watchlist())} file(s).")


if __name__ == "__main__":
    main()
