#!/usr/bin/env python3
"""One-time backfill: fetch documents for watched council files that were
baselined before document-tracking existed.

check_updates.py only fetches documents that are NEW since the last snapshot.
Files baselined before that feature was added therefore have their whole
document history invisible to it, permanently -- their snapshot already
records the documents as "seen", so nothing will ever go back for them.
Measured: 61 of 66 watched files had zero documents archived.

Text-only by design: each PDF is downloaded, extracted, stored in the corpus,
and then deleted immediately -- so disk stays flat rather than spiking by
several hundred MB and being swept later. We keep full_text + source_url +
sha256, which is what makes the file disposable (City Clerk onlinedocs URLs
verified stable; a 2013-era document still returns HTTP 200).

Resumable: documents already in the corpus are skipped by source_url.
"""
import argparse
import datetime
import json
import os
import time

import db
from cf_client import fetch_record
from documents import download_and_extract
from migrate_to_db import classify_doc, extract_fields, parse_public_comments
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--keep-pdfs", action="store_true",
                   help="retain PDFs on disk (default deletes after extraction)")
    p.add_argument("--limit", type=int, help="only process N council files")
    args = p.parse_args()

    with logged_run("backfill_documents") as counts:
        conn = db.init()
        watchlist = json.load(open(WATCHLIST_PATH))

        have = {r[0] for r in conn.execute("SELECT source_url FROM documents")}
        cfs = list(watchlist.keys())
        if args.limit:
            cfs = cfs[:args.limit]

        n_new = n_comments = n_skipped = n_failed = 0
        bytes_seen = 0

        for i, cf in enumerate(cfs, 1):
            try:
                record = fetch_record(cf)
            except Exception as e:
                print(f"[{i}/{len(cfs)}] {cf}: fetch error {e}")
                n_failed += 1
                continue
            if record is None:
                continue

            docs = [d for d in record.get("documents", []) if d.get("url") not in have]
            if not docs:
                n_skipped += 1
                continue

            print(f"[{i}/{len(cfs)}] {cf}: {len(docs)} document(s) to fetch", flush=True)

            for d in docs:
                res = download_and_extract(cf, d)
                if res.get("error") or not res.get("text"):
                    n_failed += 1
                    continue
                text = res["text"]
                bytes_seen += res.get("char_count", 0)
                title = d.get("title") or "(untitled)"
                doc_type = classify_doc(title)
                retrieved = res.get("retrieved_at") or datetime.datetime.now().isoformat(timespec="seconds")

                cur = conn.execute(
                    "INSERT OR IGNORE INTO documents "
                    "(cfnumber, doc_type, title, doc_date, source_url, sha256, full_text, "
                    " fields_json, retrieved_at, pdf_path, pdf_deleted, delete_after) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cf, doc_type, title, d.get("date"), d["url"], res.get("sha256"), text,
                     json.dumps(extract_fields(text)), retrieved,
                     res["pdf_path"] if args.keep_pdfs else None,
                     0 if args.keep_pdfs else 1, retrieved))
                if cur.rowcount:
                    n_new += 1
                    doc_id = cur.lastrowid
                    db.index_row(conn, "document", doc_id, cf, title, text,
                                 {"doc_type": doc_type, "url": d["url"], "date": d.get("date")})
                    if doc_type == "public_comment":
                        for c in parse_public_comments(text, cf, d["url"]):
                            c2 = conn.execute(
                                "INSERT OR IGNORE INTO public_comments "
                                "(cfnumber, commenter, submitted_at, body, parse_mode, "
                                " needs_review, source_doc_id, source_url) VALUES (?,?,?,?,?,?,?,?)",
                                (c["cfnumber"], c["commenter"], c["submitted_at"], c["body"],
                                 c["parse_mode"], c["needs_review"], doc_id, d["url"]))
                            if c2.rowcount:
                                n_comments += 1
                                db.index_row(conn, "public_comment", c2.lastrowid, c["cfnumber"],
                                             c["commenter"] or "(attached letter)", c["body"],
                                             {"commenter": c["commenter"],
                                              "submitted_at": c["submitted_at"],
                                              "parse_mode": c["parse_mode"], "url": d["url"]})
                have.add(d["url"])

                # Text is safely in the corpus; the file is disposable.
                if not args.keep_pdfs and res.get("pdf_path") and os.path.exists(res["pdf_path"]):
                    os.remove(res["pdf_path"])

            conn.commit()
            time.sleep(0.5)

        counts.update(files=len(cfs), documents_added=n_new, comments_added=n_comments,
                      files_already_complete=n_skipped, failures=n_failed,
                      text_mb=round(bytes_seen / 1e6, 1))
        print(f"\nBackfill complete: +{n_new} documents, +{n_comments} public comments, "
              f"{bytes_seen/1e6:.1f} MB of text, {n_failed} failure(s)")


if __name__ == "__main__":
    main()
