#!/usr/bin/env python3
"""Client for LA City's PrimeGov meeting portal (lacity.primegov.com).
Public, unauthenticated JSON API for meeting metadata + video URLs, plus a
server-rendered HTML agenda page per meeting with item-level council-file
numbers, official recommendation text, and (coarse) video-timestamp anchors.
"""
import html
import json
import re
import time
import urllib.error
import urllib.request

BASE = "https://lacity.primegov.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError):
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}")


def _clean(text):
    text = html.unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def list_meetings_for_year(year):
    """All meetings (any committee) published for a given year."""
    url = f"{BASE}/api/v2/PublicPortal/ListArchivedMeetings?year={year}"
    return json.loads(_get(url))


def list_upcoming_meetings():
    url = f"{BASE}/api/v2/PublicPortal/ListUpcomingMeetings"
    return json.loads(_get(url))


# Preference order for which agenda document to read. A meeting doesn't
# always publish a plain "HTML Agenda" -- a same-day revision, a special
# session, or a continuation replaces or adds to it under a different
# template name. Missing these silently dropped ~50 of 571 2026 meetings
# (178 -> 228 matchable) from recommendation_diff's search entirely, which
# read as "not on any nearby agenda" rather than the template-name miss it
# actually was.
AGENDA_TEMPLATE_PRIORITY = [
    "HTML Agenda", "HTML Revised Agenda", "HTML Special Agenda", "HTML Continuation Agenda",
]


def html_agenda_template_id(meeting):
    """Find the best available agenda document's templateId for a meeting
    record -- that templateId is what Portal/Meeting?meetingTemplateId=
    expects. Tries AGENDA_TEMPLATE_PRIORITY in order, not just "HTML Agenda"."""
    docs = {doc.get("templateName"): doc.get("templateId") for doc in meeting.get("documentList", [])}
    for name in AGENDA_TEMPLATE_PRIORITY:
        if name in docs:
            return docs[name]
    return None


def fetch_agenda_by_template_id(template_id):
    """Fetch and parse an agenda page directly by its templateId. Lower-level
    primitive that fetch_agenda(meeting) builds on -- exposed separately
    because ENS emails hand us this exact templateId straight in the link
    (lacity.primegov.com/Portal/Meeting?meetingTemplateId=N), a stable, exact
    reference that doesn't need resolving through a meeting record at all."""
    url = f"{BASE}/Portal/Meeting?meetingTemplateId={template_id}"
    html_text = _get(url)

    items = []
    # Each agenda item is a <div class='meeting-item' data-itemid=... data-videolocation=...>
    item_blocks = re.split(r"(?=<div[^>]*class='meeting-item')", html_text)
    for block in item_blocks:
        if "class='meeting-item'" not in block:
            continue

        cf_m = re.search(r'<td colspan="2"[^>]*>\s*([0-9]{2}-[0-9]{4}(?:-S[0-9]+)?)\s*</td>', block)
        videoloc_m = re.search(r'data-videolocation="(\d+)"', block)
        title_m = re.search(
            r'<div style="display:block;text-align:justify;font-size:12pt;font-family:Arial,Helvetica,sans-serif;">(.*?)</div>',
            block, re.S)
        rec_m = re.search(
            r'<p style="margin:0px!important;padding:0px!important;text-align:justify">(.*?)</p>',
            block, re.S)

        if not cf_m and not title_m:
            continue

        rec_text = _clean(rec_m.group(1)) if rec_m else None
        if rec_text and rec_text.startswith("Recommendation for Council action:"):
            rec_text = rec_text[len("Recommendation for Council action:"):].strip()

        items.append({
            "cfnumber": cf_m.group(1) if cf_m else None,
            "title": _clean(title_m.group(1)) if title_m else None,
            "recommendation": rec_text,
            "video_seconds": int(videoloc_m.group(1)) if videoloc_m else None,
        })

    return items


def fetch_agenda(meeting):
    """Fetch and parse a meeting's rendered HTML agenda: list of items with
    cfnumber, title, recommendation text, document links, and a coarse
    video-timestamp anchor (seconds into the stream) where available."""
    template_id = html_agenda_template_id(meeting)
    if template_id is None:
        return []
    return fetch_agenda_by_template_id(template_id)


if __name__ == "__main__":
    import sys
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    meetings = list_meetings_for_year(year)
    print(f"{len(meetings)} meetings in {year}")
    if len(sys.argv) > 2 and sys.argv[2] == "agenda":
        mid = int(sys.argv[3])
        meeting = next(m for m in meetings if m["id"] == mid)
        items = fetch_agenda(meeting)
        print(json.dumps(items, indent=2))
