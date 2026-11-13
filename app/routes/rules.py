"""'What to Watch' -- read-only for now. The real spec asks for a full
plain-language rule editor with a 'test against recent notices' backtest;
that needs keywords.py's hardcoded TIERS turned into an editable data
model, which is a separate, larger piece of work than the rest of this
pass. Shipping a read-only view of what's currently being watched for is
honest about that boundary rather than a half-built editor."""
from flask import Blueprint, render_template

import keywords

bp = Blueprint("rules", __name__)

TIER_TITLES = {
    1: "The act itself (demolition/removal)",
    2: "Emptying the building (Ellis Act, RSO, relocation)",
    3: "Entitlements, vesting, the clock",
    4: "Historic status",
    5: "Hazard / nuisance",
    6: "Process, ethics, accountability",
    8: "Legal doctrine and litigation posture",
}


@bp.route("/rules")
def rules():
    tiers = [
        {"title": TIER_TITLES.get(n, f"Tier {n}"), "weight": w, "terms": terms}
        for n, (w, terms) in sorted(keywords.TIERS.items())
    ]
    tier7 = {"title": "Named people & organizations (this investigation)",
             "weight": keywords.TIER7[0], "terms": keywords.TIER7[1]}
    ceqa = {"title": "CEQA exemption language",
            "weight": keywords.CEQA_EXEMPTION[0], "terms": keywords.CEQA_EXEMPTION[1]}
    return render_template(
        "rules.html",
        tiers=tiers, tier7=tier7, ceqa=ceqa,
        suppress_terms=keywords.SUPPRESS,
        threshold=keywords.DEFAULT_THRESHOLD,
    )
