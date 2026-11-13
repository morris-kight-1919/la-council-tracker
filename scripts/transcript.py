#!/usr/bin/env python3
"""Fetch and parse YouTube auto-captions for a council meeting video into
clean, deduplicated, timestamped text segments."""
import datetime
import html
import json
import os
import re
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
TRANSCRIPT_DIR = os.path.join(ROOT, "transcripts")


def _parse_vtt(path):
    """YouTube auto-caption VTT is a 2-line rolling window: each cue repeats
    the previous cue's last line, then adds one new line. Keep only each
    cue's final line, and skip when it's identical to the prior kept line."""
    text = open(path, encoding="utf-8").read()
    blocks = re.split(r"\n\n+", text)
    segments = []
    prev_line = None
    for block in blocks:
        m = re.match(r"(\d\d:\d\d:\d\d\.\d\d\d) --> (\d\d:\d\d:\d\d\.\d\d\d)", block)
        if not m:
            continue
        start_str = m.group(1)
        h, mn, s = start_str.split(":")
        start_seconds = int(h) * 3600 + int(mn) * 60 + float(s)

        lines = block.split("\n")[1:]
        clean_lines = []
        for line in lines:
            line = re.sub(r"<[^>]+>", "", line).strip()
            if line:
                clean_lines.append(line)
        if not clean_lines:
            continue

        last_line = html.unescape(clean_lines[-1])
        if last_line == prev_line:
            continue
        prev_line = last_line
        segments.append({"start": round(start_seconds), "text": last_line})

    return segments


def fetch_transcript(meeting_id, video_url, force=False):
    """Returns list of {start, text} segments, cached to transcripts/<id>.json
    (cache file also carries retrieved_at + video_url metadata; segments
    remain a flat list on disk under the "segments" key for callers)."""
    os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
    cache_path = os.path.join(TRANSCRIPT_DIR, f"{meeting_id}.json")
    if os.path.exists(cache_path) and not force:
        cached = json.load(open(cache_path))
        return cached["segments"] if isinstance(cached, dict) else cached  # tolerate old flat-list caches

    with tempfile.TemporaryDirectory() as tmp:
        out_template = os.path.join(tmp, "cap.%(ext)s")
        result = subprocess.run(
            ["yt-dlp", "--skip-download", "--write-auto-sub", "--sub-lang", "en",
             "--sub-format", "vtt", "-o", out_template, video_url],
            capture_output=True, text=True, timeout=120,
        )
        vtt_path = os.path.join(tmp, "cap.en.vtt")
        retrieved_at = datetime.datetime.now().isoformat(timespec="seconds")
        if not os.path.exists(vtt_path):
            # no auto-captions available for this video -- record that fact,
            # don't just leave an empty file indistinguishable from "not yet tried"
            json.dump({"segments": [], "retrieved_at": retrieved_at,
                        "video_url": video_url, "no_captions": True}, open(cache_path, "w"))
            return []
        segments = _parse_vtt(vtt_path)

    json.dump({"segments": segments, "retrieved_at": retrieved_at, "video_url": video_url},
               open(cache_path, "w"), indent=1)
    return segments


if __name__ == "__main__":
    import sys
    segs = fetch_transcript(sys.argv[1], sys.argv[2])
    print(f"{len(segs)} segments")
    for s in segs[:10]:
        print(s["start"], s["text"])
