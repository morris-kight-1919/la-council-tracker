"""Real corpus.db / watchlist.json / data/*.json queries -- the live-data
counterpart to seed.py. Nothing here duplicates pipeline logic; it reads
the same files/tables the CLI scripts already write.
"""
import json
import os

import watch as pipeline_watch  # scripts/watch.py -- load_watchlist()
from stages import derive_stage
from classify_activity import classify

from . import stages_adapter

DATA_DIR = os.path.join(pipeline_watch.ROOT, "data")

TIER_LABELS = {
    "tier1": "the act itself (demolition/removal)",
    "tier2": "emptying the building (Ellis/RSO/relocation)",
    "tier3": "entitlements/vesting",
    "tier4": "historic status",
    "tier5": "hazard/nuisance",
    "tier6": "process/ethics",
    "tier7_actors": "named people/organizations",
    "tier8": "legal doctrine/litigation",
    "ceqa_exemption": "CEQA exemption language",
}


# --------------------------------------------------------------------- misc

def _load_watchlist():
    return pipeline_watch.load_watchlist()


def _load_snapshot(cfnumber):
    path = os.path.join(DATA_DIR, f"{cfnumber}.json")
    if not os.path.exists(path):
        return None
    try:
        return json.load(open(path))
    except (json.JSONDecodeError, OSError):
        return None


def _official_url(cfnumber):
    return (f"https://cityclerk.lacity.org/lacityclerkconnect/"
            f"index.cfm?fa=ccfi.viewrecord&cfnumber={cfnumber}")


# ------------------------------------------------------------------- Inbox

def _parse_dedupe_key(dedupe_key):
    parts = (dedupe_key or "").split(":")
    if len(parts) == 3 and parts[0] == "relevance":
        return parts[1], parts[2]
    return None, None


def _fetch_source_text(conn, source_type, source_id):
    if source_type == "email":
        row = conn.execute("SELECT body_text FROM emails WHERE id=?", (source_id,)).fetchone()
        return row["body_text"] if row else None
    if source_type == "document":
        row = conn.execute("SELECT full_text FROM documents WHERE id=?", (source_id,)).fetchone()
        return row["full_text"] if row else None
    return None


def _candidate_terms(tiers, identifiers):
    terms = []
    for hits in tiers.values():
        if isinstance(hits, list):
            terms.extend(hits)
    for key in ("council_files", "planning_cases"):
        terms.extend(identifiers.get(key, []))
    return sorted(set(terms), key=len, reverse=True)


def _build_excerpt(text, terms, window=180):
    if not text:
        return "(no source text available)", "", ""
    low = text.lower()
    for term in terms:
        idx = low.find(term.lower())
        if idx == -1:
            continue
        start, end = max(0, idx - window), min(len(text), idx + len(term) + window)
        before = ("…" if start > 0 else "") + text[start:idx]
        match = text[idx:idx + len(term)]
        after = text[idx + len(term):end] + ("…" if end < len(text) else "")
        return " ".join(before.split()), match, " ".join(after.split())
    return " ".join(text.split())[:360], "", ""


def _confidence_label(fire, score):
    if fire:
        return "Likely match"
    if score >= 100:
        return "Possible match"
    return "Needs review"


def _why_matched(tiers, identifiers):
    bits = []
    if identifiers.get("watched_council_files"):
        bits.append(f"references watched CF {', '.join(identifiers['watched_council_files'])}")
    for key, hits in tiers.items():
        if hits:
            bits.append(f"{TIER_LABELS.get(key, key)}: {', '.join(hits[:3])}")
    return "; ".join(bits) if bits else "cleared the keyword-score threshold"


def _cf_title(watchlist, cfnumber):
    entry = watchlist.get(cfnumber)
    return entry["title"] if entry else cfnumber


