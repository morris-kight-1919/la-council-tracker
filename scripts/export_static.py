#!/usr/bin/env python3
"""Exports a static snapshot of the tracker for City Watch (Miki's version).

Why static, not a live app: Render's free tier (and most free static hosts)
has an ephemeral filesystem -- a live SQLite-backed app would silently reset
its data on every restart/spin-down. This script runs locally, where
corpus.db actually lives, reads it, and writes plain HTML/JSON into
static_site/. That folder gets committed and pushed; the host just serves
whatever was pushed. No backend, no database on the host, nothing to go
silently stale in a way nobody notices.

Covers the three things JOB EM's brief asked for: agendas, a calendar of
upcoming dates on tracked files, and the tracked items themselves. Every
page carries "as of" the moment this script ran. An empty section says what
was searched and when, not nothing -- an empty calendar must never look like
a quiet week when it's really a broken feed (the brief's own words).

Usage: python3 scripts/export_static.py
"""
import datetime
import html
import json
import os
import re
import sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DB_PATH = os.path.join(ROOT, "corpus.db")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")
OUT_DIR = os.path.join(ROOT, "static_site")

AS_OF = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
WINDOW_DAYS = 60


def parse_meeting_date(s):
    """payload_json stores meeting_date as e.g. 'Aug 11, 2026' -- parse or None."""
    try:
        return datetime.datetime.strptime(s, "%b %d, %Y").date()
    except (ValueError, TypeError):
        return None


def load_watchlist():
    with open(WATCHLIST_PATH) as f:
        return json.load(f)


def load_upcoming(conn, today):
    """Hearing alerts for watched files, today through WINDOW_DAYS ahead."""
    cur = conn.cursor()
    cur.execute("SELECT cfnumber, payload_json, created_at FROM outbox WHERE kind = 'hearing_alert'")
    rows = cur.fetchall()
    seen = {}  # dedupe by (cfnumber, meeting_id), keep latest created_at
    for cfnumber, payload_json, created_at in rows:
        payload = json.loads(payload_json)
        mdate = parse_meeting_date(payload.get("meeting_date"))
        if mdate is None:
            continue
        key = (cfnumber, payload.get("meeting_id"))
        if key not in seen or created_at > seen[key][1]:
            seen[key] = (payload, created_at, mdate)

    window_end = today + datetime.timedelta(days=WINDOW_DAYS)
    upcoming = []
    for (cfnumber, _mid), (payload, _created, mdate) in seen.items():
        if today <= mdate <= window_end:
            upcoming.append({**payload, "cfnumber": cfnumber, "meeting_date_iso": mdate.isoformat()})
    upcoming.sort(key=lambda r: r["meeting_date_iso"])
    return upcoming


def load_tracked_status(conn, watchlist):
    """For each tracked file, pull its most recent document (if any)."""
    cur = conn.cursor()
    out = {}
    for cfnumber in watchlist:
        cur.execute(
            "SELECT title, doc_type, doc_date, retrieved_at, source_url FROM documents "
            "WHERE cfnumber = ? ORDER BY retrieved_at DESC LIMIT 1",
            (cfnumber,),
        )
        row = cur.fetchone()
        out[cfnumber] = {
            "title": row[0], "doc_type": row[1], "doc_date": row[2],
            "retrieved_at": row[3], "source_url": row[4],
        } if row else None
    return out


