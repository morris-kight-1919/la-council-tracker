#!/usr/bin/env python3
"""Turn relevance scores into digest items.

Without this the scoring engine is decorative: score_corpus.py flagged 110
fire-immediately items and not one of them reached the outbox. That is the
same failure the source documents keep pointing at -- a corpus you cannot
search is a corpus you will not use; a score nobody reads is a score that
does not exist.

What gets queued, and why only these:
  * fire=1  -- a Tier 0 identifier for something on the watch list turned up
               in material that was NOT collected under that file. That is
               genuinely news: someone else's paperwork is talking about your
               file.
  * score >= HIGH_THRESHOLD -- strong keyword signal even with no watched
               identifier. This is the "highest-value notices contain none of
               the obvious words" case.

Suppressed items are never queued, but they stay in the database.

Usage:
    python3 queue_relevance.py --dry-run
    python3 queue_relevance.py
"""
import argparse
import datetime
import json
import os

import db
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))

# Tuned against the current corpus: max score is 196, median far below.
# The source document's advice is to tune this against a week of real traffic
# with a human reading everything above zero -- treat this as a starting point.
HIGH_THRESHOLD = 60


def label_for(conn, source_type, source_id):
    if source_type == "email":
        r = conn.execute("SELECT subject, source, received_at FROM emails WHERE id=?",
                         (source_id,)).fetchone()
        if r:
            return r["subject"], r["source"], r["received_at"], None
    else:
        r = conn.execute("SELECT title, doc_type, doc_date, cfnumber, source_url "
                         "FROM documents WHERE id=?", (source_id,)).fetchone()
        if r:
            return r["title"], r["doc_type"], r["doc_date"], r["source_url"]
    return None, None, None, None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--threshold", type=int, default=HIGH_THRESHOLD)
    args = p.parse_args()

    with logged_run("queue_relevance", threshold=args.threshold) as counts:
        conn = db.init()

        # An ENS email and the document it links to are the SAME content --
        # fetch_ens_links stores the linked page separately, so both score and
        # both would queue ("...referenced in referral_memo" immediately
        # followed by "...in ens_linked_referral_memo"). Keep the email, which
        # carries the arrival date and subject, and skip its linked documents.
        linked_urls = set()
        for r in conn.execute("SELECT links_json FROM emails"):
            linked_urls.update(json.loads(r["links_json"] or "[]"))
        dup_doc_ids = set()
        if linked_urls:
            qmarks = ",".join("?" * len(linked_urls))
            for r in conn.execute(
                    f"SELECT id FROM documents WHERE source_url IN ({qmarks})",
                    tuple(linked_urls)):
                dup_doc_ids.add(r["id"])

        rows = conn.execute(
            "SELECT * FROM relevance WHERE suppressed=0 AND (fire=1 OR score >= ?) "
            "ORDER BY fire DESC, score DESC", (args.threshold,)).fetchall()
        before = len(rows)
        rows = [r for r in rows
                if not (r["source_type"] == "document" and r["source_id"] in dup_doc_ids)]
        if before != len(rows):
            print(f"  ({before - len(rows)} linked-document duplicate(s) of ingested emails skipped)")

        print(f"{len(rows)} item(s) qualify (fire, or score >= {args.threshold})\n")
        queued = 0
        now = datetime.datetime.now().isoformat(timespec="seconds")

        for r in rows:
            label, kind, when, url = label_for(conn, r["source_type"], r["source_id"])
            if not label:
                continue

            ids = json.loads(r["identifiers_json"] or "{}")
            tiers = json.loads(r["tiers_json"] or "{}")
            watched = ids.get("watched_council_files") or []

            # Stable dedupe key on upstream identity, not rendered text.
            key = f"relevance:{r['source_type']}:{r['source_id']}"
            if r["fire"]:
                summary = (f"Watched file {', '.join(watched)} referenced in "
                           f"{kind or r['source_type']}: {label[:70]}")
                priority = "high"
            else:
                top = ", ".join(list(tiers)[:3])
                summary = f"High keyword signal (score {r['score']}, {top}): {label[:70]}"
                priority = "medium"

            if args.dry_run:
                print(f"  [{'FIRE' if r['fire'] else r['score']}] {summary[:100]}")
                continue

            cur = conn.execute(
                "INSERT OR IGNORE INTO outbox (created_at, kind, cfnumber, priority, "
                "summary, payload_json, dedupe_key) VALUES (?,?,?,?,?,?,?)",
                (now, "keyword_hit", (watched[0] if watched else None), priority, summary,
                 json.dumps({"score": r["score"], "fire": bool(r["fire"]),
                             "tiers": tiers, "identifiers": ids,
                             "label": label, "kind": kind, "date": when, "url": url}),
                 key))
            if cur.rowcount:
                queued += 1
        conn.commit()

        counts.update(qualifying=len(rows), queued=queued)
        print(f"\n{queued} new item(s) queued to the outbox.")
        if args.dry_run:
            print("(dry run — nothing queued)")
        else:
            print("Review: python3 send_digest.py     Deliver: send_digest.py --send")


if __name__ == "__main__":
    main()
