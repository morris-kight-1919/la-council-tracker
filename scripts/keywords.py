#!/usr/bin/env python3
"""Tiered keyword scoring for the City announcement stream.

Implements "Keywords — City Watch" (7 Aug 2026). Its three design rules,
which are the whole point and are easy to get wrong:

  1. MATCH STRUCTURAL IDENTIFIERS FIRST, PLAIN WORDS SECOND. A case number is
     unambiguous; "demolition" is not. Tier 0 regex is the primary trigger,
     word matching is the safety net for things that have no case number yet.

  2. SCORE, DON'T GATE. Never require a term. Sum weights, keep anything over
     a low threshold. A hard gate on any single word throws away structure --
     "Ellis" + "1923" + a watched parcel beats "demolition" alone.

  3. THE HIGHEST-VALUE NOTICES CONTAIN NONE OF THE OBVIOUS WORDS. By the time
     a notice says "demolition" the decision is made. Tier 2 (emptying the
     building) and Tier 3 (vesting/extensions) outrank Tier 1, even though
     Tier 1 is where everyone starts.

And two operational rules:
  - CO-OCCURRENCE IS THE REAL SIGNAL. Two tiers together beat either doubled.
  - SUPPRESS, DO NOT DELETE. Negative filters set a flag; the text stays, so
    the filter can be re-run later with better rules. You cannot re-run
    against messages you threw away.

Tier 7 (named actors) is deliberately OURS, not theirs -- the source document
is explicit that it "only works if it reflects who is actually filing in the
districts you care about." Theirs is tuned to demolition/historic
preservation; this one is tuned to the homelessness-funding and
conflict-of-interest investigation.
"""
import re

# ---------------------------------------------------------------- Tier 0
PLANNING_CASE = re.compile(
    r"\b(VTT|TT|CPC|DIR|ZA|ENV|AA|PAR|ADM|CHC|APCE|APCS|APCNV|APCW|APCC|APCSV)"
    r"-\d{4}-\d+(?:-[A-Z0-9]{1,6})*\b", re.I)
COUNCIL_FILE = re.compile(r"\b\d{2}-\d{4}(?:-S\d+)?\b")
PERMIT_NUMBER = re.compile(r"\b\d{5}-\d{5}-\d{5}\b")
COR_NUMBER = re.compile(r"\bCoR\s*#?\s*\d{5}\b", re.I)
APN = re.compile(r"\b\d{4}[-\s]?\d{3}[-\s]?\d{3}\b")

PREFIX_WEIGHT = {  # not equally interesting
    "VTT": 8, "TT": 8, "CHC": 8,
    "ENV": 6, "DIR": 6, "CPC": 6,
    "ZA": 4, "AA": 4, "PAR": 4,
    "ADM": 1,
}

# "the single most underwatched suffix" is -EXT
SUFFIX_WEIGHT = {
    "EXT": 8, "SL": 5, "HCA": 5, "CN": 3, "MA": 5, "SN": 3,
    "CUB": 4, "CUE": 4, "CUX": 4, "DB": 5, "TOC": 5, "ED1": 5,
    "1A": 4, "2A": 4,
}

