#!/usr/bin/env python3
"""SQLite + FTS5 corpus for everything the tracker ingests.

Adapted from the RIP Los Angeles "City Document Parsing Schema" design, with
two deliberate departures:

  1. That design keeps the corpus tiny on purpose ("never hold 1,000 PDFs")
     because it monitors specific active fights. This tracker is doing
     investigative research and wants breadth. Measured here: extracted text
     is 1/52nd the size of the source PDFs (12 MB vs 637 MB). So we can keep
     *everything* as text and still be small -- breadth and low storage are
     not in tension once the PDF is treated as disposable.

  2. It has one `documents` table. We ingest structurally different things --
     documents, individual public comments, timestamped transcript segments,
     and city emails -- so each gets a typed table, and everything also feeds
     one unified FTS5 index so a single query can search across all of them.

The PDF-as-disposable rule (their core insight, and the reason storage drops
~98%): keep full_text + source_url + sha256 forever; delete the file after a
retention window; re-fetch from source_url if ever needed. Verified that City
Clerk `onlinedocs` URLs are stable -- a 2013-era document still returns 200.
"""
import json
import os
import sqlite3  # noqa: F401  (used by _count's exception handling)

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DB_PATH = os.path.join(ROOT, "corpus.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

-- One row per ingested document (PDF or otherwise). The PDF itself is
-- disposable; this row is permanent.
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    cfnumber      TEXT,           -- council file this belongs to
    doc_type      TEXT,           -- motion | committee_report | council_action | public_comment | speaker_card | ...
    title         TEXT,
    doc_date      TEXT,           -- ISO where parseable; the document's own date
    source_url    TEXT UNIQUE,    -- re-fetchable
    sha256        TEXT,
    full_text     TEXT,
    fields_json   TEXT,           -- {cf_numbers:[], dollar_amounts:[], orgs:[], people:[], dates:[]}
    retrieved_at  TEXT,
    pdf_path      TEXT,           -- local file while retained; NULL after sweep
    pdf_deleted   INTEGER DEFAULT 0,
    delete_after  TEXT,           -- retrieved_at + retention window
    never_delete  INTEGER DEFAULT 0  -- for un-refetchable material (cf. their cpra_production carve-out)
);
CREATE INDEX IF NOT EXISTS idx_documents_cf ON documents(cfnumber);
CREATE INDEX IF NOT EXISTS idx_documents_type ON documents(doc_type);

-- One row per individual public comment. The archived PDFs bundle many
-- comments each -- 42 PDFs held 328 comments from 315 people -- so the
-- comment, not the PDF, is the unit that matters.
CREATE TABLE IF NOT EXISTS public_comments (
    id            INTEGER PRIMARY KEY,
    cfnumber      TEXT,
    commenter     TEXT,
    submitted_at  TEXT,
    body          TEXT,
    parse_mode    TEXT,           -- 'form' (structured web submission) | 'letter' (attached, unstructured)
    needs_review  INTEGER DEFAULT 0,
    source_doc_id INTEGER REFERENCES documents(id),
    source_url    TEXT,
    UNIQUE(source_url, commenter, submitted_at)
);
CREATE INDEX IF NOT EXISTS idx_pc_cf ON public_comments(cfnumber);
CREATE INDEX IF NOT EXISTS idx_pc_commenter ON public_comments(commenter);

-- Meeting transcripts, one row per caption segment.
CREATE TABLE IF NOT EXISTS transcript_segments (
    id            INTEGER PRIMARY KEY,
    meeting_id    TEXT,
    meeting_date  TEXT,
    body          TEXT,           -- committee / body name
    second        INTEGER,
    text          TEXT,
    video_url     TEXT,
    UNIQUE(meeting_id, second, text)
);
CREATE INDEX IF NOT EXISTS idx_ts_meeting ON transcript_segments(meeting_id);
CREATE INDEX IF NOT EXISTS idx_ts_body ON transcript_segments(body);

