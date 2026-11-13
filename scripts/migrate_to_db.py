#!/usr/bin/env python3
"""Load everything already on disk into corpus.db: documents, individual
public comments, and transcript segments. Idempotent -- safe to re-run.

The public-comment parsing here is the fix for the biggest gap found in the
pipeline review: 42 archived PDFs actually contain 328 individual comments
from 315 distinct people, because 22 of them bundle many comments into one
file. The old tooling showed one 300-character excerpt per PDF, making ~87%
of the public comment invisible. The comment, not the PDF, is the unit.
"""
import datetime
import json
import os
import re

import db
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DOCS_DIR = os.path.join(ROOT, "documents")
TRANSCRIPT_DIR = os.path.join(ROOT, "transcripts")
MEETING_INDEX = os.path.join(ROOT, "data", "meeting_index.json")
MANIFEST = os.path.join(DOCS_DIR, "manifest.json")

RETENTION_DAYS = 10


def classify_doc(title):
    t = (title or "").lower()
    if "communication" in t and "public" in t:
        return "public_comment"
    if "speaker card" in t:
        return "speaker_card"
    if "committee" in t and "report" in t:
        return "committee_report"
    if "council action" in t:
        return "council_action"
    if "motion" in t:
        return "motion"
    if "report from" in t:
        return "report"
    if "community impact statement" in t:
        return "community_impact_statement"
    return "other"