# ------------------------------------------------------- Tiers 1-8 (words)
TIERS = {
    1: (3, [  # the act itself -- necessary, mostly too late
        "demolition", "demolish", "demo permit", "raze", "razing",
        "removal of structure", "structure removal", "remove the existing",
        "wrecking", "relocation of structure", "house move", "building relocation",
        "partial demolition", "substantial alteration", "substantial demolition",
        "site preparation", "site clearance", "clearing the site",
        "new construction on the site of", "replace the existing",
        "in lieu of the existing", "adaptive reuse",
    ]),
    2: (4, [  # emptying the building -- highest-value tier in the list
        "ellis act", "ellis", "withdrawal of accommodations",
        "notice of intent to withdraw", "withdrawn from the rental market",
        "removal from the rental market", "rent stabilization ordinance", "rso",
        "rent stabilized", "rent-stabilized", "deregistration", "deregistered",
        "registration cancelled", "reap", "rent escrow account program",
        "escrow account", "agro", "affirmative general rehabilitation order",
        "relocation assistance", "relocation fee", "tenant relocation",
        "permanent relocation", "replacement unit", "replacement unit determination",
        "no net loss", "right to return", "right of first refusal", "re-rental",
        "tenant habitability plan", "buyout agreement", "cash for keys",
        "eviction", "unlawful detainer", "notice to quit", "three-day notice",
        "sixty-day notice", "owner occupancy", "owner move-in",
        "just cause", "vacancy", "vacated", "unoccupied",
        "utility shutoff", "service disconnection", "discontinuance of service",
    ]),
    3: (4, [  # entitlements, vesting, the clock
        "vesting", "vested", "vesting tentative tract", "tentative map",
        "final map", "subdivision", "parcel map", "time extension",
        "extend the expiration", "expiration date", "subsequent extension",
        "additional extension", "map expiration", "automatic extension",
        "small lot", "lot line adjustment", "lot split", "lot tie",
        "density bonus", "concession", "waiver of development standards",
        "transit oriented communities", "transit oriented", "executive directive 1",
        "builder's remedy", "by right", "ministerial", "streamlined", "streamlining",
        "sb 9", "sb 35", "sb 330", "sb 423", "sb 684", "sb 1123", "sb 79",
        "ab 2011", "ab 1061", "ab 130", "housing accountability act",
        "housing crisis act", "housing element", "sb 166", "rezoning program",
        "specific plan amendment", "general plan amendment", "zone change",
        "height district change",
    ]),
    4: (3, [  # historic status -- the shield, and attacks on it
        "historic-cultural monument", "hcm", "monument nomination",
        "monument designation", "cultural heritage commission", "hpoz",
        "historic preservation overlay zone", "preservation plan",
        "certificate of appropriateness", "certificate of compatibility",
        "surveyla", "historicplacesla", "historic resources survey",
        "california register", "national register", "determination of eligibility",
        "mills act", "historical property contract", "historic resource",
        "historical resource", "potential historic resource",
        "historical resources assessment report", "substantial adverse change",
        "character-defining features", "state historical building code",
        "section 106", "advisory council on historic preservation",
        "office of historic resources", "contributing structure",
        "non-contributing", "delisting", "removal of designation",
        "rescind designation",
    ]),
    5: (4, [  # hazard / nuisance -- "the deadliest single pathway"
        "imminent hazard", "immediate hazard", "extreme and immediate hazard",
        "unsafe", "substandard", "unfit for human habitation", "uninhabitable",
        "order to comply", "order to vacate", "notice to vacate",
        "nuisance abatement", "public nuisance", "abatement",
        "emergency demolition", "emergency order", "summary abatement",
        "board of building and safety commissioners", "bbsc",
        "code enforcement", "citation", "non-compliance fee",
        "red tag", "red-tagged", "placarded", "fire damage", "fire-damaged",
        "post-fire", "structural failure", "collapse", "seismic", "soft story",
        "retrofit", "unreinforced masonry", "alquist-priolo", "fault zone",
        "very high fire hazard severity zone", "scorched earth ordinance",
    ]),
    6: (2, [  # process, ethics, accountability -- highly relevant to this work
        "brown act", "cure and correct", "serial meeting", "closed session",
        "public records act", "cpra", "records request", "determination letter",
        "conflict of interest", "government code 1090", "section 1090",
        "form 700", "statement of economic interests", "recusal", "recuse",
        "ex parte", "lobbyist registration", "behested payment",
        "ethics commission", "city ethics", "tefra", "conduit financing",
        "bond issuance", "calpfa", "appeal period", "appellant", "standing",
        "notice of public hearing", "continued to", "off calendar", "withdrawn",
        "letter of determination",
    ]),
    8: (2, [  # legal doctrine + litigation posture
        "people v. chacon", "thomson v. call", "lexin v. superior court",
        "sierra club v. superior court", "national lawyers guild",
        "city of san jos", "friends of college of san mateo",
        "berkeley hillside", "entrapment by estoppel",
        "unusual circumstances exception", "fair argument standard",
        "substantial evidence standard", "writ of mandate", "ceqa challenge",
        "statute of limitations", "exhaustion of administrative remedies",
        "petition for writ of mandate", "verified petition", "complaint filed",
        "settlement agreement", "stipulated judgment", "consent decree",
        "court of appeal", "temporary restraining order",
        "preliminary injunction", "attorney fees",
        "code of civil procedure 1021.5", "private attorney general",
        "demurrer", "fix the city", "aids healthcare foundation",
        "coalition to preserve la",
    ]),
}

# CEQA exemption language is weighted heavily -- an exemption is how a
# resource gets cleared without study.
CEQA_EXEMPTION = (5, [
    "categorical exemption", "class 32", "class 3", "class 15332",
    "infill exemption", "statutory exemption", "common sense exemption",
    "15061(b)(3)", "sustainable communities project exemption",
    "notice of exemption", "mitigated negative declaration",
    "negative declaration", "addendum to the eir", "previously certified",
    "program eir", "no substantial evidence",
])

# ---------------------------------------------------------------- Tier 7
# OURS, per the source document's instruction to derive this locally.
TIER7 = (4, [
    # the conflict-of-interest thread
    "scolnick", "gibson dunn", "gibson, dunn", "evangelis", "chase scolnick",
    "lafla", "legal aid foundation", "shayla myers", "stayhoused",
    # money and oversight
    "lahsa", "los angeles homeless services authority", "alvarez & marsal",
    "alvarez and marsal", "garrie", "michele martinez", "special master",
    "monitor", "receivership", "inside safe", "measure h", "homekey",
    "encampment reduction", "roadmap agreement", "alliance settlement",
    # parties / counsel
    "la alliance", "l.a. alliance", "alliance for human rights",
    "umhofer", "mitchell", "la catholic worker", "community action network",
    "miller barondess", "hueston hennigan",
    # officials recurring in this investigation
    "curren price", "karen bass", "feldstein soto", "szabo", "kenneth mejia",
    "harris-dawson", "hutt", "jurado", "raman", "yaroslavsky", "soto-mart",
    # agencies
    "lahd", "ladbs", "hacla", "cra/la", "calpfa",
])

