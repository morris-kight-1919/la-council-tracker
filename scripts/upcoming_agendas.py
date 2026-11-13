#!/usr/bin/env python3
"""Scan UPCOMING meeting agendas for watched council files and queue alerts.

This is the piece that makes the tracker prospective instead of archival.
Everything else reads City Clerk's activity log, which is a record of what
already happened -- so every notification is a filing, not an alert. PrimeGov
publishes agendas *before* meetings, and nothing was reading them.

City Watch.md's framing note is the argument for this:

    "a city watch tool is not a scraper with a cron job. It is an instrument
     for noticing things earlier than the official notice... Everything
     valuable happens upstream of it... Your job is to push visibility to the
     left of the notice."

Lead time is short. California's Brown Act sets 72 hours for regular meetings
(24 for special ones), and agendas typically post near that floor -- so a
daily run is the minimum useful cadence. A check run "when you think of it"
will miss windows routinely.

Alerts go to the outbox (not straight to email), so the send semantics in
send_digest.py apply: nothing sends without --send, and the seen-pointer only
advances on a confirmed send.

Usage:
    python3 upcoming_agendas.py            # scan and queue
    python3 upcoming_agendas.py --dry-run  # show matches, queue nothing
"""
import argparse
import datetime
import json
import os
import re

import db
from primegov_client import list_upcoming_meetings, list_meetings_for_year, fetch_agenda
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")

# A meeting that already happened isn't an alert. Small negative tolerance
# covers same-day runs where the meeting is later today.
MIN_DAYS_OUT = -0.5


def is_sap(m):
    return (m.get("title") or "").strip().endswith("- SAP")


def meeting_datetime(m):
    raw = m.get("dateTime")
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw)
    except ValueError:
        return None


def upcoming(days_ahead=21):
    """Upcoming meetings from the live endpoint, plus any future-dated
    meetings in this year's list (the upcoming endpoint is a short window)."""
    seen, out = set(), []
    now = datetime.datetime.now()
    horizon = now + datetime.timedelta(days=days_ahead)

    for source in (list_upcoming_meetings(), list_meetings_for_year(now.year)):
        for m in source:
            if is_sap(m) or m["id"] in seen:
                continue
            dt = meeting_datetime(m)
            if not dt or dt > horizon:
                continue
            if (dt - now).total_seconds() / 86400 < MIN_DAYS_OUT:
                continue
            seen.add(m["id"])
            out.append((dt, m))
    return sorted(out, key=lambda x: x[0])


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="print matches, queue nothing")
    p.add_argument("--days", type=int, default=21, help="how far ahead to look")
    args = p.parse_args()

    with logged_run("upcoming_agendas") as counts:
        conn = db.init()
        watchlist = json.load(open(WATCHLIST_PATH))
        meetings = upcoming(args.days)
        print(f"{len(meetings)} upcoming meeting(s) within {args.days} days\n")

        now = datetime.datetime.now()
        queued = matched = agendas_read = 0

        for dt, m in meetings:
            try:
                items = fetch_agenda(m)
            except Exception as e:
                print(f"  {m.get('date')} {m.get('title','')[:44]}: agenda fetch failed ({e})")
                continue
            agendas_read += 1
            if not items:
                continue

            hits = [it for it in items if it.get("cfnumber") in watchlist]
            if not hits:
                continue

            days_out = (dt - now).total_seconds() / 86400
            when = "TODAY" if days_out < 1 else f"in {days_out:.0f} day(s)"
            print(f"  {m.get('date')} — {m.get('title','')[:52]}  [{when}]")

            for it in hits:
                cf = it["cfnumber"]
                matched += 1
                title = it.get("title") or watchlist[cf].get("title") or ""
                print(f"      CF {cf}: {title[:80]}")

                if args.dry_run:
                    continue

                # Stable dedupe key -- re-running must not re-queue the same
                # hearing. Never a hash of rendered text (City Watch.md VI).
                key = f"hearing:{cf}:{m['id']}"
                summary = (f"On the agenda for {m.get('title')} — {m.get('date')} "
                           f"({when})")
                payload = {
                    "cfnumber": cf, "meeting_id": m["id"], "meeting_title": m.get("title"),
                    "meeting_date": m.get("date"), "days_out": round(days_out, 1),
                    "item_title": title, "recommendation": it.get("recommendation"),
                    "url": f"https://cityclerk.lacity.org/lacityclerkconnect/"
                           f"index.cfm?fa=ccfi.viewrecord&cfnumber={cf}",
                }
                cur = conn.execute(
                    "INSERT OR IGNORE INTO outbox (created_at, kind, cfnumber, priority, "
                    "summary, payload_json, dedupe_key) VALUES (?,?,?,?,?,?,?)",
                    (now.isoformat(timespec="seconds"), "hearing_alert", cf,
                     "high" if days_out <= 7 else "medium",
                     summary, json.dumps(payload), key))
                if cur.rowcount:
                    queued += 1
        conn.commit()

        counts.update(meetings_scanned=len(meetings), agendas_read=agendas_read,
                      matches=matched, queued=queued)
        print(f"\n{matched} watched file(s) on upcoming agendas; {queued} new alert(s) queued.")
        if args.dry_run:
            print("(dry run — nothing queued)")
        elif queued:
            print("Run send_digest.py to see them; --send to deliver.")


if __name__ == "__main__":
    main()
