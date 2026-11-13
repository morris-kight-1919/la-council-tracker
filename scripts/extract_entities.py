#!/usr/bin/env python3
"""Cheap entity extraction for fields_json: names, organizations, addresses,
dollar amounts, council-file numbers, dates.

The RIP LA schema design specifies fields_json as
{case_numbers, apns, addresses, names, dates} riding alongside full text.
Ours previously carried only CF numbers, dollar amounts and dates -- so
"every document where Gibson Dunn appears" wasn't answerable, only
"every transcript".

Deliberately regex and known-entity matching, not NLP: it runs in-process
over 555 documents in seconds, has no model dependency competing with the
Morris Kight transcription job for CPU, and its failure mode is *missing*
things rather than inventing them. Precision over recall -- a false entity
is worse than a missed one, because it pollutes every downstream query.

APNs (assessor parcel numbers) from their schema are omitted: those are
land-use identifiers and don't appear in council-file documents.
"""
import re

# --- Organisations --------------------------------------------------------
# Capitalised run ending in a corporate/institutional suffix.
ORG_SUFFIX = (r"(?:LLC|L\.L\.C\.|Inc\.?|Incorporated|LLP|L\.L\.P\.|L\.P\.|Corp\.?|"
              r"Corporation|Company|Foundation|Institute|Authority|Coalition|"
              r"Partners|Associates|Alliance|Trust|Council|Center|Centre)")
ORG_RE = re.compile(
    r"\b((?:[A-Z][\w&'’.-]*\s+){1,5}" + ORG_SUFFIX + r")(?=[\s,.;:)]|$)")

# Named entities we care about that don't fit the suffix pattern.
KNOWN_ORGS = [
    "Gibson Dunn", "Gibson, Dunn & Crutcher", "LAFLA", "Legal Aid Foundation of Los Angeles",
    "LAHSA", "Los Angeles Homeless Services Authority", "Alvarez & Marsal",
    "LA Alliance for Human Rights", "L.A. Alliance for Human Rights",
    "Los Angeles Community Action Network", "LA CAN", "Los Angeles Catholic Worker",
    "Inner City Law Center", "Public Counsel", "StayHoused LA", "Inside Safe",
    "The People Concern", "PATH", "People Assisting the Homeless", "Shangri-La",
    "Umhofer", "Miller Barondess", "Hueston Hennigan", "Manatt",
    "Housing Authority", "HACLA", "Bureau of Sanitation", "City Administrative Officer",
    "Chief Legislative Analyst", "Controller", "City Attorney",
]

# Sitting/recent councilmembers plus recurring figures in this investigation.
KNOWN_PEOPLE = [
    "Harris-Dawson", "Krekorian", "Blumenfield", "Hernandez", "Hutt", "Jurado",
    "Lee", "McOsker", "Padilla", "Park", "Price", "Raman", "Rodriguez",
    "Soto-Martinez", "Yaroslavsky", "Nazarian", "de León", "de Leon", "DeLeon",
    "Bass", "Feldstein Soto", "Szabo", "Mejia",
    "Scolnick", "Evangelis", "Myers", "Garrie", "Martinez", "Carter", "Goethals",
    "Marcus", "Fauble", "Adler", "McRae", "Hamburger",
]

# --- Addresses ------------------------------------------------------------
STREET_TYPE = (r"(?:Street|St\.|Avenue|Ave\.|Boulevard|Blvd\.|Road|Rd\.|Drive|Dr\.|"
               r"Way|Place|Pl\.|Court|Ct\.|Lane|Ln\.|Terrace|Parkway|Pkwy\.|Circle)")
ADDRESS_RE = re.compile(
    r"\b(\d{1,6}(?:-\d{1,6})?\s+"
    r"(?:North|South|East|West|N\.|S\.|E\.|W\.)?\s*"
    r"(?:[A-Z][\w'’.-]*\s+){1,4}" + STREET_TYPE + r")\b")

CF_RE = re.compile(r"\b\d{2}-\d{4}(?:-S\d+)?\b")
MONEY_RE = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?(?:\s*(?:million|billion|M|B)\b)?", re.I)
DATE_RE = re.compile(
    r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}"
    r"|(?:January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+\d{1,2},?\s+\d{4})\b")

# Capitalised phrases that match ORG_RE but aren't organisations. Several of
# these are real artifacts seen in the corpus: agenda timestamps produce
# "10:00 AM Council", status lines produce "ADOPTED Council".
ORG_STOPWORDS = re.compile(
    r"^(?:The|This|That|These|Those|Said|Such|Any|All|Each|Both|"
    r"AM|PM|ADOPTED|APPROVED|CONTINUED|REFERRED|FORTHWITH|"
    r"City Council|Council File|Motion|Report|Attachment|Exhibit|"
    r"Item|Items|File|Page|Date|Time|Special|Regular)\b", re.I)

MAX_PER_FIELD = 60


def _dedupe(seq, limit=MAX_PER_FIELD):
    seen, out = set(), []
    for s in seq:
        s = " ".join(s.split()).strip(" ,.;:")
        k = s.lower()
        if s and k not in seen:
            seen.add(k)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def extract(text):
    if not text:
        return {}
    # Collapse whitespace once; PDF text is full of ragged spacing that
    # otherwise breaks multi-word patterns.
    t = re.sub(r"[ \t]+", " ", text)

    orgs = [m.group(1) for m in ORG_RE.finditer(t) if not ORG_STOPWORDS.match(m.group(1))]
    for known in KNOWN_ORGS:
        if re.search(r"\b" + re.escape(known) + r"\b", t, re.I):
            orgs.append(known)

    people = [p for p in KNOWN_PEOPLE if re.search(r"\b" + re.escape(p) + r"\b", t)]

    return {
        "cf_numbers": _dedupe(CF_RE.findall(t)),
        "organizations": _dedupe(orgs),
        "people": _dedupe(people),
        "addresses": _dedupe(m.group(1) for m in ADDRESS_RE.finditer(t)),
        "dollar_amounts": _dedupe(MONEY_RE.findall(t)),
        "dates": _dedupe(DATE_RE.findall(t)),
    }


if __name__ == "__main__":
    import json
    import sys
    import db

    conn = db.init()
    if "--reextract" in sys.argv:
        from run_log import logged_run
        with logged_run("extract_entities") as counts:
            rows = conn.execute("SELECT id, full_text FROM documents").fetchall()
            n = 0
            for r in rows:
                conn.execute("UPDATE documents SET fields_json=? WHERE id=?",
                             (json.dumps(extract(r["full_text"])), r["id"]))
                n += 1
                if n % 100 == 0:
                    conn.commit()
                    print(f"  {n}/{len(rows)}", flush=True)
            conn.commit()
            counts["documents"] = n
            print(f"re-extracted entities for {n} document(s)")
    else:
        # Show what it finds across the corpus.
        from collections import Counter
        c_org, c_ppl, c_addr = Counter(), Counter(), Counter()
        for r in conn.execute("SELECT fields_json FROM documents WHERE fields_json IS NOT NULL"):
            f = json.loads(r["fields_json"] or "{}")
            c_org.update(f.get("organizations", []))
            c_ppl.update(f.get("people", []))
            c_addr.update(f.get("addresses", []))
        for label, c in (("organizations", c_org), ("people", c_ppl), ("addresses", c_addr)):
            print(f"\nTop {label}:")
            for name, n in c.most_common(12):
                print(f"  {n:>4}  {name}")
