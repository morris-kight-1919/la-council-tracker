#!/usr/bin/env python3
"""Search every cached meeting transcript -- Council and committee -- for
keywords, and report hits with committee name, date, and a timestamped
YouTube link.

Deliberately does NOT auto-add anything to watchlist.json. The older
transcript_search.py attributed each hit to the nearest preceding agenda-item
video anchor, but those anchors are coarse (one meeting had 54 items sharing
20 anchors), so remarks made during general public comment were being pinned
to unrelated agenda items -- e.g. a comment about LAFLA legal-aid contracts
was attributed to an oil-and-gas drilling ordinance, which then got added to
the watchlist. Per City Watch.md Section III, a confidently-wrong match is
worse than no match. So: surface the hit, link to the exact moment, and let
a human decide what it belongs to.

Usage:
    python3 search_transcripts.py "Gibson Dunn" LAFLA Scolnick
    python3 search_transcripts.py --committees "Housing and Homelessness" LAHSA
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
TRANSCRIPT_DIR = os.path.join(ROOT, "transcripts")
MEETING_INDEX = os.path.join(ROOT, "data", "meeting_index.json")

CONTEXT_SEGMENTS = 2


def load_meeting_index():
    """meeting_id -> {date, title, videoUrl}. Built by build_meeting_index()
    so search doesn't need to hit the network."""
    if os.path.exists(MEETING_INDEX):
        return json.load(open(MEETING_INDEX))
    return {}


def build_meeting_index(years=(2025, 2026)):
    from primegov_client import list_meetings_for_year
    index = {}
    for year in years:
        try:
            for m in list_meetings_for_year(year):
                index[str(m["id"])] = {
                    "date": m.get("date"),
                    "title": m.get("title"),
                    "videoUrl": m.get("videoUrl"),
                    "dateTime": m.get("dateTime"),
                }
        except Exception as e:
            print(f"  (couldn't list {year}: {e})", file=sys.stderr)
    os.makedirs(os.path.dirname(MEETING_INDEX), exist_ok=True)
    json.dump(index, open(MEETING_INDEX, "w"), indent=1)
    return index


def load_segments(path):
    cached = json.load(open(path))
    return cached["segments"] if isinstance(cached, dict) else cached


def yt_link(url, seconds):
    if not url:
        return None
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}t={int(seconds)}s"


def hhmmss(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def search(keywords, committee_filter=None):
    index = load_meeting_index()
    if not index:
        print("Building meeting index (one-time network fetch)...")
        index = build_meeting_index()

    results = {kw: [] for kw in keywords}
    files = sorted(f for f in os.listdir(TRANSCRIPT_DIR) if f.endswith(".json"))

    for fname in files:
        meeting_id = fname[:-5]
        meta = index.get(meeting_id, {})
        title = meta.get("title") or f"meeting {meeting_id}"

        if committee_filter and committee_filter.lower() not in title.lower():
            continue

        try:
            segs = load_segments(os.path.join(TRANSCRIPT_DIR, fname))
        except (json.JSONDecodeError, OSError):
            continue
        if not segs:
            continue

        for i, seg in enumerate(segs):
            text_l = seg["text"].lower()
            for kw in keywords:
                if kw.lower() in text_l:
                    lo, hi = max(0, i - CONTEXT_SEGMENTS), min(len(segs), i + CONTEXT_SEGMENTS + 1)
                    context = " ".join(s["text"] for s in segs[lo:hi])
                    results[kw].append({
                        "meeting_id": meeting_id,
                        "date": meta.get("date", "?"),
                        "dateTime": meta.get("dateTime", ""),
                        "title": title,
                        "second": seg["start"],
                        "timestamp": hhmmss(seg["start"]),
                        "context": re.sub(r"\s+", " ", context).strip(),
                        "link": yt_link(meta.get("videoUrl"), seg["start"]),
                    })
    return results


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    committee_filter = None
    if "--committees" in sys.argv:
        idx = sys.argv.index("--committees")
        if idx + 1 < len(sys.argv):
            committee_filter = sys.argv[idx + 1]
            if committee_filter in args:
                args.remove(committee_filter)

    if not args:
        print(__doc__)
        sys.exit(1)

    results = search(args, committee_filter)

    total = 0
    for kw, hits in results.items():
        hits.sort(key=lambda h: h.get("dateTime") or "")
        print(f"\n{'='*70}\n{kw}: {len(hits)} hit(s)\n{'='*70}")
        for h in hits:
            print(f"\n  {h['date']} @ {h['timestamp']} -- {h['title']}")
            print(f"  {h['link']}")
            print(f"    ...{h['context']}...")
        total += len(hits)

    n_files = len([f for f in os.listdir(TRANSCRIPT_DIR) if f.endswith('.json')])
    print(f"\n{total} total hit(s) across {n_files} cached transcript(s).")
    print("Note: hits are NOT auto-added to the watchlist -- agenda-item "
          "attribution is unreliable (see script docstring). Review and add "
          "relevant council files by hand.")


if __name__ == "__main__":
    main()
