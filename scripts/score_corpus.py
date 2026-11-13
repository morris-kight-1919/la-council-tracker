#!/usr/bin/env python3
"""Score every email and document with the tiered keyword model and store
the result, so the highest-signal material can be surfaced first.

Per "Keywords — City Watch": suppress, do not delete. Low-value items are
flagged, never removed, because the filter will be re-tuned and you cannot
re-run against messages you threw away.

Usage:
    python3 score_corpus.py                 # score everything
    python3 score_corpus.py --top 25        # show highest scorers
    python3 score_corpus.py --fire          # only 'fire immediately' items
"""
import argparse
import json
import os

import db
import keywords
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_PATH = os.path.join(HERE, "..", "watchlist.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS relevance (
    source_type  TEXT,        -- email | document
    source_id    INTEGER,
    score        INTEGER,
    fire         INTEGER,     -- Tier 0 identifier hit the watch set
    suppressed   INTEGER,
    tiers_json   TEXT,
    identifiers_json TEXT,
    scored_at    TEXT,
    PRIMARY KEY (source_type, source_id)
);
CREATE INDEX IF NOT EXISTS idx_relevance_score ON relevance(score DESC);
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, help="show the N highest-scoring items")
    p.add_argument("--fire", action="store_true", help="show fire-immediately items")
    p.add_argument("--threshold", type=int, default=keywords.DEFAULT_THRESHOLD)
    args = p.parse_args()

    conn = db.init()
    conn.executescript(SCHEMA)
    watched = set(json.load(open(WATCHLIST_PATH)).keys()) if os.path.exists(WATCHLIST_PATH) else set()

    if args.top or args.fire:
        where = "WHERE r.fire=1" if args.fire else ""
        rows = conn.execute(f"""
            SELECT r.source_type, r.source_id, r.score, r.fire, r.suppressed,
                   r.tiers_json, r.identifiers_json,
                   COALESCE(e.subject, d.title) AS label,
                   COALESCE(e.source, d.doc_type) AS kind,
                   d.cfnumber AS cf
            FROM relevance r
            LEFT JOIN emails e    ON r.source_type='email'    AND e.id=r.source_id
            LEFT JOIN documents d ON r.source_type='document' AND d.id=r.source_id
            {where}
            ORDER BY r.score DESC LIMIT ?""", (args.top or 40,)).fetchall()
        print(f"{len(rows)} item(s):\n")
        for r in rows:
            flag = " [FIRE]" if r["fire"] else ""
            supp = " [suppressed]" if r["suppressed"] else ""
            print(f"  {r['score']:>4}{flag}{supp}  [{r['source_type']}/{r['kind']}] "
                  f"{(r['label'] or '')[:66]}")
            tiers = json.loads(r["tiers_json"] or "{}")
            for t, hits in list(tiers.items())[:4]:
                print(f"          {t}: {', '.join(hits[:6])}")
            ids = json.loads(r["identifiers_json"] or "{}")
            if ids.get("watched_council_files"):
                print(f"          WATCHED CF: {ids['watched_council_files']}")
        return

    with logged_run("score_corpus") as counts:
        import datetime
        now = datetime.datetime.now().isoformat(timespec="seconds")
        n = fired = suppressed = above = 0

        for table, stype, textcol, cfcol in (("emails", "email", "body_text", "NULL"),
                                              ("documents", "document", "full_text", "cfnumber")):
            rows = conn.execute(
                f"SELECT id, {textcol} AS t, {cfcol} AS own_cf FROM {table}").fetchall()
            for r in rows:
                s = keywords.score(r["t"], watched_cfs=watched, own_cf=r["own_cf"])
                conn.execute(
                    "INSERT OR REPLACE INTO relevance (source_type, source_id, score, fire, "
                    "suppressed, tiers_json, identifiers_json, scored_at) VALUES (?,?,?,?,?,?,?,?)",
                    (stype, r["id"], s["total"], int(s["fire_immediately"]),
                     int(s["suppress"]), json.dumps(s["tiers"]),
                     json.dumps(s["identifiers"]), now))
                n += 1
                fired += int(s["fire_immediately"])
                suppressed += int(s["suppress"])
                above += int(s["total"] >= args.threshold)
            conn.commit()

        counts.update(scored=n, fire_immediately=fired, suppressed=suppressed,
                      above_threshold=above, threshold=args.threshold)
        print(f"scored {n} item(s)")
        print(f"  fire-immediately (watched identifier hit): {fired}")
        print(f"  above threshold ({args.threshold}): {above}")
        print(f"  suppressed (low value, KEPT not deleted): {suppressed}")
        print("\nSee the best of it:  python3 score_corpus.py --top 20")


if __name__ == "__main__":
    main()
