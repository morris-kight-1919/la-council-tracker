#!/usr/bin/env python3
"""Classify a File Activities log line by type and significance, so the
changelog can highlight high-signal events over routine housekeeping.
Same regex-pattern-list approach as the court-docket project's classify.py."""
import re

# Order matters -- first match wins.
PATTERNS = [
    (r"\bcouncil adopted\b|\badopted forthwith\b", "VOTE_ADOPTED", "high"),
    (r"\bcouncil action final\b", "FINAL_ACTION", "high"),
    (r"\bcouncil amended\b|\bamended item\b", "AMENDED", "high"),
    (r"\bpublic hearing held\b", "PUBLIC_HEARING", "high"),
    (r"\bcommittee report adopted\b", "COMMITTEE_ADOPTED", "high"),
    (r"\bcouncil continued\b", "CONTINUED", "medium"),
    (r"\bcouncil scheduled item\b", "SCHEDULED", "medium"),
    (r"\breferred to\b.*\bcommittee\b", "REFERRED", "medium"),
    (r"\bcommittee waived consideration\b", "COMMITTEE_WAIVED", "medium"),
    (r"\breceived and filed\b", "RECEIVED_AND_FILED", "medium"),
    (r"\bmotion (adopted|filed)\b", "MOTION", "medium"),
]

DEFAULT = ("OTHER", "low")


def classify(activity_text):
    text = (activity_text or "").lower()
    for pattern, category, priority in PATTERNS:
        if re.search(pattern, text):
            return category, priority
    return DEFAULT


if __name__ == "__main__":
    import sys
    print(classify(" ".join(sys.argv[1:])))
