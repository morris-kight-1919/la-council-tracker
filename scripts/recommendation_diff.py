#!/usr/bin/env python3
"""Track a council file's official recommendation text across each meeting
appearance (via PrimeGov), and diff consecutive versions. See
TEXT_DIFFING_EXPLAINED.md for the full concept."""
import datetime
import difflib
import json
import os

from primegov_client import list_meetings_for_year, fetch_agenda

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
VERSIONS_DIR = os.path.join(ROOT, "data", "recommendation_versions")
MATCH_LOG_PATH = os.path.join(ROOT, "data", "recommendation_match_log.jsonl")

# City Watch.md Section III: this is a fuzzy date-window match, not a join on
# a stable identifier -- it can silently degrade to a 0% match rate and look
# identical to "nothing to diff." Log every attempt so that's auditable.

# Document titles worth chasing down a matching meeting for -- these are the
# ones that typically carry recommendation text. Others (speaker cards,
# public communications, impact statements) are skipped.
HEARING_DOC_KEYWORDS = ["committee report", "council action", "report from", "motion"]

SEARCH_WINDOW_MEETINGS = 15  # nearest-by-date meetings to check before giving up


def is_hearing_doc(title):
    t = (title or "").lower()
    return any(kw in t for kw in HEARING_DOC_KEYWORDS)


def _parse_meeting_date(m):
    try:
        return datetime.datetime.strptime(m["date"], "%b %d, %Y").date()
    except (ValueError, KeyError):
        return None


def _parse_doc_date(date_str):
    try:
        return datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return None


def _log_match_attempt(cfnumber, doc_date_str, found, meetings_checked, reason=None):
    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "cfnumber": cfnumber,
        "doc_date": doc_date_str,
        "found": found,
        "meetings_checked": meetings_checked,
        "reason": reason,
    }
    os.makedirs(os.path.dirname(MATCH_LOG_PATH), exist_ok=True)
    with open(MATCH_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def recent_match_rate(limit=50):
    """For health_check.py: fraction of recent match attempts that found a
    result. A rate that drops to 0 over a meaningful sample is the exact
    'join that silently stopped working' failure mode -- surface it, don't
    let it sit quiet."""
    if not os.path.exists(MATCH_LOG_PATH):
        return None
    lines = open(MATCH_LOG_PATH).readlines()[-limit:]
    if not lines:
        return None
    entries = [json.loads(l) for l in lines if l.strip()]
    if not entries:
        return None
    found_count = sum(1 for e in entries if e["found"])
    return {"attempts": len(entries), "found": found_count, "rate": found_count / len(entries)}


def find_recommendation(cfnumber, doc_date_str):
    """Search meetings nearest to doc_date_str for an agenda item matching
    this CF number. Returns (meeting, item) or (None, None). Every attempt
    is logged (see _log_match_attempt) regardless of outcome."""
    target = _parse_doc_date(doc_date_str)
    if target is None:
        _log_match_attempt(cfnumber, doc_date_str, False, 0, reason="unparseable doc_date")
        return None, None

    years_to_check = {target.year}
    if target.month == 1:
        years_to_check.add(target.year - 1)
    if target.month == 12:
        years_to_check.add(target.year + 1)

    candidates = []
    for year in years_to_check:
        for m in list_meetings_for_year(year):
            # "- SAP" listings are video-only companions to the real meeting
            # (no agenda ever) -- they never match and just burn search
            # budget. Skip for now; revisit if SAP content is ever needed.
            if "- SAP" in (m.get("title") or ""):
                continue
            mdate = _parse_meeting_date(m)
            if mdate:
                candidates.append((abs((mdate - target).days), m))
    candidates.sort(key=lambda x: x[0])

    checked = 0
    for _, meeting in candidates[:SEARCH_WINDOW_MEETINGS]:
        checked += 1
        items = fetch_agenda(meeting)
        for item in items:
            if item.get("cfnumber") == cfnumber and item.get("recommendation"):
                _log_match_attempt(cfnumber, doc_date_str, True, checked)
                return meeting, item

    _log_match_attempt(cfnumber, doc_date_str, False, checked,
                        reason=f"no matching agenda item in nearest {checked} meeting(s)")
    return None, None


def capture_and_diff(cfnumber, doc_title, doc_date_str):
    """If this looks like a hearing document, try to find + store a new
    recommendation-text version, and diff against the prior version if one
    exists. Returns diff text (str) or None."""
    if not is_hearing_doc(doc_title):
        return None

    meeting, item = find_recommendation(cfnumber, doc_date_str)
    if item is None:
        return None

    os.makedirs(VERSIONS_DIR, exist_ok=True)
    path = os.path.join(VERSIONS_DIR, f"{cfnumber}.json")
    versions = json.load(open(path)) if os.path.exists(path) else []

    new_text = item["recommendation"]
    if versions and versions[-1]["text"] == new_text:
        return None  # unchanged

    versions.append({
        "date": doc_date_str,
        "meeting_id": meeting["id"],
        "text": new_text,
    })
    json.dump(versions, open(path, "w"), indent=1)

    if len(versions) < 2:
        return None  # first version seen, nothing to diff against yet

    old_text, new_text = versions[-2]["text"], versions[-1]["text"]
    diff = list(difflib.unified_diff(
        old_text.split(". "), new_text.split(". "),
        lineterm="", fromfile=f"before ({versions[-2]['date']})", tofile=f"after ({versions[-1]['date']})",
    ))
    if not diff:
        return None
    return "\n".join(diff)


if __name__ == "__main__":
    import sys
    diff = capture_and_diff(sys.argv[1], sys.argv[2], sys.argv[3])
    print(diff or "(no diff)")