# --------------------------------------------------------- negative filters
SUPPRESS = [
    "job posting", "employment opportunity", "recruitment", "civil service exam",
    "street closure", "road closure", "parade", "film permit", "special event",
    "tree trimming", "street sweeping", "parking restriction", "holiday schedule",
    "proclamation", "commendation", "in memoriam", "adjourn in memory",
]

DEFAULT_THRESHOLD = 6


def _hits(text_l, terms):
    return [t for t in terms if t in text_l]


def score(text, watched_cfs=None, watched_cases=None, own_cf=None):
    """Score one document/email. Returns a dict with total, per-tier hits,
    identifiers found, and a suppress flag.

    watched_cfs / watched_cases: things already being tracked. A Tier 0
    identifier that matches one of these means FIRE IMMEDIATELY, no threshold.

    own_cf: the council file this text was ALREADY collected under, if any.
    Without this the rule is circular -- a document fetched from a watched
    file inevitably cites that file's own number, so everything "fires" and
    the signal is worthless (measured: 524 of 790 items). The source
    document's rule is about the incoming announcement stream, where a
    watched identifier turning up is genuinely news. A file's own paperwork
    citing itself is not news, so its own number is excluded from the test.
    """
    if not text:
        return {"total": 0, "tiers": {}, "identifiers": {}, "suppress": False,
                "fire_immediately": False}

    text_l = text.lower()
    watched_cfs = set(watched_cfs or [])
    watched_cases = set(c.upper() for c in (watched_cases or []))

    total = 0
    tiers = {}
    identifiers = {}
    fire = False

    # --- Tier 0: structural identifiers, the primary trigger ---
    # Score DISTINCT identifiers, and cap the count. Scoring every occurrence
    # made the total scale with document length rather than relevance -- one
    # long staff report scored 8,848 purely by repeating case numbers.
    distinct_cases = {}
    for m in PLANNING_CASE.finditer(text):
        distinct_cases.setdefault(m.group(0).upper(), m.group(1).upper())
    if distinct_cases:
        identifiers["planning_cases"] = sorted(distinct_cases)
        for whole, prefix in list(distinct_cases.items())[:6]:   # cap
            pts = 5 + PREFIX_WEIGHT.get(prefix, 2)
            for suffix, w in SUFFIX_WEIGHT.items():
                if re.search(r"-" + suffix + r"\b", whole):
                    pts += w
            if whole in watched_cases:
                fire = True
            total += pts

    cfs = sorted(set(COUNCIL_FILE.findall(text)))
    if cfs:
        identifiers["council_files"] = cfs
        # noisy alone -- weight low unless watched, and never credit a
        # document for citing the file it was collected under.
        watched_hit = [c for c in cfs if c in watched_cfs and c != own_cf]
        if watched_hit:
            fire = True
            identifiers["watched_council_files"] = watched_hit
            total += 10
        else:
            total += 1

    for name, pat, pts in (("permits", PERMIT_NUMBER, 4),
                           ("records_requests", COR_NUMBER, 4)):
        found = sorted(set(pat.findall(text)))
        if found:
            identifiers[name] = found
            total += pts

    # APNs are dangerous alone (match phone numbers, dollar figures) -- only
    # credited when they hit the watch set.
    apns = sorted(set(APN.findall(text)))
    if apns:
        watched_apn = [a for a in apns if a in watched_cases]
        if watched_apn:
            identifiers["watched_apns"] = watched_apn
            total += 8
            fire = True

    # --- Tiers 1-8 ---
    for tier, (weight, terms) in TIERS.items():
        found = _hits(text_l, terms)
        if found:
            tiers[f"tier{tier}"] = found[:12]
            total += weight * min(len(found), 4)   # cap so one doc can't run away

    w, terms = CEQA_EXEMPTION
    found = _hits(text_l, terms)
    if found:
        tiers["ceqa_exemption"] = found[:8]
        total += w * min(len(found), 3)

    w, terms = TIER7
    found = _hits(text_l, terms)
    if found:
        tiers["tier7_actors"] = found[:12]
        total += w * min(len(found), 4)

    # --- co-occurrence bonus: two tiers together beat either doubled ---
    n_tiers = len([k for k in tiers if k.startswith("tier") or k == "ceqa_exemption"])
    if n_tiers >= 2:
        total += 3 * (n_tiers - 1)

    suppressed = _hits(text_l, SUPPRESS)

    return {
        "total": total,
        "tiers": tiers,
        "identifiers": identifiers,
        "suppress": bool(suppressed) and total < DEFAULT_THRESHOLD,
        "suppress_terms": suppressed[:5],
        "fire_immediately": fire,
        "cooccurring_tiers": n_tiers,
    }


if __name__ == "__main__":
    import json
    import sys
    txt = sys.stdin.read() if not sys.stdin.isatty() else " ".join(sys.argv[1:])
    print(json.dumps(score(txt), indent=1))
