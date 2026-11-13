#!/usr/bin/env python3
"""Search council meeting transcripts for keywords, find the council file
being discussed at each hit (via the nearest preceding agenda-item video
timestamp), and optionally add those files to watchlist.json."""
import datetime
import json
import os
import re

from primegov_client import fetch_agenda
from transcript import fetch_transcript

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")


def load(path, default):
    if os.path.exists(path):
        return json.load(open(path))
    return default


def nearest_item(agenda_items, seconds):
    """The agenda item whose video_seconds anchor is the closest one at or
    before this timestamp -- best guess at what was being discussed."""
    candidates = [it for it in agenda_items if it.get("video_seconds") is not None
                  and it["video_seconds"] <= seconds]
    if not candidates:
        return None
    return max(candidates, key=lambda it: it["video_seconds"])


def youtube_link(video_url, seconds):
    return f"{video_url}&t={int(seconds)}s" if "?" in video_url else f"{video_url}?t={int(seconds)}s"


def search_meeting(meeting, keyword, context=1):
    """Returns list of {second, text, context_text, item} for a single meeting."""
    video_url = meeting.get("videoUrl")
    if not video_url:
        return []

    segments = fetch_transcript(meeting["id"], video_url)
    if not segments:
        return []

    agenda_items = fetch_agenda(meeting)
    kw_lower = keyword.lower()
    hits = []
    for i, seg in enumerate(segments):
        if kw_lower in seg["text"].lower():
            lo = max(0, i - context)
            hi = min(len(segments), i + context + 1)
            context_text = " ".join(s["text"] for s in segments[lo:hi])
            item = nearest_item(agenda_items, seg["start"])
            hits.append({
                "second": seg["start"],
                "text": seg["text"],
                "context": context_text,
                "youtube_url": youtube_link(video_url, seg["start"]),
                "item": item,
            })
    return hits


def add_to_watchlist(cfnumber, title, keyword):
    watchlist = load(WATCHLIST_PATH, {})
    if cfnumber in watchlist:
        return False

    # Never write a null title. The agenda parser returns None for items whose
    # markup doesn't match, and an entry with title=None crashes every
    # consumer doing title[:70]. Fall back to the council file's own record.
    if not title:
        try:
            from cf_client import fetch_record
            rec = fetch_record(cfnumber)
            title = (rec or {}).get("title")
        except Exception:
            title = None
    if not title:
        title = f"(untitled council file {cfnumber})"

    watchlist[cfnumber] = {
        "title": title,
        "added": datetime.date.today().isoformat(),
        "source": f"transcript:{keyword}",
    }
    json.dump(watchlist, open(WATCHLIST_PATH, "w"), indent=1)
    return True


if __name__ == "__main__":
    import sys
    from primegov_client import list_meetings_for_year

    keyword = sys.argv[1]
    meeting_ids = [int(x) for x in sys.argv[2:]]
    meetings_2026 = list_meetings_for_year(2026)
    by_id = {m["id"]: m for m in meetings_2026}

    added = 0
    for mid in meeting_ids:
        meeting = by_id.get(mid)
        if not meeting:
            print(f"meeting {mid} not found in 2026 list")
            continue
        hits = search_meeting(meeting, keyword)
        print(f"\n=== Meeting {mid} ({meeting['date']}, {meeting['title']}): {len(hits)} hit(s) ===")
        for h in hits:
            print(f"  [{h['second']}s] {h['youtube_url']}")
            print(f"    ...{h['context']}...")
            if h["item"]:
                print(f"    -> likely CF {h['item']['cfnumber']}: {h['item']['title']}")
                if add_to_watchlist(h["item"]["cfnumber"], h["item"]["title"], keyword):
                    added += 1
                    print(f"    -> added to watchlist.json")
            else:
                print("    -> no agenda item anchor found near this timestamp")

    print(f"\n{added} new file(s) added to watchlist.json")
