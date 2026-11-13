#!/usr/bin/env python3
"""Client for the LA City Council's Legislative Management System (LMS),
which replaced the old CFMS (cityclerk.lacity.org/lacityclerkconnect) for
Council File data on 2026-10-26 -- the old URL now just 302s to
lms.lacity.gov/files/<CF> (confirmed live 2026-11-05).

The new site can't be scraped with a plain urllib/curl GET: it sits behind
an AWS WAF JS challenge (a bare request gets back an empty 202 with header
`x-amzn-waf-action: challenge`, confirmed live), and the page itself is a
client-rendered Next.js app, not server HTML. A real browser is required.
Once Playwright solves the challenge for one page load, that browser
context's cookies carry it for every later load in the same context, so
this module keeps one Chromium instance alive per process (lazily started
on first use, closed via atexit) instead of paying the challenge cost on
every fetch -- confirmed live: first load ~2s, every later load in the
same context ~1.4s regardless of which Council File.

Playwright's sync API is not meant to be driven from more than one thread
at a time; fine for this tool's single-worker local Flask dev server and
single-process CLI scripts, but don't call this from multiple threads.
"""
import atexit
import datetime
import re

from bs4 import BeautifulSoup

LMS_BASE = "https://lms.lacity.gov"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

_playwright = None
_browser = None
_context = None


def _ensure_context():
    global _playwright, _browser, _context
    if _context is not None:
        return _context
    from playwright.sync_api import sync_playwright
    _playwright = sync_playwright().start()
    _browser = _playwright.chromium.launch(headless=True)
    _context = _browser.new_context(user_agent=UA)
    atexit.register(_shutdown)
    return _context


def _shutdown():
    global _playwright, _browser, _context
    try:
        if _browser is not None:
            _browser.close()
        if _playwright is not None:
            _playwright.stop()
    except Exception:
        pass
    _browser = _context = _playwright = None


def _get_soup(url, timeout_ms=30000):
    ctx = _ensure_context()
    page = ctx.new_page()
    try:
        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("text=Information", timeout=15000)
        except Exception:
            pass  # not-found / search-result pages have no Information block
        html_text = page.content()
    finally:
        page.close()
    return BeautifulSoup(html_text, "lxml")


def _dl_to_dict(dl):
    """Both detail-panel <dl>s ("Dates and deadlines", "Information") render
    as <dl><div><dt>Label</dt><dd>Value</dd></div>...</dl>, but *which*
    labels appear varies by file type (a motion has Mover/Seconded
    by/District; a budget item has Districts/Originating entity; some add
    Expiration date) -- so these are parsed generically into a dict rather
    than hard-coded, and the fixed top-level keys below just look up
    whichever label variant is actually present."""
    out = {}
    for row in dl.find_all("div", recursive=False):
        dt = row.find("dt")
        dd = row.find("dd")
        if dt and dd:
            out[dt.get_text(strip=True)] = dd.get_text(" ", strip=True)
    return out


_DOC_DATE_RE = re.compile(r"([A-Za-z]{3,9}\.?\s+\d{1,2},\s+\d{4})\s*$")


def _parse_documents(soup):
    """The Documents tab renders the SAME document link twice -- once inside
    the "All documents" section, again inside its category-specific section
    (e.g. "Reports & Analysis") -- both present in the DOM at once, only one
    visible per the category-filter UI state. Scope to "All documents" so
    each document is listed once; aria-label dates ("Jan 1, 2003") contain
    their own comma, so the title/date split can't just be the last comma --
    it looks for the trailing "<Month> <day>, <year>" instead."""
    documents = []
    panel = soup.find(id=lambda i: i and i.endswith("content-documents"))
    if not panel:
        return documents
    section = panel.find("section", attrs={"aria-label": "All documents"}) or panel
    seen = set()
    for a in section.find_all("a", attrs={"data-usage": "document_open"}):
        label = a.get("aria-label", "")
        # Trailing metadata after the date varies ("PDF, opens in new tab"
        # vs "PDF, 3 pages, opens in new tab") -- "PDF" itself is the
        # stable marker, so cut there rather than matching the whole tail.
        label = re.split(r",\s*PDF\b", label, maxsplit=1)[0].strip()
        date_m = _DOC_DATE_RE.search(label)
        if date_m:
            doc_date = date_m.group(1)
            doc_title = label[:date_m.start()].rstrip(", ").strip()
        else:
            doc_title, doc_date = label, None
        url = a.get("href")
        key = (doc_title, url)
        if key in seen:
            continue
        seen.add(key)
        documents.append({
            "title": doc_title or a.get_text(strip=True),
            "url": url,
            "date": doc_date,
        })
    return documents


def _parse_timeline(soup):
    activities = []
    panel = soup.find(id=lambda i: i and i.endswith("content-timeline"))
    if not panel:
        return activities
    for li in panel.find_all("li"):
        date_span = li.find("span", class_="tabular-nums")
        desc = li.find("p")
        if date_span and desc:
            activities.append({
                "date": date_span.get_text(strip=True),
                "activity": desc.get_text(" ", strip=True),
            })
    return activities


