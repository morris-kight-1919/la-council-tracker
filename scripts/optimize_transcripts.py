#!/usr/bin/env python3
"""Re-shape transcript storage from one-row-per-caption-segment to
one-row-per-meeting with the segments held as compressed JSON.

Why: the original schema stored 610,969 segment rows for 226 meetings. That
turned 44 MB of source JSON into roughly 170 MB of table + indexes:

    transcript_segments                     88.0 MB
    sqlite_autoindex (UNIQUE on text)       35.7 MB   <- unique index over text
    search_index_content                    35.1 MB   <- FTS keeping its own copy
    idx_ts_meeting                           9.8 MB
    idx_ts_body                             30.2 MB   <- already dropped

Three separate wastes: a UNIQUE constraint whose index covers the whole text
column, FTS5 storing a duplicate copy of content it was handed, and an index
on a ~40-value column across 611k rows.

New shape:
  - one row per meeting; segments as zlib-compressed JSON
  - contentless FTS5 (content='') so the index stores no copy of the text
  - snippets reconstructed on demand by decompressing the one matching
    meeting -- cheap, since a single meeting is ~200 KB

Segment-level lookup is preserved via segments_for(meeting_id).
"""
import json
import os
import zlib

import db
from run_log import logged_run

NEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS transcripts (
    meeting_id    TEXT PRIMARY KEY,
    meeting_date  TEXT,
    body          TEXT,
    video_url     TEXT,
    n_segments    INTEGER,
    duration_s    INTEGER,
    segments_z    BLOB      -- zlib-compressed JSON: [{start, text}, ...]
);

-- contentless FTS: stores the index, not the text
CREATE VIRTUAL TABLE IF NOT EXISTS transcript_fts USING fts5(
    meeting_id UNINDEXED,
    body,
    text,
    content=''
);
"""


def segments_for(conn, meeting_id):
    """Decompress and return a meeting's segments."""
    row = conn.execute("SELECT segments_z FROM transcripts WHERE meeting_id=?",
                       (meeting_id,)).fetchone()
    if not row or not row["segments_z"]:
        return []
    return json.loads(zlib.decompress(row["segments_z"]).decode())


def main():
    with logged_run("optimize_transcripts") as counts:
        conn = db.init()
        conn.executescript(NEW_SCHEMA)

        meetings = conn.execute(
            "SELECT DISTINCT meeting_id, meeting_date, body, video_url "
            "FROM transcript_segments").fetchall()
        print(f"re-shaping {len(meetings)} meeting(s)...")

        moved = 0
        for m in meetings:
            segs = conn.execute(
                "SELECT second, text FROM transcript_segments "
                "WHERE meeting_id=? ORDER BY second", (m["meeting_id"],)).fetchall()
            if not segs:
                continue
            payload = [{"start": s["second"], "text": s["text"]} for s in segs]
            blob = zlib.compress(json.dumps(payload).encode(), 9)
            full_text = " ".join(s["text"] for s in segs)

            conn.execute(
                "INSERT OR REPLACE INTO transcripts "
                "(meeting_id, meeting_date, body, video_url, n_segments, duration_s, segments_z) "
                "VALUES (?,?,?,?,?,?,?)",
                (m["meeting_id"], m["meeting_date"], m["body"], m["video_url"],
                 len(payload), payload[-1]["start"] if payload else 0, blob))
            conn.execute(
                "INSERT INTO transcript_fts (meeting_id, body, text) VALUES (?,?,?)",
                (m["meeting_id"], m["body"] or "", full_text))
            moved += 1
        conn.commit()

        # Drop the old segment table and its transcript rows from the shared index
        conn.execute("DELETE FROM search_index WHERE source_type='transcript'")
        conn.execute("DROP INDEX IF EXISTS idx_ts_meeting")
        conn.execute("DROP TABLE IF EXISTS transcript_segments")
        conn.commit()
        conn.execute("VACUUM")

        counts.update(meetings=moved)
        print(f"moved {moved} meeting(s); dropped transcript_segments")


if __name__ == "__main__":
    main()
