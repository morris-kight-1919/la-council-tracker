#!/usr/bin/env python3
"""Bulk-fetch YouTube auto-caption transcripts for every LA City meeting that
has video -- full Council AND all committees.

Committee meetings are where the substantive discussion usually happens
(full Council often adopts committee reports with little debate), and until
now nothing fetched them: the earlier keyword searches filtered on
title == 'City Council Meeting', which excluded ~367 committee meetings.

Resumable: skips meetings already cached in transcripts/. Safe to re-run.

Usage:
    python3 fetch_all_transcripts.py 2026
    python3 fetch_all_transcripts.py 2026 --committees-only
"""
import sys
import time

from primegov_client import list_meetings_for_year
from transcript import fetch_transcript
from run_log import logged_run

# "- SAP" meetings are the Secondary Audio Program (Spanish-language) track of
# a meeting that also exists under its plain title -- same proceeding, second
# audio feed. Skip for English keyword search; they'd double the work and
# duplicate every hit.
def is_sap(meeting):
    return (meeting.get("title") or "").strip().endswith("- SAP")


def is_full_council(meeting):
    return (meeting.get("title") or "").strip() == "City Council Meeting"


def main():
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    committees_only = "--committees-only" in sys.argv

    with logged_run("fetch_all_transcripts", year=year) as counts:
        meetings = list_meetings_for_year(year)

        targets = [m for m in meetings if m.get("videoUrl") and not is_sap(m)]
        if committees_only:
            targets = [m for m in targets if not is_full_council(m)]
        targets.sort(key=lambda m: m.get("dateTime") or "")

        print(f"{len(targets)} meeting(s) with video in {year} "
              f"(SAP duplicates excluded{', committees only' if committees_only else ''})\n")

        fetched = cached = no_captions = failed = 0

        for i, m in enumerate(targets, 1):
            title = (m.get("title") or "?")[:52]
            label = f"[{i}/{len(targets)}] {m.get('date','?')} {title}"
            try:
                before = time.time()
                segs = fetch_transcript(m["id"], m["videoUrl"])
                elapsed = time.time() - before

                if not segs:
                    no_captions += 1
                    print(f"{label} -- no captions available")
                elif elapsed < 0.5:
                    cached += 1  # returned from cache, no network round-trip
                    print(f"{label} -- cached ({len(segs)} segs)")
                else:
                    fetched += 1
                    print(f"{label} -- fetched {len(segs)} segs", flush=True)
                    time.sleep(2)  # be polite; only pause on real fetches
            except Exception as e:
                failed += 1
                print(f"{label} -- FAILED: {e}", flush=True)

        counts.update(meetings_targeted=len(targets), fetched=fetched,
                      already_cached=cached, no_captions=no_captions, failed=failed)

        print(f"\n=== {year}: fetched={fetched} cached={cached} "
              f"no_captions={no_captions} failed={failed} ===")


if __name__ == "__main__":
    main()