def _parse_votes(soup):
    """A file can have activity across multiple meeting dates, and a single
    meeting date can carry more than one vote event (e.g. a committee vote
    and a council vote the same day) -- each renders as its own collapsible
    block inside that meeting's <section>."""
    vote_events = []
    panel = soup.find(id=lambda i: i and i.endswith("content-votes"))
    if not panel:
        return vote_events
    for section in panel.find_all("section", attrs={"aria-label": re.compile(r"^Council meeting of")}):
        meeting_date = re.sub(r"^Council meeting of\s*", "", section["aria-label"]).strip()
        for vote_div in section.find_all("div", class_=lambda c: c and "group/vote" in c):
            action_span = vote_div.find(
                "span", class_=lambda c: c and "font-medium" in c and "text-text-primary" in c)
            tally_span = vote_div.find("span", class_=lambda c: c and "ml-auto" in c)
            agenda_item, moved_seconded = None, None
            for p in vote_div.find_all("p"):
                t = p.get_text(" ", strip=True)
                if t.lower().startswith("agenda item"):
                    agenda_item = t
                elif t.lower().startswith("moved by"):
                    moved_seconded = t
            members = []
            for li in vote_div.find_all("li"):
                spans = li.find_all("span")
                if len(spans) >= 2:
                    members.append({
                        "name": spans[0].get_text(strip=True),
                        "vote": spans[1].get_text(strip=True),
                    })
            if members:
                vote_events.append({
                    "meeting_date": meeting_date,
                    "meeting_type": None,
                    "vote_action": action_span.get_text(strip=True) if action_span else None,
                    "vote_given": tally_span.get_text(strip=True) if tally_span else None,
                    "agenda_item": agenda_item,
                    "moved_seconded": moved_seconded,
                    "members": members,
                })
    return vote_events


def fetch_record(cfnumber):
    """Fetch and parse a single council file's detail page from LMS."""
    url = f"{LMS_BASE}/files/{cfnumber}"
    soup = _get_soup(url)

    # Next.js embeds the not-found route's prefetch payload as RSC flight
    # data inside a <script> tag on every page, found pages included -- so
    # the real page's actual <h1> (not a string search over the whole
    # document) is what distinguishes a true 404 from a found record.
    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else None
    if title == "Council file not found" or not soup.find(id="overview"):
        return None

    status, status_detail, related_to = None, None, None
    overview = soup.find(id="overview")
    if overview:
        badge = overview.find("span", attrs={"aria-label": True})
        if badge:
            parts = [p.strip() for p in badge["aria-label"].split(",", 1)]
            status = parts[0] if parts else None
            status_detail = parts[1] if len(parts) > 1 else None
        rel_a = overview.find("a", href=re.compile(r"^/files/"))
        if rel_a:
            related_to = rel_a.get_text(" ", strip=True)

    fields = {}
    for dl in soup.find_all("dl"):
        fields.update(_dl_to_dict(dl))

    vote_events = _parse_votes(soup)
    # vote_1 (index 0) is the most recent -- keep `vote` as that single
    # event for existing callers (watch.py's "last vote" display), and
    # `vote_history` for every event, same shape the old CFMS client used.
    vote = vote_events[0] if vote_events else None

    return {
        "cfnumber": cfnumber,
        "title": title,
        "status": status,
        "status_detail": status_detail,
        "related_to": related_to,
        "date_received": fields.get("Date received"),
        "last_changed": fields.get("Last changed") or fields.get("Last changed date"),
        "expiration": fields.get("Expiration date") or fields.get("Expiration"),
        "council_district": fields.get("District") or fields.get("Districts"),
        "mover": fields.get("Mover"),
        "second": fields.get("Seconded by"),
        "fields": fields,
        "activities": _parse_timeline(soup),
        "documents": _parse_documents(soup),
        "vote": vote,
        "vote_history": vote_events,
        "url": url,
        "retrieved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }


def search(keyword=None, committee_code=None, searchtype="2", max_results=500):
    """NOTE (2026-11-05): not yet migrated to LMS. The old CFMS
    vcfi.doSearch POST endpoint this used is retired along with the rest of
    CFMS, and would hit the same AWS WAF challenge as fetch_record() did --
    this was not part of the reported bug (watch.add()/fetch_record()) and
    is being left as a known follow-up rather than guessed at. Returns []
    rather than raising, so discover.py fails quiet, not loud, until it's
    rebuilt against lms.lacity.gov/search."""
    return []


if __name__ == "__main__":
    import json
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "search":
        for r in search(keyword=sys.argv[2]):
            print(r["cfnumber"], "|", r["last_change"], "|", r["title"][:80])
    elif len(sys.argv) > 1:
        rec = fetch_record(sys.argv[1])
        print(json.dumps(rec, indent=2))
        _shutdown()
