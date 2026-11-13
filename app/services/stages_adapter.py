"""Thin call-through to the existing pipeline's stage-derivation logic
(scripts/stages.py) -- no logic is duplicated here. Not yet called by any
route while SEED_MODE is on (seed records already carry a fixed example
status); wired up in the real-data-connection pass.
"""
from stages import STAGE_ORDER, derive_stage  # noqa: F401  (scripts/ on sys.path via app/config.py)

STAGE_LABELS = {
    "INTRODUCED": "Introduced",
    "REFERRED_TO_COMMITTEE": "Referred to committee",
    "IN_COMMITTEE": "In committee",
    "COMMITTEE_ACTED": "Committee acted",
    "REFERRED_TO_COUNCIL": "Referred to Council",
    "SCHEDULED_FOR_COUNCIL": "Scheduled for Council",
    "COUNCIL_ADOPTED": "Adopted by Council",
    "WITH_MAYOR": "With the Mayor",
    "FINAL": "Final",
    "EXPIRED": "Expired",
    "UNKNOWN": "Status unclear",
}


def status_label(stage):
    return STAGE_LABELS.get(stage, stage.replace("_", " ").title())