def _build_inbox_item(conn, watchlist, row):
    payload = json.loads(row["payload_json"] or "{}")
    fire = bool(payload.get("fire"))
    tiers = payload.get("tiers", {})
    identifiers = payload.get("identifiers", {})

    source_type, source_id = _parse_dedupe_key(row["dedupe_key"])
    text = _fetch_source_text(conn, source_type, source_id) if source_type else None
    before, match, after = _build_excerpt(text, _candidate_terms(tiers, identifiers))

    cfnumber = row["cfnumber"]
    return {
        "id": row["id"],
        "decision": row["decision"],
        "fire": fire,
        "source_title": payload.get("label") or "(untitled)",
        "received_at": payload.get("date") or row["created_at"],
        "excerpt_before": before,
        "excerpt_match": match,
        "excerpt_after": after,
        "proposed_cfnumber": cfnumber,
        "proposed_title": _cf_title(watchlist, cfnumber) if cfnumber else None,
        "confidence": _confidence_label(fire, payload.get("score", 0)),
        "why_matched": _why_matched(tiers, identifiers),
        "source_url": payload.get("url") or _official_url(cfnumber) if cfnumber else payload.get("url"),
        "candidates": identifiers.get("council_files", [])[:8],
    }


def get_inbox_items(conn, limit=200):
    rows = conn.execute(
        "SELECT o.id, o.created_at, o.cfnumber, o.payload_json, o.dedupe_key, r.decision "
        "FROM outbox o LEFT JOIN outbox_review r ON r.outbox_id = o.id "
        "WHERE o.kind='keyword_hit' AND (r.decision IS NULL OR r.decision = 'backlog') "
        "ORDER BY o.created_at DESC LIMIT ?", (limit,)
    ).fetchall()

    watchlist = _load_watchlist()
    items = [_build_inbox_item(conn, watchlist, r) for r in rows]
    # Stable sort: new items first, then backlog-fire, then backlog-other --
    # relative created_at-desc order within each bucket is preserved because
    # Python's sort is stable and the rows already arrived in that order.
    items.sort(key=lambda it: 0 if it["decision"] is None else (1 if it["fire"] else 2))
    return items


def write_decision(conn, outbox_id, decision, cfnumber_override=None, note=None, decided_by=None):
    """Record a human decision on one outbox item. REPLACE, not INSERT --
    decisions are reversible by construction (re-submit to change one)."""
    import datetime
    conn.execute(
        "INSERT OR REPLACE INTO outbox_review "
        "(outbox_id, decision, cfnumber_override, note, decided_at, decided_by) "
        "VALUES (?,?,?,?,?,?)",
        (outbox_id, decision, cfnumber_override, note,
         datetime.datetime.now().isoformat(timespec="seconds"), decided_by or None))
    conn.commit()


def needs_review_count(conn):
    row = conn.execute(
        "SELECT COUNT(*) n FROM outbox o LEFT JOIN outbox_review r ON r.outbox_id = o.id "
        "WHERE o.kind='keyword_hit' AND r.decision IS NULL"
    ).fetchone()
    return row["n"]


# --------------------------------------------------------------- Watchlist

def _hearings_by_cf(conn, cfnumbers):
    if not cfnumbers:
        return {}
    qmarks = ",".join("?" * len(cfnumbers))
    rows = conn.execute(
        f"SELECT o.id, o.cfnumber, o.payload_json, o.created_at FROM outbox o "
        f"LEFT JOIN outbox_review r ON r.outbox_id = o.id "
        f"WHERE o.kind='hearing_alert' AND o.cfnumber IN ({qmarks}) "
        f"  AND (r.decision IS NULL OR r.decision NOT IN ('dismissed','rejected')) "
        f"ORDER BY o.created_at DESC", tuple(cfnumbers)
    ).fetchall()
    out = {}
    for r in rows:
        if r["cfnumber"] in out:
            continue  # first (most recent) hearing_alert per CF wins
        out[r["cfnumber"]] = {"id": r["id"], **json.loads(r["payload_json"] or "{}")}
    return out


WHY_WATCHED_LABELS = {"manual": "You added this manually"}


def _why_watched_label(source):
    if source in WHY_WATCHED_LABELS:
        return WHY_WATCHED_LABELS[source]
    if source and ":" in source:
        kind, value = source.split(":", 1)
        return f"Auto-discovered: {kind} “{value}”"
    return source or "Unknown"