def build_search_index(conn):
    cur = conn.cursor()
    docs = []
    cur.execute("SELECT id, cfnumber, doc_type, title, doc_date, source_url, full_text FROM documents")
    for id_, cf, doc_type, title, doc_date, url, full_text in cur.fetchall():
        docs.append({
            "id": f"doc-{id_}", "kind": "document", "cfnumber": cf, "doc_type": doc_type,
            "title": title or "(untitled)", "date": doc_date, "url": url,
            "body": (full_text or "")[:4000],
        })
    cur.execute("SELECT id, message_id, source, subject, body_text, received_at, cfnumbers_json FROM emails")
    for id_, _msg, source, subject, body_text, received_at, cfnumbers_json in cur.fetchall():
        cfs = json.loads(cfnumbers_json) if cfnumbers_json else []
        docs.append({
            "id": f"email-{id_}", "kind": "email", "cfnumber": (cfs[0] if cfs else None),
            "doc_type": source, "title": subject or "(no subject)", "date": received_at, "url": None,
            "body": (body_text or "")[:4000],
        })
    cur.execute("PRAGMA table_info(public_comments)")
    pc_cols = [c[1] for c in cur.fetchall()]
    cur.execute(f"SELECT {', '.join(pc_cols)} FROM public_comments")
    for row in cur.fetchall():
        r = dict(zip(pc_cols, row))
        docs.append({
            "id": f"pc-{r.get('id')}", "kind": "public_comment", "cfnumber": r.get("cfnumber"),
            "doc_type": "public_comment", "title": f"Public comment on {r.get('cfnumber')}",
            "date": r.get("submitted_at") or r.get("doc_date"), "url": None,
            "body": (r.get("comment_text") or r.get("full_text") or "")[:4000],
        })
    return docs


