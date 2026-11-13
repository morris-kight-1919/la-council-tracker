#!/usr/bin/env python3
"""Derive where a council file sits in its lifecycle, from its activity log.

Patterns below were derived from the actual corpus (622 activity lines, 196
distinct shapes across the watchlist), not guessed. The real vocabulary looks
like:

    Motion referred to Housing and Homelessness Committee.
    Homelessness and Poverty Committee scheduled item for committee meeting on October N, N.
    Housing and Homelessness Committee approved item(s) .
    City Clerk scheduled item for Council on June N, N.
    Council adopted item forthwith.
    City Clerk transmitted file to Mayor. Last day for Mayor to act is July N, N.
    Council action final.
    File expired per Council policy, Council file No. N-N.

Why stages rather than raw activity lines: a raw log tells you *what* happened;
a stage tells you *where the thing is*, which is what makes "what's sitting in
committee right now?" a query and "this just moved to Council" a notification.

Note: activities come newest-first from cf_client.
"""
import re

# (stage, priority_rank, compiled pattern). Higher rank wins when several
# match -- a file that was referred to committee and later adopted is adopted.
STAGE_PATTERNS = [
    ("EXPIRED",               90, r"\bfile expired\b"),
    ("FINAL",                 80, r"\bcouncil action final\b"),
    ("WITH_MAYOR",            70, r"\btransmitted file to mayor\b|\blast day for mayor to act\b"),
    ("COUNCIL_ADOPTED",       60, r"\bcouncil adopted item\b|\badopted forthwith\b|"
                                  r"\bcouncil adopted\b.*\bas amended\b"),
    ("SCHEDULED_FOR_COUNCIL", 50, r"\bscheduled item for council on\b"),
    ("COMMITTEE_ACTED",       40, r"committee\s+approved\b|committee\s+waived consideration\b|"
                                  r"committee[^.]*\bapproved (?:as amended|item)"),
    ("IN_COMMITTEE",          30, r"committee scheduled item for committee\b|"
                                  r"committee continued item\b"),
    ("REFERRED_TO_COMMITTEE", 20, r"\breferred to\b[^.]*\bcommittee\b|\brereferred item to\b"),
    ("REFERRED_TO_COUNCIL",   15, r"\breferred to council\b"),
    ("INTRODUCED",            10, r"\bmotion\b|\bsubmitted by\b|\bdocument\(s\) submitted\b"),
]
COMPILED = [(s, rank, re.compile(p, re.I)) for s, rank, p in STAGE_PATTERNS]

# Ordered for display / progress reasoning.
STAGE_ORDER = ["INTRODUCED", "REFERRED_TO_COMMITTEE", "IN_COMMITTEE", "COMMITTEE_ACTED",
               "REFERRED_TO_COUNCIL", "SCHEDULED_FOR_COUNCIL", "COUNCIL_ADOPTED",
               "WITH_MAYOR", "FINAL", "EXPIRED"]

# Transitions worth telling a human about, and how loudly.
TRANSITION_PRIORITY = {
    "SCHEDULED_FOR_COUNCIL": "high",    # actionable -- the vote hasn't happened yet
    "IN_COMMITTEE": "high",             # also actionable -- hearing is upcoming
    "COUNCIL_ADOPTED": "high",          # decided
    "COMMITTEE_ACTED": "medium",
    "REFERRED_TO_COMMITTEE": "medium",
    "WITH_MAYOR": "medium",
    "FINAL": "medium",
    "EXPIRED": "medium",
    "REFERRED_TO_COUNCIL": "medium",
    "INTRODUCED": "low",
}

DATE_IN_TEXT = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),?\s+(\d{4})", re.I)


def classify_activity_stage(text):
    """(stage, rank) for a single activity line, or (None, -1)."""
    best, best_rank = None, -1
    for stage, rank, pat in COMPILED:
        if pat.search(text or "") and rank > best_rank:
            best, best_rank = stage, rank
    return best, best_rank


def scheduled_date(text):
    """The hearing date mentioned in a scheduling line, if any."""
    m = DATE_IN_TEXT.search(text or "")
    return m.group(0) if m else None


def derive_stage(activities):
    """Current stage of a file. activities are newest-first.

    Walks newest to oldest and takes the first line that maps to a stage --
    the most recent meaningful event is where the file actually is. (Taking a
    max over all lines would be wrong: a file referred back to committee after
    a Council vote is in committee again, not adopted.)
    """
    if not activities:
        return {"stage": "UNKNOWN", "as_of": None, "detail": None, "hearing_date": None}

    for a in activities:
        text = a.get("activity", "")
        stage, rank = classify_activity_stage(text)
        if stage:
            return {
                "stage": stage,
                "as_of": a.get("date"),
                "detail": " ".join(text.split())[:200],
                "hearing_date": scheduled_date(text)
                if stage in ("SCHEDULED_FOR_COUNCIL", "IN_COMMITTEE") else None,
            }

    return {"stage": "UNKNOWN", "as_of": activities[0].get("date"),
            "detail": " ".join((activities[0].get("activity") or "").split())[:200],
            "hearing_date": None}


def detect_transition(prior_activities, new_activities):
    """Did the file move? Returns None, or a dict describing the move."""
    old = derive_stage(prior_activities or [])
    new = derive_stage(new_activities or [])
    if old["stage"] == new["stage"]:
        return None
    return {
        "from": old["stage"],
        "to": new["stage"],
        "as_of": new["as_of"],
        "detail": new["detail"],
        "hearing_date": new["hearing_date"],
        "priority": TRANSITION_PRIORITY.get(new["stage"], "low"),
    }


if __name__ == "__main__":
    import json, os, sys
    HERE = os.path.dirname(os.path.abspath(__file__))
    DATA = os.path.join(HERE, "..", "data")
    counts = {}
    rows = []
    for fname in sorted(os.listdir(DATA)):
        p = os.path.join(DATA, fname)
        if not fname.endswith(".json") or os.path.isdir(p) or fname == "meeting_index.json":
            continue
        try:
            rec = json.load(open(p))
        except Exception:
            continue
        if not isinstance(rec, dict) or "activities" not in rec:
            continue
        st = derive_stage(rec.get("activities") or [])
        counts[st["stage"]] = counts.get(st["stage"], 0) + 1
        rows.append((st["stage"], rec.get("cfnumber"), (rec.get("title") or "")[:52], st["as_of"]))

    print("Current stage of every watched council file:\n")
    for stage in STAGE_ORDER + ["UNKNOWN"]:
        n = counts.get(stage, 0)
        if n:
            print(f"  {n:>3}  {stage}")
    if "--list" in sys.argv:
        print()
        order = {s: i for i, s in enumerate(STAGE_ORDER)}
        for stage, cf, title, as_of in sorted(rows, key=lambda r: order.get(r[0], 99)):
            print(f"  {stage:<24} {cf:<15} {as_of or '':<12} {title}")
