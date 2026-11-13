#!/usr/bin/env python3
"""Search the whole corpus -- documents, public comments, meeting transcripts,
and city emails -- from one query.

Their design's sharpest observation: "A corpus you cannot search is a corpus
you will not use. Eight documents in three weeks is not a data problem, it is
a usage problem... Build the reader before adding more writers." We just added
610,969 transcript segments as writers, so this is the reader.

Usage:
    corpus_search.py "Gibson Dunn"
    corpus_search.py LAFLA --type public_comment
    corpus_search.py "eviction defense" --cf 23-1022-S18
    corpus_search.py --commenter "Warren Resources"
    corpus_search.py --stats
"""
import argparse
import json
import re
import sqlite3
import textwrap
import zlib

import db


def transcript_search(conn, query, limit=25):
    """Transcripts live in a contentless FTS5 index (content=''), which stores
    no copy of the text -- that's what made the DB 3.5x smaller. The trade-off
    is that snippet() and stored columns come back NULL, so we match in FTS to
    get rowids, join back to the transcripts table, and build the snippet by
    decompressing that one meeting. Cheap: a meeting is ~200 KB."""
    rows = conn.execute(
        "SELECT rowid FROM transcript_fts WHERE transcript_fts MATCH ? "
        "ORDER BY rank LIMIT ?", (query, limit)).fetchall()
    if not rows:
        return []

    terms = [t.strip('"').lower() for t in re.findall(r'"[^"]+"|\S+', query)
             if t.upper() not in ("AND", "OR", "NOT")]
    out = []
    for r in rows:
        m = conn.execute(
            "SELECT meeting_id, meeting_date, body, video_url, segments_z "
            "FROM transcripts WHERE rowid=?", (r["rowid"],)).fetchone()
        if not m:
            continue
        segs = json.loads(zlib.decompress(m["segments_z"]).decode())
        for i, s in enumerate(segs):
            low = s["text"].lower()
            if any(t in low for t in terms):
                lo, hi = max(0, i - 2), min(len(segs), i + 3)
                ctx = " ".join(x["text"] for x in segs[lo:hi])
                for t in terms:
                    ctx = re.sub(f"({re.escape(t)})", r"<<\1>>", ctx, flags=re.I)
                link = m["video_url"]
                if link:
                    link += ("&" if "?" in link else "?") + f"t={int(s['start'])}s"
                out.append({
                    "meeting_id": m["meeting_id"], "date": m["meeting_date"],
                    "body": m["body"], "second": s["start"], "context": ctx, "link": link,
                })
                break  # first hit per meeting; keeps output scannable
    return out


def fts_search(conn, query, source_type=None, cfnumber=None, limit=25):
    sql = ("SELECT source_type, source_id, cfnumber, title, meta, "
           "snippet(search_index, 4, '<<', '>>', ' ... ', 18) AS snip, "
           "bm25(search_index) AS rank "
           "FROM search_index WHERE search_index MATCH ?")
    params = [query]
    if source_type:
        sql += " AND source_type = ?"
        params.append(source_type)
    if cfnumber:
        sql += " AND cfnumber = ?"
        params.append(cfnumber)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def show(rows):
    if not rows:
        print("  (no matches)")
        return
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        head = f"[{r['source_type']}]"
        if r["cfnumber"]:
            head += f" CF {r['cfnumber']}"
        if r["title"]:
            head += f" — {r['title'][:70]}"
        print(f"\n  {head}")

        bits = []
        if meta.get("date"):
            bits.append(meta["date"])
        if meta.get("body"):
            bits.append(meta["body"])
        if meta.get("commenter"):
            bits.append(f"commenter: {meta['commenter']}")
        if meta.get("submitted_at"):
            bits.append(meta["submitted_at"])
        if meta.get("parse_mode") == "letter":
            bits.append("ATTACHED LETTER — needs review")
        if bits:
            print(f"    {' · '.join(str(b) for b in bits)}")

        link = meta.get("video_url") or meta.get("url")
        if link:
            print(f"    {link}")

        snip = " ".join((r["snip"] or "").split())
        for line in textwrap.wrap(snip, 92)[:4]:
            print(f"    {line}")


def main():
    p = argparse.ArgumentParser(description="Search the tracker corpus.")
    p.add_argument("query", nargs="?", help="FTS5 query. Quote phrases: '\"Gibson Dunn\"'")
    p.add_argument("--type", dest="source_type",
                   choices=["document", "public_comment", "transcript", "email"])
    p.add_argument("--cf", dest="cfnumber", help="restrict to a council file")
    p.add_argument("--commenter", help="list public comments by a commenter (substring match)")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--stats", action="store_true", help="corpus size and composition")
    args = p.parse_args()

    conn = db.init()

    if args.stats:
        print("Corpus:")
        for k, v in db.stats(conn).items():
            print(f"  {k}: {v:,}")
        print("\nDocuments by type:")
        for r in conn.execute("SELECT doc_type, COUNT(*) n FROM documents "
                              "GROUP BY 1 ORDER BY n DESC"):
            print(f"  {r['n']:>5}  {r['doc_type']}")
        print("\nTranscripts by body (top 12):")
        for r in conn.execute("SELECT body, COUNT(*) n FROM transcripts "
                              "WHERE body != '' GROUP BY 1 ORDER BY n DESC LIMIT 12"):
            print(f"  {r['n']:>5}  {r['body']}")
        return

    if args.commenter:
        rows = conn.execute(
            "SELECT cfnumber, commenter, submitted_at, body, parse_mode FROM public_comments "
            "WHERE commenter LIKE ? ORDER BY submitted_at", (f"%{args.commenter}%",)).fetchall()
        print(f"{len(rows)} comment(s) matching commenter ~ {args.commenter!r}")
        for r in rows:
            print(f"\n  CF {r['cfnumber']} · {r['commenter']} · {r['submitted_at']}")
            for line in textwrap.wrap(" ".join(r["body"].split()), 92)[:5]:
                print(f"    {line}")
        return

    if not args.query:
        p.print_help()
        return

    try:
        rows = fts_search(conn, args.query, args.source_type, args.cfnumber, args.limit)
        tr = [] if (args.source_type and args.source_type != "transcript") or args.cfnumber \
             else transcript_search(conn, args.query, args.limit)
    except sqlite3.OperationalError as e:
        print(f"Query error: {e}\nTip: quote multi-word phrases, e.g. '\"Gibson Dunn\"'")
        return

    print(f"{len(rows) + len(tr)} match(es) for {args.query!r}"
          + (f" [type={args.source_type}]" if args.source_type else "")
          + (f" [CF {args.cfnumber}]" if args.cfnumber else ""))
    if rows or not tr:
        show(rows)

    for h in tr:
        print(f"\n  [transcript] {h['date']} · {h['body']}")
        if h["link"]:
            print(f"    {h['link']}")
        for line in textwrap.wrap(" ".join(h["context"].split()), 92)[:3]:
            print(f"    {line}")


if __name__ == "__main__":
    main()
