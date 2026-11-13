#!/usr/bin/env python3
"""Aggregate per-councilmember voting records across every watched council
file with a recorded vote. Refetches live (rather than relying only on
cached snapshots) so it works even for files baselined before per-member
vote parsing was added to cf_client.py."""
import datetime
import json
import os
import time

import re

import db
from cf_client import fetch_record
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")
OUT_PATH = os.path.join(ROOT, "data", "vote_patterns.json")


def _normalize_name(name):
    """For detecting likely-same-person name variants (e.g. 'KEVIN DE LEON'
    vs 'KEVIN DELEON') WITHOUT auto-merging them -- per City Watch.md
    Section IV, ambiguous data gets flagged for a human, never silently
    resolved one way or the other."""
    return re.sub(r"[^A-Z0-9]", "", name.upper())


def is_vacant(name):
    return "VACANT" in name.upper()


def main():
    watchlist = json.load(open(WATCHLIST_PATH))
    member_records = {}  # name -> {yes, no, absent, abstain, other, files: []}
    vacant_count = 0
    conn = db.init()

    with logged_run("vote_patterns") as counts:
        files_with_votes = 0
        fetch_errors = 0

        for cf in watchlist:
            try:
                record = fetch_record(cf)
            except Exception as e:
                print(f"  ERROR {cf}: {e}")
                fetch_errors += 1
                continue
            if record is None or not record.get("vote") or not record["vote"].get("members"):
                continue

            files_with_votes += 1
            retrieved_at = datetime.datetime.now().isoformat(timespec="seconds")

            # Aggregate tally: one entry per FILE per member, using only the
            # most recent vote event (record["vote"], == vote_history[0]) --
            # "how does this member's record on this file currently stand,"
            # matching the original intent. Historical detail (every event)
            # is written to the DB separately below, not folded into this
            # per-file tally, so the summary counts keep their original
            # meaning even though a file can now have multiple events.
            for m in record["vote"]["members"]:
                name = m["name"]
                if is_vacant(name):
                    # Not a real member -- quarantine separately rather than
                    # let "(VACANT)" pollute the per-member table.
                    vacant_count += 1
                    continue

                rec = member_records.setdefault(name, {
                    "council_district": m["council_district"],
                    "yes": 0, "no": 0, "absent": 0, "abstain": 0, "other": 0,
                    "files": [],
                })
                v = m["vote"].upper()
                if v == "YES":
                    rec["yes"] += 1
                elif v == "NO":
                    rec["no"] += 1
                    rec["files"].append({"cf": cf, "vote": "NO", "action": record["vote"]["vote_action"]})
                elif v == "ABSENT":
                    rec["absent"] += 1
                elif "ABSTAIN" in v:
                    rec["abstain"] += 1
                else:
                    rec["other"] += 1

            # Full history: every vote event this file has on record, for
            # on-demand per-member lookup in the GUI (2026-08-09). event_index
            # 0 is the most recent. A re-fetch replaces the same
            # (cfnumber, event_index, member_name) rows -- events don't
            # renumber between runs since the page always lists them
            # most-recent-first.
            for event_index, event in enumerate(record.get("vote_history") or []):
                for m in event["members"]:
                    if is_vacant(m["name"]):
                        continue
                    conn.execute(
                        "INSERT OR REPLACE INTO votes "
                        "(cfnumber, event_index, member_name, council_district, vote, "
                        " vote_action, meeting_date, retrieved_at) VALUES (?,?,?,?,?,?,?,?)",
                        (cf, event_index, m["name"], m["council_district"], m["vote"].upper(),
                         event.get("vote_action"), event.get("meeting_date"), retrieved_at))

            conn.commit()
            time.sleep(0.5)

        # Detect (but do not merge) likely-same-person name variants.
        by_normalized = {}
        for name in member_records:
            by_normalized.setdefault(_normalize_name(name), []).append(name)
        ambiguous_groups = {norm: names for norm, names in by_normalized.items() if len(names) > 1}

        json.dump({
            "members": member_records,
            "ambiguous_name_groups": ambiguous_groups,
            "vacant_seat_votes_excluded": vacant_count,
        }, open(OUT_PATH, "w"), indent=1)

        counts["files_checked"] = len(watchlist)
        counts["files_with_votes"] = files_with_votes
        counts["fetch_errors"] = fetch_errors
        counts["distinct_member_names"] = len(member_records)
        counts["ambiguous_name_groups"] = len(ambiguous_groups)

        print(f"{files_with_votes} watched file(s) had a recorded vote.\n")
        print(f"{'Member':<25} {'Yes':>5} {'No':>5} {'Absent':>7} {'Abstain':>8}")
        for name, rec in sorted(member_records.items()):
            print(f"{name:<25} {rec['yes']:>5} {rec['no']:>5} {rec['absent']:>7} {rec['abstain']:>8}")

        if ambiguous_groups:
            print(f"\nAmbiguous name variants found ({len(ambiguous_groups)} group(s)) -- NOT auto-merged, "
                  f"counts above are kept separate per variant. Verify by hand which are the same person:")
            for norm, names in ambiguous_groups.items():
                print(f"  {names} -- combined would be "
                      f"{sum(member_records[n]['yes'] for n in names)} yes / "
                      f"{sum(member_records[n]['no'] for n in names)} no")

        if vacant_count:
            print(f"\n{vacant_count} vote row(s) were for a vacant seat -- excluded from the member table above.")

        dissenters = {n: r for n, r in member_records.items() if r["no"] > 0}
        if dissenters:
            print("\nNO votes cast (worth a closer look):")
            for name, rec in dissenters.items():
                for f in rec["files"]:
                    print(f"  {name}: NO on {f['cf']} ({f['action']})")


if __name__ == "__main__":
    main()
