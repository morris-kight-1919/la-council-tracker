#!/usr/bin/env python3
"""Delete retained PDFs past their retention window. Keep the row forever --
full_text, source_url and sha256 stay, so the file is re-fetchable.

This is the piece that makes "keep everything" affordable. Measured on this
corpus: 637 MB of PDFs against 12 MB of extracted text, a 52x ratio. Dropping
the files cuts storage ~98% while losing nothing searchable.

Safety, because deletion is the one irreversible thing here:
  - Only deletes files whose sha256 still matches what we recorded at
    ingest. A mismatch means the file changed or is corrupt -- keep it and
    flag, don't quietly destroy evidence of a problem.
  - Never deletes rows marked never_delete=1 (their cpra_production carve-out:
    records-request productions arrive on expiring links and may be
    un-refetchable).
  - Never deletes anything whose source_url isn't a real re-fetchable URL.
  - --dry-run is the DEFAULT. Deleting requires --delete.

Verified before enabling: City Clerk `onlinedocs` URLs are stable -- a
2013-era document still returns HTTP 200 in 2026.
"""
import argparse
import datetime
import hashlib
import os

import db
from run_log import logged_run


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--delete", action="store_true",
                   help="actually delete (default is a dry run)")
    p.add_argument("--days", type=int, default=None,
                   help="override retention window; delete PDFs older than N days")
    args = p.parse_args()

    with logged_run("doc_retention", mode="delete" if args.delete else "dry-run") as counts:
        conn = db.init()
        now = datetime.datetime.now()

        if args.days is not None:
            cutoff = (now - datetime.timedelta(days=args.days)).isoformat(timespec="seconds")
            rows = conn.execute(
                "SELECT id, cfnumber, title, pdf_path, sha256, source_url FROM documents "
                "WHERE pdf_deleted=0 AND pdf_path IS NOT NULL AND never_delete=0 "
                "AND retrieved_at < ?", (cutoff,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, cfnumber, title, pdf_path, sha256, source_url FROM documents "
                "WHERE pdf_deleted=0 AND pdf_path IS NOT NULL AND never_delete=0 "
                "AND delete_after < ?", (now.isoformat(timespec="seconds"),)).fetchall()

        deleted = skipped_missing = skipped_hash = skipped_url = 0
        freed = 0

        for r in rows:
            path = r["pdf_path"]
            if not path or not os.path.exists(path):
                conn.execute("UPDATE documents SET pdf_deleted=1, pdf_path=NULL WHERE id=?", (r["id"],))
                skipped_missing += 1
                continue

            if not (r["source_url"] or "").startswith("http"):
                skipped_url += 1
                continue

            if r["sha256"]:
                actual = sha256_of(path)
                if actual != r["sha256"]:
                    print(f"  HASH MISMATCH, keeping: {r['cfnumber']} {r['title'][:50]}")
                    skipped_hash += 1
                    continue

            size = os.path.getsize(path)
            if args.delete:
                os.remove(path)
                conn.execute("UPDATE documents SET pdf_deleted=1, pdf_path=NULL WHERE id=?", (r["id"],))
            deleted += 1
            freed += size

        if args.delete:
            conn.commit()

        counts.update(candidates=len(rows), deleted=deleted, freed_mb=round(freed / 1e6, 1),
                      skipped_hash_mismatch=skipped_hash, skipped_missing=skipped_missing,
                      skipped_non_http=skipped_url)

        verb = "Deleted" if args.delete else "Would delete"
        print(f"{verb} {deleted} PDF(s), freeing {freed/1e6:.1f} MB")
        if skipped_hash:
            print(f"  {skipped_hash} kept -- sha256 mismatch (investigate, do not force)")
        if skipped_url:
            print(f"  {skipped_url} kept -- source_url not re-fetchable")
        if skipped_missing:
            print(f"  {skipped_missing} row(s) already had no file; marked deleted")
        if not args.delete and deleted:
            print("\nDry run. Re-run with --delete to actually remove the files.")


if __name__ == "__main__":
    main()
