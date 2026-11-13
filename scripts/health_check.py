#!/usr/bin/env python3
"""Cheap, on-demand health check. Reads existing logs and timestamps --
recomputes nothing -- and answers the three questions from City Watch.md
Section I: did the scheduler fire and with what exit code, how stale is
each feed (max record timestamp, not last HTTP call), and is the current
silence expected or a sign the pipeline is dead."""
import datetime
import json
import os

from run_log import read_recent
from recommendation_diff import recent_match_rate

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA_DIR = os.path.join(ROOT, "data")
TRANSCRIPT_DIR = os.path.join(ROOT, "transcripts")

# How stale is too stale, per feed. City Clerk / PrimeGov both update on
# roughly a daily cadence when the tracker is run daily -- tune this if the
# actual run cadence changes.
STALENESS_THRESHOLD_HOURS = {
    "check_updates": 48,   # should run ~daily; 2 days of silence is worth a look
    "discover": 7 * 24,    # discovery is lower-frequency by design
}


def last_run_per_script():
    runs = read_recent(500)
    last = {}
    for r in runs:
        script = r["script"]
        if script not in last or r["timestamp"] > last[script]["timestamp"]:
            last[script] = r
    return last


def max_retrieved_at(directory, key_path=None):
    """Newest retrieved_at timestamp found across JSON files in a directory.
    key_path: dotted path to the field if not top-level, e.g. for old-format
    transcript caches this returns None (handled by caller)."""
    newest = None
    if not os.path.isdir(directory):
        return None
    for fname in os.listdir(directory):
        if not fname.endswith(".json"):
            continue
        try:
            data = json.load(open(os.path.join(directory, fname)))
        except (json.JSONDecodeError, OSError):
            continue
        ts = data.get("retrieved_at") if isinstance(data, dict) else None
        if ts and (newest is None or ts > newest):
            newest = ts
    return newest


def hours_since(iso_timestamp):
    if not iso_timestamp:
        return None
    then = datetime.datetime.fromisoformat(iso_timestamp)
    return (datetime.datetime.now() - then).total_seconds() / 3600


def main():
    print("=== LA Council Tracker: Health Check ===\n")

    last_runs = last_run_per_script()
    if not last_runs:
        print("No runs logged yet (run_log.jsonl doesn't exist or is empty).")
        print("This itself is a signal: nothing has ever run, or logging isn't wired up.")
        return

    print("-- Last run per script --")
    overall_ok = True
    for script, run in sorted(last_runs.items()):
        age_h = hours_since(run["timestamp"])
        threshold = STALENESS_THRESHOLD_HOURS.get(script, 48)
        status_flag = "OK"
        if run["status"] != "ok":
            status_flag = "FAILED"
            overall_ok = False
        elif age_h is not None and age_h > threshold:
            status_flag = f"STALE ({age_h:.0f}h since last run, threshold {threshold}h)"
            overall_ok = False
        print(f"  [{status_flag}] {script}: {run['timestamp']} "
              f"({run['status']}, {json.dumps({k: v for k, v in run.items() if k not in ('script', 'status', 'timestamp', 'traceback')})})")
        if run["status"] == "error":
            print(f"      error: {run.get('error')}")

    print("\n-- Feed staleness (max retrieved_at across cached data, not last HTTP call) --")
    cf_max = max_retrieved_at(DATA_DIR)
    transcript_max = max_retrieved_at(TRANSCRIPT_DIR)
    print(f"  City Clerk Connect (data/): newest retrieved_at = {cf_max or 'none recorded'}")
    print(f"  PrimeGov transcripts: newest retrieved_at = {transcript_max or 'none recorded'}")

    print("\n-- Recommendation-text-diff match rate (fuzzy date-window join, City Watch.md Section III) --")
    match_stats = recent_match_rate()
    if match_stats is None:
        print("  No match attempts logged yet.")
    else:
        print(f"  {match_stats['found']}/{match_stats['attempts']} recent attempts found a matching agenda item "
              f"({match_stats['rate']:.0%})")
        if match_stats["attempts"] >= 5 and match_stats["rate"] == 0:
            print("  WARNING: 0% match rate over a meaningful sample. This is exactly the silent-join "
                  "failure pattern from City Watch.md Section III -- worth checking by hand.")
            overall_ok = False

    print(f"\n=== Overall: {'HEALTHY' if overall_ok else 'NEEDS ATTENTION'} ===")
    if not overall_ok:
        print("At least one script has failed or gone stale beyond its threshold. "
              "Per City Watch.md: this is exactly the state that looks identical to "
              "'nothing happened' if you don't check it explicitly.")


if __name__ == "__main__":
    main()