def extract_fields(text):
    """Cheap auto-extracts that ride alongside full text (their fields_json
    idea). Not NLP -- just regex over things that are unambiguous in this
    corpus and useful to filter on later."""
    if not text:
        return {}
    return {
        "cf_numbers": sorted(set(re.findall(r"\b\d{2}-\d{4}(?:-S\d+)?\b", text)))[:40],
        "dollar_amounts": sorted(set(re.findall(r"\$[\d,]+(?:\.\d{2})?(?:\s*(?:million|billion))?", text)))[:40],
        "dates": sorted(set(re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", text)))[:40],
    }


def parse_public_comments(text, cfnumber, source_url):
    """Split a bundled public-comment PDF into individual comments.

    ~93% are structured web-form submissions with a regular shape:
        Name: <who>  Date Submitted: <when>
        Council File No: <cf>  Comments for Public Posting: <body>
    The rest are attached letters with no such structure -- those are kept
    whole and flagged needs_review rather than force-parsed into a fake
    commenter, per City Watch.md's rule that uncertain data gets labelled,
    never silently coerced.
    """
    out = []
    pattern = re.compile(
        r"Name:\s*(?P<name>.+?)\s+Date Submitted:\s*(?P<date>.+?)\s+"
        r"Council File No:\s*(?P<cf>\S*)\s*.*?Comments for Public Posting:\s*(?P<body>.*?)"
        r"(?=Communication from Public|Name:\s*.+?\s+Date Submitted:|\Z)",
        re.S,
    )
    for m in pattern.finditer(text):
        body = re.sub(r"\s+", " ", m.group("body")).strip()
        if not body:
            continue
        out.append({
            "commenter": re.sub(r"\s+", " ", m.group("name")).strip(),
            "submitted_at": re.sub(r"\s+", " ", m.group("date")).strip(),
            "cfnumber": (m.group("cf") or cfnumber).strip() or cfnumber,
            "body": body,
            "parse_mode": "form",
            "needs_review": 0,
        })

    if not out:
        # Attached letter (or an unrecognised layout). Keep it whole, flag it.
        cleaned = re.sub(r"\s+", " ", text).strip()
        if cleaned:
            out.append({
                "commenter": None,
                "submitted_at": None,
                "cfnumber": cfnumber,
                "body": cleaned,
                "parse_mode": "letter",
                "needs_review": 1,
            })
    return out


def ingest_document(conn, cf, doc, result):
    """Insert one downloaded+extracted document into the corpus, plus any
    public comments inside it. Shared by backfill_documents.py and
    check_updates.py so the DB can't silently drift from what's on disk --
    check_updates used to download documents without ingesting them, which
    left 51 files on disk and 0 in the database.

    Returns (documents_added, comments_added).
    """
    import extract_entities

    text = result.get("text")
    if not text:
        return 0, 0

    title = doc.get("title") or "(untitled)"
    doc_type = classify_doc(title)
    retrieved = result.get("retrieved_at") or datetime.datetime.now().isoformat(timespec="seconds")

    cur = conn.execute(
        "INSERT OR IGNORE INTO documents "
        "(cfnumber, doc_type, title, doc_date, source_url, sha256, full_text, "
        " fields_json, retrieved_at, pdf_path, pdf_deleted, delete_after) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (cf, doc_type, title, doc.get("date"), doc["url"], result.get("sha256"), text,
         json.dumps(extract_entities.extract(text)), retrieved, None, 1, retrieved))
    if not cur.rowcount:
        return 0, 0

    doc_id = cur.lastrowid
    db.index_row(conn, "document", doc_id, cf, title, text,
                 {"doc_type": doc_type, "url": doc["url"], "date": doc.get("date")})

    n_comments = 0
    if doc_type == "public_comment":
        for c in parse_public_comments(text, cf, doc["url"]):
            c2 = conn.execute(
                "INSERT OR IGNORE INTO public_comments "
                "(cfnumber, commenter, submitted_at, body, parse_mode, needs_review, "
                " source_doc_id, source_url) VALUES (?,?,?,?,?,?,?,?)",
                (c["cfnumber"], c["commenter"], c["submitted_at"], c["body"],
                 c["parse_mode"], c["needs_review"], doc_id, doc["url"]))
            if c2.rowcount:
                n_comments += 1
                db.index_row(conn, "public_comment", c2.lastrowid, c["cfnumber"],
                             c["commenter"] or "(attached letter)", c["body"],
                             {"commenter": c["commenter"], "submitted_at": c["submitted_at"],
                              "parse_mode": c["parse_mode"], "url": doc["url"]})
    return 1, n_comments


def main():
    with logged_run("migrate_to_db") as counts:
        conn = db.init()
        manifest = json.load(open(MANIFEST)) if os.path.exists(MANIFEST) else {}
        # manifest is keyed "cf/name.pdf" -> {source_url, sha256, retrieved_at}
        by_stem = {}
        for key, meta in manifest.items():
            cf, fname = key.split("/", 1)
            by_stem[(cf, fname[:-4])] = meta

        n_docs = n_comments = n_segs = 0

        # ---- documents + public comments ----
        for cf in sorted(os.listdir(DOCS_DIR)):
            cf_dir = os.path.join(DOCS_DIR, cf)
            if not os.path.isdir(cf_dir):
                continue
            for fname in sorted(os.listdir(cf_dir)):
                if not fname.endswith(".txt"):
                    continue
                stem = fname[:-4]
                text = open(os.path.join(cf_dir, fname), encoding="utf-8", errors="replace").read()
                meta = by_stem.get((cf, stem), {})
                source_url = meta.get("source_url") or f"local:{cf}/{stem}"
                retrieved_at = meta.get("retrieved_at") or datetime.datetime.now().isoformat(timespec="seconds")
                title = re.sub(r"^\d+_", "", stem).rsplit("_", 1)[0].replace("_", " ")
                doc_type = classify_doc(title)
                pdf_path = os.path.join(cf_dir, stem + ".pdf")
                delete_after = (datetime.datetime.fromisoformat(retrieved_at)
                                + datetime.timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")

                cur = conn.execute(
                    "INSERT OR IGNORE INTO documents "
                    "(cfnumber, doc_type, title, source_url, sha256, full_text, fields_json, "
                    " retrieved_at, pdf_path, pdf_deleted, delete_after) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cf, doc_type, title, source_url, meta.get("sha256"), text,
                     json.dumps(extract_fields(text)), retrieved_at,
                     pdf_path if os.path.exists(pdf_path) else None,
                     0 if os.path.exists(pdf_path) else 1, delete_after),
                )
                if cur.rowcount:
                    n_docs += 1
                    doc_id = cur.lastrowid
                    db.index_row(conn, "document", doc_id, cf, title, text,
                                 {"doc_type": doc_type, "url": source_url})

                    if doc_type == "public_comment":
                        for c in parse_public_comments(text, cf, source_url):
                            cur2 = conn.execute(
                                "INSERT OR IGNORE INTO public_comments "
                                "(cfnumber, commenter, submitted_at, body, parse_mode, "
                                " needs_review, source_doc_id, source_url) VALUES (?,?,?,?,?,?,?,?)",
                                (c["cfnumber"], c["commenter"], c["submitted_at"], c["body"],
                                 c["parse_mode"], c["needs_review"], doc_id, source_url),
                            )
                            if cur2.rowcount:
                                n_comments += 1
                                db.index_row(conn, "public_comment", cur2.lastrowid,
                                             c["cfnumber"], c["commenter"] or "(attached letter)",
                                             c["body"], {"commenter": c["commenter"],
                                                         "submitted_at": c["submitted_at"],
                                                         "parse_mode": c["parse_mode"],
                                                         "url": source_url})
            conn.commit()

        # ---- transcripts ----
        index = json.load(open(MEETING_INDEX)) if os.path.exists(MEETING_INDEX) else {}
        for fname in sorted(os.listdir(TRANSCRIPT_DIR)):
            if not fname.endswith(".json"):
                continue
            mid = fname[:-5]
            cached = json.load(open(os.path.join(TRANSCRIPT_DIR, fname)))
            segs = cached["segments"] if isinstance(cached, dict) else cached
            if not segs:
                continue
            meta = index.get(mid, {})
            body = meta.get("title") or ""
            mdate = meta.get("date") or ""
            vurl = meta.get("videoUrl") or (cached.get("video_url") if isinstance(cached, dict) else None)

            rows = [(mid, mdate, body, s["start"], s["text"], vurl) for s in segs]
            before = conn.total_changes
            conn.executemany(
                "INSERT OR IGNORE INTO transcript_segments "
                "(meeting_id, meeting_date, body, second, text, video_url) VALUES (?,?,?,?,?,?)",
                rows)
            added = conn.total_changes - before
            n_segs += added

            if added:
                # Index the meeting as one searchable blob (segment-level FTS
                # would bloat the index; the segments table handles precision).
                full = " ".join(s["text"] for s in segs)
                db.index_row(conn, "transcript", mid, "", f"{mdate} — {body}", full,
                             {"meeting_id": mid, "date": mdate, "body": body, "video_url": vurl})
            conn.commit()

        counts.update(documents=n_docs, public_comments=n_comments, transcript_segments=n_segs)

        print("Migration complete.")
        for k, v in db.stats(conn).items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
