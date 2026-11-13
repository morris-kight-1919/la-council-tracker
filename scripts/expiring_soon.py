#!/usr/bin/env python3
"""Flag watched council files approaching their procedural expiration date
(the date a file dies without council action, per its own record)."""
import datetime
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA_DIR = os.path.join(ROOT, "data")

WARN_WITHIN_DAYS = 30


def main():
    today = datetime.date.today()
    flagged = []

    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".json"):
            continue
        record = json.load(open(os.path.join(DATA_DIR, fname)))
        exp = record.get("expiration")
        if not exp:
            continue
        try:
            exp_date = datetime.datetime.strptime(exp, "%m/%d/%Y").date()
        except ValueError:
            continue

        days_left = (exp_date - today).days
        if days_left <= WARN_WITHIN_DAYS:
            flagged.append((days_left, record["cfnumber"], record["title"], exp))

    flagged.sort()

    if not flagged:
        print(f"No watched files expiring within {WARN_WITHIN_DAYS} days.")
        return

    print(f"Files expiring within {WARN_WITHIN_DAYS} days (or already past expiration):\n")
    for days_left, cf, title, exp in flagged:
        status = "EXPIRED" if days_left < 0 else f"{days_left}d left"
        print(f"  [{status:>10}] {cf} — {title[:80]} (expires {exp})")


if __name__ == "__main__":
    main()
