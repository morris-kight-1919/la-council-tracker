#!/usr/bin/env python3
"""Run configured keyword/committee searches and add newly-found council
files to watchlist.json. Never removes or overwrites existing entries."""
import datetime
import json
import os

from cf_client import search
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
CONFIG_PATH = os.path.join(ROOT, "config.json")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")


def load(path, default):
    if os.path.exists(path):
        return json.load(open(path))
    return default


def main():
    config = load(CONFIG_PATH, {"keywords": [], "committees": []})
    watchlist = load(WATCHLIST_PATH, {})

    with logged_run("discover") as counts:
        today = datetime.date.today().isoformat()
        added = []
        searches_run = 0
        zero_result_searches = []  # City Watch.md III: a search that always returns 0 is worth knowing about

        for kw in config.get("keywords", []):
            print(f"Searching keyword: {kw!r}")
            results = search(keyword=kw)
            print(f"  {len(results)} results")
            searches_run += 1
            if len(results) == 0:
                zero_result_searches.append(f"keyword:{kw}")
            for r in results:
                cf = r["cfnumber"]
                if cf not in watchlist:
                    watchlist[cf] = {
                        "title": r["title"],
                        "added": today,
                        "source": f"keyword:{kw}",
                    }
                    added.append(cf)

        for code in config.get("committees", []):
            print(f"Searching committee code: {code!r}")
            results = search(committee_code=code)
            print(f"  {len(results)} results")
            searches_run += 1
            if len(results) == 0:
                zero_result_searches.append(f"committee:{code}")
            for r in results:
                cf = r["cfnumber"]
                if cf not in watchlist:
                    watchlist[cf] = {
                        "title": r["title"],
                        "added": today,
                        "source": f"committee:{code}",
                    }
                    added.append(cf)

        json.dump(watchlist, open(WATCHLIST_PATH, "w"), indent=1)

        counts["searches_run"] = searches_run
        counts["files_added"] = len(added)
        counts["zero_result_searches"] = zero_result_searches

        if added:
            print(f"\nAdded {len(added)} new file(s) to watchlist:")
            for cf in added:
                print(f"  {cf}: {watchlist[cf]['title'][:80]}")
        else:
            print("\nNo new files found.")

        if zero_result_searches:
            print(f"\nNote: these searches returned zero results this run: {zero_result_searches}. "
                  f"If a keyword that used to work now returns 0, that's worth checking by hand "
                  f"(see City Watch.md Section III) -- it may be a real absence, or the search may be broken.")


if __name__ == "__main__":
    main()