def get_watchlist_rows(conn):
    watchlist = _load_watchlist()
    cfnumbers = list(watchlist.keys())
    hearings = _hearings_by_cf(conn, cfnumbers)

    rows = []
    for cf, meta in sorted(watchlist.items()):
        record = _load_snapshot(cf)
        stage = derive_stage(record.get("activities") or []) if record else \
            {"stage": "UNKNOWN", "as_of": None, "detail": None, "hearing_date": None}

        hearing = hearings.get(cf)
        if hearing:
            next_event_date, next_event_label = hearing.get("meeting_date"), hearing.get("meeting_title")
        elif stage.get("hearing_date"):
            next_event_date, next_event_label = stage["hearing_date"], "(from activity text)"
        else:
            next_event_date = next_event_label = None

        source = meta.get("source", "")
        rows.append({
            "cfnumber": cf,
            "title": meta.get("title") or cf,
            "official_url": _official_url(cf),
            "status_label": stages_adapter.status_label(stage["stage"]),
            "status_as_of": stage["as_of"],
            "next_event_date": next_event_date,
            "next_event_label": next_event_label,
            "committee": None,
            "confidence": "Confirmed" if source == "manual" else "Auto-discovered — needs review",
            "why_watched": _why_watched_label(source),
            "last_checked": record.get("retrieved_at") if record else None,
        })
    return rows


# ---------------------------------------------------------- Council File detail

def get_council_file(conn, cfnumber):
    watchlist = _load_watchlist()
    meta = watchlist.get(cfnumber)
    record = _load_snapshot(cfnumber)
    if meta is None and record is None:
        return None

    stage = derive_stage(record.get("activities") or []) if record else \
        {"stage": "UNKNOWN", "as_of": None, "detail": None, "hearing_date": None}
    hearings = _hearings_by_cf(conn, [cfnumber])
    hearing = hearings.get(cfnumber)

    source = (meta or {}).get("source", "")
    return {
        "cfnumber": cfnumber,
        "title": (record or {}).get("title") or (meta or {}).get("title") or cfnumber,
        "official_url": (record or {}).get("url") or _official_url(cfnumber),
        "status": stage["stage"],
        "status_label": stages_adapter.status_label(stage["stage"]),
        "status_as_of": stage["as_of"],
        "status_detail": stage["detail"],
        "next_event_date": hearing.get("meeting_date") if hearing else stage.get("hearing_date"),
        "next_event_label": hearing.get("meeting_title") if hearing else "(from activity text)" if stage.get("hearing_date") else None,
        "committee": None,
        "why_watched": _why_watched_label(source),
        "confidence": "Confirmed" if source == "manual" else "Auto-discovered — needs review",
        "watched": meta is not None,
        "last_checked": (record or {}).get("retrieved_at"),
    }


def get_timeline(conn, cfnumber):
    record = _load_snapshot(cfnumber)
    if not record:
        return []
    official_url = record.get("url") or _official_url(cfnumber)
    out = []
    for a in record.get("activities", []):
        category, priority = classify(a.get("activity", ""))
        out.append({
            "date": a.get("date"),
            "event_type": category.replace("_", " ").title(),
            "description": a.get("activity"),
            "source_title": "City Clerk Connect — activity log",
            "source_url": official_url,
            "retrieved_at": record.get("retrieved_at"),
            "confidence": None,
            "why_linked": None,
        })
    return out


def get_documents(conn, cfnumber):
    return conn.execute(
        "SELECT title, doc_type, doc_date, source_url, retrieved_at FROM documents "
        "WHERE cfnumber=? ORDER BY doc_date DESC, retrieved_at DESC", (cfnumber,)
    ).fetchall()


def get_comments(conn, cfnumber):
    return conn.execute(
        "SELECT commenter, submitted_at, body, parse_mode, needs_review FROM public_comments "
        "WHERE cfnumber=? ORDER BY submitted_at DESC", (cfnumber,)
    ).fetchall()


# ------------------------------------------------------------------- Sources

def get_sources(conn, limit=40):
    docs = conn.execute(
        "SELECT title, doc_type, doc_date, retrieved_at, source_url, cfnumber, full_text "
        "FROM documents ORDER BY retrieved_at DESC LIMIT ?", (limit,)
    ).fetchall()
    emails = conn.execute(
        "SELECT subject, received_at, retrieved_at, cfnumbers_json, body_text FROM emails "
        "ORDER BY retrieved_at DESC LIMIT ?", (limit,)
    ).fetchall()

    out = []
    for d in docs:
        text = d["full_text"] or ""
        out.append({
            "source_type": "document",
            "title": d["title"],
            "published_at": d["doc_date"],
            "retrieved_at": d["retrieved_at"],
            "source_url": d["source_url"],
            "linked_cfnumbers": [d["cfnumber"]] if d["cfnumber"] else [],
            "extraction_state": "complete" if len(text) > 100 else ("partial" if text else "pending"),
            "preview": " ".join(text.split())[:360],
            "_sort": d["retrieved_at"] or "",
        })
    for e in emails:
        text = e["body_text"] or ""
        cfs = json.loads(e["cfnumbers_json"] or "[]")
        out.append({
            "source_type": "email",
            "title": e["subject"],
            "published_at": e["received_at"],
            "retrieved_at": e["retrieved_at"],
            "source_url": None,
            "linked_cfnumbers": cfs[:6],
            "extraction_state": "complete" if len(text) > 100 else ("partial" if text else "pending"),
            "preview": " ".join(text.split())[:360],
            "_sort": e["retrieved_at"] or "",
        })
    out.sort(key=lambda s: s["_sort"], reverse=True)
    for s in out:
        del s["_sort"]
    return out[:limit]


