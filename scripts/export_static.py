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
    """LMS email subjects spell out the month in full, e.g. 'November 13, 2026'."""
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.datetime.strptime(s, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def load_watchlist():
    with open(WATCHLIST_PATH) as f:
        return json.load(f)


MEETING_SUBJECT_RE = re.compile(
    r"(New (?:regular|special(?: meeting)?) agenda): (.+?) meeting on (\w+ \d+, \d{4})"
)

# One-off entries confirmed by directly loading the LMS meeting page (its
# <title>), not sourced from a subscription email -- used only when a
# meeting's notification was missed because this mailbox's subscriptions
# were off (fixed 2026-11-13; see the "Select all" state bug noted that
# day). Remove an entry once its own email shows up normally, or once its
# date has passed and it's no longer in the window.
MANUALLY_VERIFIED_MEETINGS = [
    {"meeting_body": "City Council", "agenda_kind": "Confirmed directly (not yet in subscription mail)",
     "url": "https://lms.lacity.gov/meetings/2026-11-13-council", "meeting_date_iso": "2026-11-13",
     "verified_directly": True},
]


def load_upcoming(conn, today):
    """Upcoming meeting agendas from LMS subscription emails, today through
    WINDOW_DAYS ahead. Switched 2026-11-13 from the old PrimeGov-based
    outbox/hearing_alert pipeline, which went silent after the City Clerk's
    Oct 26 migration to LMS (PrimeGov's own API returns an empty list now,
    confirmed directly) -- the LMS subscription emails are the real,
    currently-working source. This covers every subscribed meeting body,
    not just ones with a tracked file on the agenda; the old pipeline only
    ever covered tracked files, which is why this is a meaningfully wider
    and more useful Calendar than before, not just a swapped data source.
    """
    cur = conn.cursor()
    cur.execute("SELECT subject, received_at, links_json FROM emails WHERE source = 'lms_meeting_agenda'")
    seen = {}  # dedupe by (body, date), keep most recently received
    for subject, received_at, links_json in cur.fetchall():
        m = MEETING_SUBJECT_RE.search(subject or "")
        if not m:
            continue
        kind, body, date_str = m.groups()
        mdate = parse_meeting_date(date_str)
        if mdate is None:
            continue
        links = json.loads(links_json) if links_json else []
        url = next((u for u in links if "/meetings/" in u), (links[0] if links else None))
        key = (body, mdate.isoformat())
        if key not in seen or received_at > seen[key][1]:
            seen[key] = ({"meeting_body": body, "agenda_kind": kind, "url": url}, received_at, mdate)

    window_end = today + datetime.timedelta(days=WINDOW_DAYS)
    upcoming = [
        {**payload, "meeting_date_iso": mdate.isoformat()}
        for payload, _received, mdate in seen.values()
        if today <= mdate <= window_end
    ]
    seen_keys = {(u["meeting_body"], u["meeting_date_iso"]) for u in upcoming}
    for manual in MANUALLY_VERIFIED_MEETINGS:
        d = datetime.date.fromisoformat(manual["meeting_date_iso"])
        key = (manual["meeting_body"], manual["meeting_date_iso"])
        if today <= d <= window_end and key not in seen_keys:
            upcoming.append(manual)
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


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex">
<title>City Watch</title>
<style>
  :root {{
    --bg:#f6f5f2; --panel:#fff; --text:#1f1d1a; --muted:#6b665e; --border:#ddd8cf;
    --accent:#1d4ed8; --accent-dark:#1e3a8a; --radius:8px;
    --amber-bg:#fdf0d5; --amber-text:#92600a; --amber-border:#eecb8a;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg:#17150f; --panel:#221f19; --text:#efe9df; --muted:#a39c8e; --border:#3a352c;
      --accent:#6c93ff; --accent-dark:#3a5ecc;
      --amber-bg:#3a2e14; --amber-text:#f0c374; --amber-border:#5c4a22;
      color-scheme: dark;
    }}
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{ margin:0; background:var(--bg); color:var(--text); font-family:-apple-system,"Helvetica Neue",Arial,sans-serif;
          font-size:16px; line-height:1.5; padding-bottom: env(safe-area-inset-bottom, 0px); }}
  header {{ padding:1.1rem 1.1rem 0.9rem; background:var(--panel); border-bottom:1px solid var(--border);
            padding-top: calc(1.1rem + env(safe-area-inset-top, 0px)); }}
  header h1 {{ margin:0 0 0.2rem; font-size:1.3rem; }}
  .asof {{ color:var(--muted); font-size:0.85rem; }}
  .headline-count {{ margin-top:0.6rem; padding:0.6rem 0.8rem; border-radius:var(--radius); font-weight:700;
                      font-size:0.95rem; background:var(--accent); color:#fff; }}
  .headline-count.zero {{ background:var(--amber-bg); color:var(--amber-text); border:1px solid var(--amber-border); font-weight:600; }}

  .jumpbar {{ position: sticky; top: 0; z-index: 10; display:flex; gap:0.5rem; padding:0.6rem 1.1rem;
              background:var(--panel); border-bottom:1px solid var(--border); overflow-x:auto; }}
  .jumpbar a {{ flex: 1 1 0; text-align:center; white-space:nowrap; min-height:44px; display:flex; align-items:center;
                justify-content:center; padding:0 0.75rem; border:1px solid var(--border); border-radius:var(--radius);
                color:var(--text); text-decoration:none; font-size:0.92rem; font-weight:600; background:var(--bg); }}
  .jumpbar a.important {{ border-color:var(--accent); color:var(--accent); }}
  .jumpbar a:active {{ background:var(--border); }}

  main {{ padding: 1.1rem; max-width: 640px; margin: 0 auto; }}
  section {{ margin-bottom: 1.75rem; scroll-margin-top: 4.5rem; }}
  .section-head {{ display:flex; align-items:baseline; justify-content:space-between; margin-bottom:0.7rem; }}
  .section-head h2 {{ margin:0; font-size:1.1rem; }}
  .count {{ color:var(--muted); font-size:0.85rem; }}

  .card {{ background:var(--panel); border:1px solid var(--border); border-radius:var(--radius);
           padding:1rem 1.1rem; margin-bottom:0.7rem; }}
  .card h3 {{ margin:0 0 0.35rem; font-size:1rem; line-height:1.35; }}
  .card h3 a {{ color:var(--text); text-decoration:none; }}
  .card h3 a:hover {{ color:var(--accent); }}
  .meta {{ color:var(--muted); font-size:0.82rem; margin-bottom:0.3rem; }}
  #calendar .card {{ border-left:4px solid var(--accent); padding:1.1rem 1.25rem; }}
  #calendar .card h3 {{ font-size:1.08rem; }}
  #calendar .meta-date {{ display:inline-block; font-weight:700; color:var(--accent); font-size:0.95rem; margin-bottom:0.3rem; }}
  .empty {{ background:var(--amber-bg); color:var(--amber-text); border:1px solid var(--amber-border);
            border-radius:var(--radius); padding:0.9rem 1rem; font-size:0.92rem; }}

  input[type=search] {{ width:100%; min-height:48px; padding:0.6rem 0.9rem; border:1px solid var(--border);
                         border-radius:var(--radius); font-size:1rem; margin-bottom:0.9rem; background:var(--panel);
                         color:var(--text); }}
  button.top {{ display:block; margin: 0 auto; min-height:40px; padding:0 1rem; border:1px solid var(--border);
                border-radius:var(--radius); background:var(--panel); color:var(--text); font-size:0.88rem; }}
</style>
</head>
<body>
<header>
  <h1>City Watch</h1>
  <div class="asof">As of {as_of}</div>
  <div class="headline-count{headline_class}">{headline_text}</div>
</header>
<nav class="jumpbar">
  <a href="#calendar" class="important">Calendar</a>
  <a href="#watchlist">Watchlist</a>
  <a href="#search">Search</a>
</nav>
<main>

<section id="calendar">
  <div class="section-head"><h2>Calendar</h2><span class="count">next {window_days} days</span></div>
  {calendar_body}
</section>

<section id="watchlist">
  <div class="section-head"><h2>Watchlist</h2><span class="count">{watchlist_count} tracked</span></div>
  {watchlist_body}
</section>

<section id="search">
  <div class="section-head"><h2>Search</h2><span class="count">{search_count} records</span></div>
  <input type="search" id="q" placeholder="Search documents, emails, public comments…">
  <div id="results"></div>
</section>

<button class="top" onclick="window.scrollTo({{top:0,behavior:'smooth'}})">Back to top</button>

</main>
<script src="https://cdnjs.cloudflare.com/ajax/libs/lunr.js/2.3.9/lunr.min.js"></script>
<script>
let idx, docsById = {{}};
fetch('search-index.json').then(r => r.json()).then(data => {{
  docsById = Object.fromEntries(data.map(d => [d.id, d]));
  idx = lunr(function () {{
    this.ref('id'); this.field('title'); this.field('body'); this.field('cfnumber');
    data.forEach(d => this.add(d));
  }});
}});
function esc(s) {{ const d = document.createElement('div'); d.textContent = s || ''; return d.innerHTML; }}
document.getElementById('q').addEventListener('input', e => {{
  const q = e.target.value.trim();
  const out = document.getElementById('results');
  if (!idx || q.length < 2) {{ out.innerHTML = ''; return; }}
  let hits;
  try {{ hits = idx.search(q); }} catch (err) {{ hits = idx.search(q.replace(/[*:^~+\\-]/g, ' ')); }}
  if (!hits.length) {{ out.innerHTML = '<p class="empty">No matches.</p>'; return; }}
  out.innerHTML = hits.slice(0, 40).map(h => {{
    const d = docsById[h.ref];
    return `<div class="card"><h3>${{esc(d.title)}}</h3><div class="meta">${{esc(d.kind)}} · ${{esc(d.cfnumber || 'no CF')}} · ${{esc(d.date || 'undated')}}</div></div>`;
  }}).join('');
}});
</script>
</body>
</html>
"""


def render_calendar(upcoming, watchlist):
    if upcoming:
        return "".join(
            '<div class="card"><div class="meta-date">{mdate}{today_tag}</div>'
            '<h3><a href="{url}" target="_blank">{body}</a></h3>'
            '<div class="meta">{kind}</div></div>'.format(
                url=html.escape(it.get("url") or "#"),
                body=html.escape(it["meeting_body"]),
                mdate=html.escape(it["meeting_date_iso"]),
                today_tag=" — TODAY" if it["meeting_date_iso"] == datetime.date.today().isoformat() else "",
                kind=html.escape(it["agenda_kind"]),
            ) for it in upcoming
        )
    return (f'<p class="empty">No upcoming meeting agendas found in the LMS subscription mail, '
            f'searched {AS_OF}, window through {WINDOW_DAYS} days ahead. This means the subscribed inbox has '
            f'nothing posted for this window yet — check the LMS mail pipeline is still running if this '
            f'persists, since an empty calendar can mean a quiet window or a broken feed and this page can\'t '
            f'tell you which.</p>')


def headline(upcoming):
    if upcoming:
        n = len(upcoming)
        return "", f"{n} upcoming meeting agenda{'s' if n != 1 else ''} posted"
    return " zero", "Nothing upcoming right now — see Calendar below for why"


def render_watchlist(watchlist, statuses):
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
    return "".join(rows) if rows else '<p class="empty">Nothing tracked yet.</p>'


def write_page(watchlist, statuses, upcoming, search_docs):
    headline_class, headline_text = headline(upcoming)
    page = PAGE.format(
        as_of=AS_OF, window_days=WINDOW_DAYS,
        headline_class=headline_class, headline_text=html.escape(headline_text),
        calendar_body=render_calendar(upcoming, watchlist),
        watchlist_count=len(watchlist), watchlist_body=render_watchlist(watchlist, statuses),
        search_count=len(search_docs),
    )
    with open(os.path.join(OUT_DIR, "index.html"), "w") as f:
        f.write(page)
    for stale in ("calendar.html", "search.html"):
        p = os.path.join(OUT_DIR, stale)
        if os.path.exists(p):
            os.remove(p)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    watchlist = load_watchlist()
    today = datetime.date.today()

    statuses = load_tracked_status(conn, watchlist)
    upcoming = load_upcoming(conn, today)
    search_docs = build_search_index(conn)

    write_page(watchlist, statuses, upcoming, search_docs)
    with open(os.path.join(OUT_DIR, "search-index.json"), "w") as f:
        json.dump(search_docs, f)

    print(f"Exported {len(watchlist)} tracked files, {len(upcoming)} upcoming items, "
          f"{len(search_docs)} searchable records, as of {AS_OF}")


if __name__ == "__main__":
    main()
