"""Sample data for SEED_MODE.

Lets every page be reviewed with realistic content before the app is
connected to the real pipeline (corpus.db / watchlist.json / data/*.json).
Nothing here reads or writes those real files -- this module is entirely
self-contained, plain Python data.

Shapes deliberately mirror what the real queries will return once the next
pass connects this to corpus.db, so swapping seed data for real data later
is a change to the route functions, not to the templates.
"""
import datetime

_TODAY = datetime.date.today()
_NOW = datetime.datetime.now().replace(microsecond=0)


def _iso_date(days_offset):
    return (_TODAY + datetime.timedelta(days=days_offset)).isoformat()


def _iso_dt(hours_offset=0, days_offset=0):
    return (_NOW - datetime.timedelta(hours=hours_offset, days=-days_offset)).isoformat(timespec="minutes")


COUNCIL_FILE = {
    "cfnumber": "26-0386",
    "title": "Elysian Park — tree removal and habitat restoration review",
    "official_url": "https://cityclerk.lacity.org/lacityclerkconnect/index.cfm?fa=ccfi.viewrecord&cfnumber=26-0386",
    "status": "SCHEDULED_FOR_COUNCIL",
    "status_label": "Scheduled for Council",
    "status_as_of": _iso_date(-2),
    "status_detail": "City Clerk scheduled item for Council on " + _iso_date(9) + ".",
    "next_event_date": _iso_date(9),
    "next_event_label": "City Council Meeting",
    "committee": "Energy and Environment Committee",
    "why_watched": "Auto-discovered: keyword “Elysian Park”",
    "confidence": "Auto-discovered — needs review",
    "watched": True,
    "last_checked": _iso_dt(),
}

TIMELINE = [
    {
        "date": _iso_date(-2),
        "event_type": "Scheduled",
        "description": "City Clerk scheduled item for Council on " + _iso_date(9) + ".",
        "source_title": "City Clerk Connect — activity log",
        "source_url": COUNCIL_FILE["official_url"],
        "retrieved_at": _iso_dt(),
        "confidence": None,
        "why_linked": None,
    },
    {
        "date": _iso_date(-6),
        "event_type": "Committee action",
        "description": "Energy and Environment Committee approved item(s).",
        "source_title": "City Clerk Connect — activity log",
        "source_url": COUNCIL_FILE["official_url"],
        "retrieved_at": _iso_dt(),
        "confidence": None,
        "why_linked": None,
    },
    {
        "date": _iso_date(-19),
        "event_type": "Referred",
        "description": "Motion referred to Energy and Environment Committee.",
        "source_title": "City Clerk Connect — activity log",
        "source_url": COUNCIL_FILE["official_url"],
        "retrieved_at": _iso_dt(),
        "confidence": "needs review",
        "why_linked": "Agenda text for this item mentions “Elysian Park”, "
                      "matching the watch rule “Elysian Park monitoring.”",
    },
]

DOCUMENTS = [
    {
        "title": "Committee Report",
        "doc_type": "committee_report",
        "doc_date": _iso_date(-6),
        "source_url": "https://cityclerk.lacity.org/onlinedocs/2026/26-0386_rpt_EE_08-02-26.pdf",
        "retrieved_at": _iso_dt(),
    },
]

COMMENTS = []  # none in this example -- the Public Comments tab renders its own empty state

INBOX_ITEM = {
    "id": 1,
    "source_title": "08/05/2026 — Journal/Actions — Energy and Environment Committee",
    "received_at": _iso_dt(hours_offset=14),
    "excerpt_before": "...report recommends approval of the tree removal permit at ",
    "excerpt_match": "Elysian Park",
    "excerpt_after": " subject to habitat restoration conditions outlined in Attachment B...",
    "proposed_cfnumber": "26-0386",
    "proposed_title": COUNCIL_FILE["title"],
    "confidence": "Likely match",
    "why_matched": "Matched watch rule “Elysian Park monitoring” — "
                   "keywords: Elysian Park, habitat restoration",
    "source_url": COUNCIL_FILE["official_url"],
    "candidates": ["26-0386"],
}

SOURCE_PROBLEM = {
    "script": "fetch_ens_links",
    "status": "error",
    "timestamp": _iso_dt(hours_offset=3),
    "message": "Could not fetch one linked PrimeGov agenda (timed out after 3 attempts).",
}

COMMENT_DEADLINE = {
    "cfnumber": COUNCIL_FILE["cfnumber"],
    "status": "open",
    "deadline": _iso_date(4),
    "note": "Example only — public-comment deadline tracking isn't built into the "
            "real pipeline yet. This card exists to check the page layout, not "
            "because this particular deadline is real.",
}

SOURCES = [
    {
        "source_type": "email",
        "title": INBOX_ITEM["source_title"],
        "published_at": INBOX_ITEM["received_at"],
        "retrieved_at": _iso_dt(),
        "source_url": None,
        "linked_cfnumbers": ["26-0386"],
        "extraction_state": "complete",
        "preview": INBOX_ITEM["excerpt_before"] + INBOX_ITEM["excerpt_match"] + INBOX_ITEM["excerpt_after"],
    },
    {
        "source_type": "document",
        "title": DOCUMENTS[0]["title"],
        "published_at": DOCUMENTS[0]["doc_date"],
        "retrieved_at": DOCUMENTS[0]["retrieved_at"],
        "source_url": DOCUMENTS[0]["source_url"],
        "linked_cfnumbers": ["26-0386"],
        "extraction_state": "complete",
        "preview": "Committee recommends approval of the tree removal permit at "
                   "Elysian Park subject to habitat restoration conditions...",
    },
]


def get_watchlist():
    return [COUNCIL_FILE]


def get_council_file(cfnumber):
    return COUNCIL_FILE if cfnumber == COUNCIL_FILE["cfnumber"] else None


def get_timeline(cfnumber):
    return TIMELINE if cfnumber == COUNCIL_FILE["cfnumber"] else []


def get_documents(cfnumber):
    return DOCUMENTS if cfnumber == COUNCIL_FILE["cfnumber"] else []


def get_comments(cfnumber):
    return COMMENTS if cfnumber == COUNCIL_FILE["cfnumber"] else []


def get_inbox_items():
    return [INBOX_ITEM]


def get_sources():
    return SOURCES


def get_source_problems():
    return [SOURCE_PROBLEM]


def get_comment_deadlines():
    return [COMMENT_DEADLINE]


def get_upcoming_hearings():
    return [{
        "cfnumber": COUNCIL_FILE["cfnumber"],
        "title": COUNCIL_FILE["title"],
        "meeting_title": COUNCIL_FILE["next_event_label"],
        "meeting_date": COUNCIL_FILE["next_event_date"],
        "official_url": COUNCIL_FILE["official_url"],
    }]


def get_recent_changes():
    return [{
        "cfnumber": COUNCIL_FILE["cfnumber"],
        "title": COUNCIL_FILE["title"],
        "summary": TIMELINE[0]["description"],
        "when": TIMELINE[0]["date"],
    }]