def source_counts(conn):
    n_docs = conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"]
    n_emails = conn.execute("SELECT COUNT(*) n FROM emails").fetchone()["n"]
    return n_docs, n_emails


# ------------------------------------------------------------------ Calendar

def get_calendar_events(conn):
    """Hearings (from real hearing_alert outbox rows) and recent stage
    changes, in one chronological list. No comment-period deadlines --
    that extraction doesn't exist anywhere in the pipeline yet, so none are
    fabricated here (explicit absence, not a silent gap)."""
    watchlist = _load_watchlist()
    events = []

    hearing_rows = conn.execute(
        "SELECT o.id, o.cfnumber, o.payload_json, o.created_at FROM outbox o "
        "LEFT JOIN outbox_review r ON r.outbox_id = o.id "
        "WHERE o.kind='hearing_alert' "
        "  AND (r.decision IS NULL OR r.decision NOT IN ('dismissed','rejected')) "
        "ORDER BY o.created_at DESC"
    ).fetchall()
    seen_cf = set()
    for r in hearing_rows:
        if r["cfnumber"] in seen_cf:
            continue  # most recent hearing_alert per CF only
        seen_cf.add(r["cfnumber"])
        p = json.loads(r["payload_json"] or "{}")
        events.append({
            "id": r["id"],
            "kind": "Officially scheduled",
            "date": p.get("meeting_date"),
            "title": f"CF {r['cfnumber']} — {p.get('meeting_title', 'Meeting')}",
            "detail": p.get("item_title"),
            "cfnumber": r["cfnumber"],
            "source_url": p.get("url") or _official_url(r["cfnumber"]),
        })

    change_rows = conn.execute(
        "SELECT id, cfnumber, summary, payload_json, created_at FROM outbox "
        "WHERE kind='stage_change' ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    for r in change_rows:
        p = json.loads(r["payload_json"] or "{}")
        events.append({
            "id": r["id"],
            "kind": "Status change",
            "date": p.get("as_of") or r["created_at"],
            "title": f"CF {r['cfnumber']} — {r['summary']}",
            "detail": p.get("detail"),
            "cfnumber": r["cfnumber"],
            "source_url": p.get("url") or _official_url(r["cfnumber"]),
        })

    events.sort(key=lambda e: e["date"] or "", reverse=True)
    return events


# ------------------------------------------------------------------ Activity

def get_recent_errors(hours=48):
    """Pipeline run failures in the last N hours -- a 'source problem' card
    on Today, kept structurally separate from civic-event cards so a fetch
    failure can never read as if it were Council action."""
    import datetime
    import run_log
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    return [e for e in run_log.read_recent(500)
            if e.get("status") == "error" and e.get("timestamp", "") >= cutoff]


def get_recent_changes(conn, hours=24):
    """Council-file activity in the last N hours: stage changes (highest
    signal) plus newly-retrieved documents, for Today's 'Changes' card."""
    import datetime
    cutoff = (datetime.datetime.now() - datetime.timedelta(hours=hours)).isoformat()
    out = []
    for r in conn.execute(
        "SELECT id, cfnumber, summary, created_at FROM outbox "
        "WHERE kind='stage_change' AND created_at >= ? ORDER BY created_at DESC", (cutoff,)
    ).fetchall():
        out.append({"id": r["id"], "cfnumber": r["cfnumber"], "summary": r["summary"], "when": r["created_at"]})

    doc_rows = conn.execute(
        "SELECT cfnumber, COUNT(*) n, MAX(retrieved_at) last FROM documents "
        "WHERE retrieved_at >= ? AND cfnumber IS NOT NULL GROUP BY cfnumber "
        "ORDER BY last DESC", (cutoff,)
    ).fetchall()
    for r in doc_rows:
        out.append({
            "id": None,  # not an outbox row -- no dismiss/snooze target
            "cfnumber": r["cfnumber"],
            "summary": f"{r['n']} new document(s) retrieved",
            "when": r["last"],
        })
    out.sort(key=lambda c: c["when"] or "", reverse=True)
    return out


def get_activity_log(limit=100):
    """Pipeline run history (run_log.jsonl) -- kept in its own tab, never
    mixed into a Council File's civic-event timeline, so a fetch/parser
    failure never reads as if it were Council action."""
    import run_log
    return list(reversed(run_log.read_recent(limit)))


def get_review_decisions(conn, limit=50):
    rows = conn.execute(
        "SELECT r.outbox_id, r.decision, r.cfnumber_override, r.note, r.decided_at, r.decided_by, "
        "       o.cfnumber, o.summary, o.kind "
        "FROM outbox_review r JOIN outbox o ON o.id = r.outbox_id "
        "WHERE r.decision != 'backlog' "
        "ORDER BY r.decided_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return rows


# -------------------------------------------------------------------- Search

def search(conn, query, limit=25):
    if not query or not query.strip():
        return []
    try:
        rows = conn.execute(
            "SELECT source_type, source_id, cfnumber, title, meta, "
            "snippet(search_index, 4, '[', ']', '…', 18) AS snip, "
            "bm25(search_index) AS rank "
            "FROM search_index WHERE search_index MATCH ? ORDER BY rank LIMIT ?",
            (query, limit)
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        out.append({
            "source_type": r["source_type"],
            "cfnumber": r["cfnumber"],
            "title": r["title"],
            "snippet": r["snip"],
            "date": meta.get("date"),
            "url": meta.get("url") or meta.get("video_url"),
        })
    return out


# ------------------------------------------------------------------ Members

def search_members(conn, query, limit=20):
    """Name substring match, case-insensitive. Scoped to watched files only
    -- per the user's explicit scope call, 2026-08-09: not a full LA
    Council historical archive, just whoever shows up in what's watched."""
    if not query or not query.strip():
        return []
    rows = conn.execute(
        "SELECT DISTINCT member_name FROM votes WHERE member_name LIKE ? "
        "ORDER BY member_name LIMIT ?", (f"%{query.strip()}%", limit)
    ).fetchall()
    return [r["member_name"] for r in rows]


def get_member_record(conn, name):
    """Every vote event for this member across watched files, most recent
    first, plus tallies computed from the SAME rows the list shows (so the
    numbers and the list can never drift apart) using only each file's most
    recent event (event_index=0) -- "how this member's record on this file
    currently stands," matching vote_patterns.py's aggregate semantics."""
    rows = conn.execute(
        "SELECT cfnumber, event_index, vote, vote_action, meeting_date, council_district "
        "FROM votes WHERE member_name = ? ORDER BY cfnumber, event_index", (name,)
    ).fetchall()
    if not rows:
        return None

    watchlist = _load_watchlist()
    district = rows[0]["council_district"]
    history = []
    tallies = {"yes": 0, "no": 0, "absent": 0, "abstain": 0, "other": 0}
    for r in rows:
        title = (watchlist.get(r["cfnumber"]) or {}).get("title") or r["cfnumber"]
        history.append({
            "cfnumber": r["cfnumber"],
            "title": title,
            "event_index": r["event_index"],
            "vote": r["vote"],
            "vote_action": r["vote_action"],
            "meeting_date": r["meeting_date"],
            "official_url": _official_url(r["cfnumber"]),
        })
        if r["event_index"] == 0:
            v = (r["vote"] or "").upper()
            if v == "YES":
                tallies["yes"] += 1
            elif v == "NO":
                tallies["no"] += 1
            elif v == "ABSENT":
                tallies["absent"] += 1
            elif "ABSTAIN" in v:
                tallies["abstain"] += 1
            else:
                tallies["other"] += 1

    history.sort(key=lambda h: (h["meeting_date"] or "", h["cfnumber"]), reverse=True)
    return {"name": name, "council_district": district, "tallies": tallies, "history": history}