PAGE_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title} — City Watch</title>
<style>
  :root {{ --bg:#f6f5f2; --panel:#fff; --text:#1f1d1a; --muted:#6b665e; --border:#ddd8cf; --accent:#1d4ed8; --radius:4px; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; font-family:-apple-system,"Helvetica Neue",Arial,sans-serif; background:var(--bg); color:var(--text); font-size:15px; line-height:1.5; }}
  header {{ padding:1.25rem 1.5rem; border-bottom:1px solid var(--border); background:var(--panel); }}
  header h1 {{ margin:0 0 0.25rem; font-size:1.2rem; }}
  .asof {{ color:var(--muted); font-size:0.85rem; }}
  nav {{ display:flex; gap:1rem; padding:0.75rem 1.5rem; background:var(--panel); border-bottom:1px solid var(--border); }}
  nav a {{ color:var(--text); text-decoration:none; font-size:0.9rem; }}
  nav a:hover {{ color:var(--accent); }}
  main {{ padding:1.5rem; max-width:760px; margin:0 auto; }}
  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:var(--radius); padding:1rem 1.25rem; margin-bottom:0.9rem; }}
  .card h3 {{ margin:0 0 0.35rem; font-size:1rem; }}
  .card h3 a {{ color:var(--text); text-decoration:none; }}
  .card h3 a:hover {{ color:var(--accent); }}
  .meta {{ color:var(--muted); font-size:0.82rem; margin-bottom:0.4rem; }}
  .empty {{ color:var(--muted); font-style:italic; padding:1rem 0; }}
  input[type=search] {{ width:100%; padding:0.6rem 0.8rem; border:1px solid var(--border); border-radius:var(--radius); font-size:1rem; margin-bottom:1rem; }}
  mark {{ background:#fdf0d5; }}
</style>
</head>
<body>
<header>
  <h1>City Watch</h1>
  <div class="asof">As of {as_of}</div>
</header>
<nav>
  <a href="index.html">Watchlist</a>
  <a href="calendar.html">Calendar</a>
  <a href="search.html">Search</a>
</nav>
<main>
"""
PAGE_TAIL = "</main></body></html>\n"


def write_watchlist_page(watchlist, statuses):
    rows = []
    for cfnumber, meta in sorted(watchlist.items()):
        status = statuses.get(cfnumber)
        rows.append('<div class="card"><h3><a href="https://lms.lacity.gov/files/{cf}" target="_blank">{cf}</a> — {title}</h3>'
                     '<div class="meta">Tracked since {added} · source: {source}</div>'
                     '{latest}</div>'.format(
            cf=html.escape(cfnumber), title=html.escape(meta.get("title", "")),
            added=html.escape(meta.get("added", "unknown")), source=html.escape(meta.get("source", "unknown")),
            latest=('<div class="meta">Latest on file: {t} ({dt}, retrieved {r})</div>'.format(
                t=html.escape(status["title"] or ""), dt=html.escape(status["doc_date"] or "undated"),
                r=html.escape((status["retrieved_at"] or "")[:10])
            ) if status else '<div class="meta">No documents on file yet for this number — tracked, nothing retrieved so far.</div>')
        ))
    body = "".join(rows) if rows else '<p class="empty">Nothing tracked yet.</p>'
    page = PAGE_HEAD.format(title="Watchlist", as_of=AS_OF) + \
        f"<p>{len(watchlist)} council files tracked.</p>" + body + PAGE_TAIL
    with open(os.path.join(OUT_DIR, "index.html"), "w") as f:
        f.write(page)


def write_calendar_page(upcoming, watchlist):
    if upcoming:
        rows = "".join(
            '<div class="card"><h3><a href="{url}" target="_blank">{title}</a></h3>'
            '<div class="meta">{mdate} · {mtitle} · CF {cf}</div></div>'.format(
                url=html.escape(it.get("url") or "#"),
                title=html.escape((it.get("item_title") or "")[:140]),
                mdate=html.escape(it["meeting_date_iso"]),
                mtitle=html.escape(it.get("meeting_title") or ""),
                cf=html.escape(it["cfnumber"]),
            ) for it in upcoming
        )
    else:
        rows = (f'<p class="empty">No upcoming agenda items found for any of the {len(watchlist)} tracked files, '
                f'searched {AS_OF}, window through {WINDOW_DAYS} days ahead. This means no hearing alerts matched '
                f'— check the pipeline is still running if this persists, since an empty calendar can mean a quiet '
                f'window or a broken feed and this page can\'t tell you which.</p>')
    page = PAGE_HEAD.format(title="Calendar", as_of=AS_OF) + \
        f"<p>Upcoming agenda items on tracked files, next {WINDOW_DAYS} days.</p>" + rows + PAGE_TAIL
    with open(os.path.join(OUT_DIR, "calendar.html"), "w") as f:
        f.write(page)


def write_search_page():
    page = PAGE_HEAD.format(title="Search", as_of=AS_OF) + """
<input type="search" id="q" placeholder="Search documents, emails, public comments…" autofocus>
<div id="results"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/lunr.js/2.3.9/lunr.min.js"></script>
<script>
let idx, docsById = {};
fetch('search-index.json').then(r => r.json()).then(data => {
  docsById = Object.fromEntries(data.map(d => [d.id, d]));
  idx = lunr(function () {
    this.ref('id'); this.field('title'); this.field('body'); this.field('cfnumber');
    data.forEach(d => this.add(d));
  });
});
function esc(s) { const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim();
  const out = document.getElementById('results');
  if (!idx || q.length < 2) { out.innerHTML = ''; return; }
  let hits;
  try { hits = idx.search(q); } catch (err) { hits = idx.search(lunr.default ? q : q.replace(/[*:^~+\\-]/g, ' ')); }
  if (!hits.length) { out.innerHTML = '<p class="empty">No matches.</p>'; return; }
  out.innerHTML = hits.slice(0, 40).map(h => {
    const d = docsById[h.ref];
    return `<div class="card"><h3>${esc(d.title)}</h3><div class="meta">${esc(d.kind)} · ${esc(d.cfnumber || 'no CF')} · ${esc(d.date || 'undated')}</div></div>`;
  }).join('');
});
</script>
""" + PAGE_TAIL
    with open(os.path.join(OUT_DIR, "search.html"), "w") as f:
        f.write(page)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    watchlist = load_watchlist()
    today = datetime.date.today()

    statuses = load_tracked_status(conn, watchlist)
    upcoming = load_upcoming(conn, today)
    search_docs = build_search_index(conn)

    write_watchlist_page(watchlist, statuses)
    write_calendar_page(upcoming, watchlist)
    write_search_page()
    with open(os.path.join(OUT_DIR, "search-index.json"), "w") as f:
        json.dump(search_docs, f)

    print(f"Exported {len(watchlist)} tracked files, {len(upcoming)} upcoming items, "
          f"{len(search_docs)} searchable records, as of {AS_OF}")


if __name__ == "__main__":
    main()
