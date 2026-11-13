#!/usr/bin/env python3
"""One-time (but safe to re-run) backfill: mark pre-existing, already-
baselined outbox rows as decision='backlog' so the GUI's Inbox doesn't open
with 130 items competing for attention on day one.

Deliberately NOT a hide -- 'backlog' stays visible and queryable, sorted
behind genuinely-new items but with fire=1 (real watched-Council-File hits)
surfaced before plain score-threshold noise within the backlog itself. Of
the 130 rows this targets, 107 are fire=1 and were marked sent_at via
send_digest.py --baseline, i.e. marked delivered WITHOUT being sent -- and
since AppleMail sending is still broken, no human has actually seen them
through any channel. Hiding them would bury real matches; this doesn't.

Idempotent: only touches outbox rows with no existing outbox_review row.
"""
import datetime
import json

import db
from run_log import logged_run


def main():
    with logged_run("backfill_outbox_review") as counts:
        conn = db.init()
        rows = conn.execute(
            "SELECT o.id, o.payload_json FROM outbox o "
            "LEFT JOIN outbox_review r ON r.outbox_id = o.id "
            "WHERE o.kind='keyword_hit' AND o.sent_at IS NOT NULL AND r.outbox_id IS NULL"
        ).fetchall()

        now = datetime.datetime.now().isoformat(timespec="seconds")
        n_fire = n_other = 0
        for r in rows:
            fire = json.loads(r["payload_json"] or "{}").get("fire")
            if fire:
                n_fire += 1
            else:
                n_other += 1
            conn.execute(
                "INSERT INTO outbox_review (outbox_id, decision, decided_at, note) "
                "VALUES (?, 'backlog', ?, ?)",
                (r["id"], now,
                 "Backfilled 2026-08-09: pre-existing item baselined before the Inbox "
                 "review queue existed. Deprioritized, not hidden -- see project memory."))
        conn.commit()

        counts.update(backfilled=len(rows), fire=n_fire, other=n_other)
        print(f"Backfilled {len(rows)} outbox row(s) as decision='backlog' "
              f"({n_fire} fire=1 watched-file hits, {n_other} score-only)")
        if not rows:
            print("(nothing to backfill -- already done, or no baselined rows exist)")


if __name__ == "__main__":
    main()
