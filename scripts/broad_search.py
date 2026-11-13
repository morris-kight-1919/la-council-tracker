#!/usr/bin/env python3
"""Run transcript_search across many meetings x many keywords in one pass."""
from primegov_client import list_meetings_for_year
from transcript_search import search_meeting, add_to_watchlist

MEETING_IDS = [18421, 17724, 15255, 17801, 18006, 17723, 18309, 18322, 18364,
               18157, 18334, 18335, 18324, 17722, 18005]
KEYWORDS = ["Scolnick", "Gibson Dunn", "LAFLA", "Alvarez", "Garrie", "Monitor",
            "LAHSA", "Inside Safe"]

meetings = {m["id"]: m for m in list_meetings_for_year(2026)}

total_hits = 0
total_added = 0
for kw in KEYWORDS:
    kw_hits = []
    for mid in MEETING_IDS:
        meeting = meetings.get(mid)
        if not meeting:
            continue
        hits = search_meeting(meeting, kw)
        for h in hits:
            kw_hits.append((meeting, h))
    if kw_hits:
        print(f"\n############ {kw}: {len(kw_hits)} hit(s) ############")
        for meeting, h in kw_hits:
            print(f"  {meeting['date']} (meeting {meeting['id']}) [{h['second']}s] {h['youtube_url']}")
            print(f"    ...{h['context']}...")
            if h["item"]:
                print(f"    -> likely CF {h['item']['cfnumber']}: {h['item']['title']}")
                if add_to_watchlist(h["item"]["cfnumber"], h["item"]["title"], kw):
                    total_added += 1
                    print(f"    -> added to watchlist.json")
            else:
                print("    -> no agenda item anchor found near this timestamp")
        total_hits += len(kw_hits)
    else:
        print(f"{kw}: 0 hits")

print(f"\n=== TOTAL: {total_hits} hits across {len(KEYWORDS)} keywords x {len(MEETING_IDS)} meetings, {total_added} new watchlist entries ===")