-- Emails from the City (council-file subscriptions, ENS, agenda notices).
CREATE TABLE IF NOT EXISTS emails (
    id            INTEGER PRIMARY KEY,
    message_id    TEXT UNIQUE,    -- stable upstream identifier; dedupe key
    source        TEXT,           -- cityclerk_cf | ens | agenda | unknown
    from_addr     TEXT,
    subject       TEXT,
    received_at   TEXT,
    body_text     TEXT,
    cfnumbers_json TEXT,          -- CF numbers extracted from the body
    links_json    TEXT,           -- document/agenda URLs found in the body
    processed     INTEGER DEFAULT 0,
    retrieved_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_emails_source ON emails(source);
CREATE INDEX IF NOT EXISTS idx_emails_processed ON emails(processed);

-- Things that should go out in a digest. Deliberately separate from the data
-- snapshots: City Watch.md Section VI warns that if the "already seen"
-- pointer advances when the *work* completes rather than when the *send* is
-- confirmed, a single failed send drops those records permanently and
-- silently. So data capture advances the snapshot; sending advances sent_at
-- here, and only on confirmed success.
CREATE TABLE IF NOT EXISTS outbox (
    id           INTEGER PRIMARY KEY,
    created_at   TEXT,
    kind         TEXT,      -- keyword_hit | stage_change | hearing_alert
    cfnumber     TEXT,
    priority     TEXT,      -- high | medium | low
    summary      TEXT,      -- one-line human-readable
    payload_json TEXT,
    dedupe_key   TEXT UNIQUE,   -- stable upstream id, never a hash of rendered text
    sent_at      TEXT,      -- NULL until a send is CONFIRMED
    digest_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_outbox_unsent ON outbox(sent_at);

-- What actually went out, so "was this in Tuesday's digest?" is answerable
-- from the log alone rather than by re-deriving it.
CREATE TABLE IF NOT EXISTS sent_digests (
    digest_id    TEXT PRIMARY KEY,
    sent_at      TEXT,
    recipient    TEXT,
    subject      TEXT,
    item_count   INTEGER,
    item_ids     TEXT       -- JSON array of outbox ids
);

-- A human decision on one outbox item, kept separate from outbox itself so
-- outbox stays append-only (same additive-resolution pattern as `relevance`
-- sitting beside emails/documents). Added for the GUI's Inbox review queue,
-- 2026-08-09. decision='backlog' marks items that predate the Inbox --
-- deliberately still visible/queryable, never hidden (City Watch.md's
-- "quarantine, never silently use" rule, applied here to review state).
CREATE TABLE IF NOT EXISTS outbox_review (
    outbox_id          INTEGER PRIMARY KEY REFERENCES outbox(id),
    decision            TEXT,      -- confirmed | rejected | cfnumber_overridden | deferred | dismissed | snoozed | backlog
    cfnumber_override   TEXT,
    note                TEXT,
    decided_at          TEXT,
    decided_by          TEXT       -- 'Morris' | 'Richard' -- plain attribution, no auth
);

-- One row per (council file, vote event, member) -- added 2026-08-09,
-- scoped to watched files only (not a full LA Council voting archive;
-- that's a distinct, deferred idea -- see project memory). A file's page
-- can carry MULTIPLE vote events over its lifecycle (committee votes,
-- council votes, reconsiderations -- confirmed live, e.g. one file showed
-- "(8 Votes)"), each with its own date/action/member list; event_index 0
-- is the most recent. Same-day multiple events are real (confirmed:
-- distinct vote_action text per event), so event_index -- not meeting_date
-- -- is what makes a row unique.
CREATE TABLE IF NOT EXISTS votes (
    cfnumber           TEXT,
    event_index        INTEGER,  -- 0 = most recent event for this file
    member_name        TEXT,
    council_district   TEXT,
    vote               TEXT,   -- YES | NO | ABSENT | ABSTAIN | OTHER
    vote_action        TEXT,   -- e.g. "Adopted", "Adopted as Amended"
    meeting_date       TEXT,
    retrieved_at       TEXT,
    PRIMARY KEY (cfnumber, event_index, member_name)
);
CREATE INDEX IF NOT EXISTS idx_votes_member ON votes(member_name);

-- One unified full-text index over everything, so a single query spans
-- documents, comments, transcripts and emails.
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    source_type,      -- document | public_comment | transcript | email
    source_id UNINDEXED,
    cfnumber,
    title,
    body,
    meta UNINDEXED    -- JSON blob for display (date, link, commenter, ...)
);
"""


def connect(path=DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init(path=DB_PATH):
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def index_row(conn, source_type, source_id, cfnumber, title, body, meta=None):
    """Add a row to the unified FTS index."""
    conn.execute(
        "INSERT INTO search_index (source_type, source_id, cfnumber, title, body, meta) "
        "VALUES (?,?,?,?,?,?)",
        (source_type, str(source_id), cfnumber or "", title or "", body or "",
         json.dumps(meta or {})),
    )


def _count(conn, table):
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0  # table not present in this DB version


def stats(conn):
    out = {}
    for t in ("documents", "public_comments", "emails"):
        out[t] = _count(conn, t)
    out["transcript_meetings"] = _count(conn, "transcripts")
    row = conn.execute("SELECT COALESCE(SUM(n_segments),0) FROM transcripts").fetchone()
    out["transcript_segments"] = row[0] if row else 0
    out["search_index"] = _count(conn, "search_index")
    out["pdfs_retained"] = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE pdf_deleted=0 AND pdf_path IS NOT NULL").fetchone()[0]
    return out


if __name__ == "__main__":
    conn = init()
    print(f"Initialised {DB_PATH}")
    for k, v in stats(conn).items():
        print(f"  {k}: {v}")
