#!/usr/bin/env python3
"""Ingest .eml files from the City Clerk's new Legislative Management System
(lms.lacity.gov), which replaced CFMS on 2026-10-26.

Same "core inversion" as eml_ingest.py (the City's mail decides what's worth
looking at), and deliberately reuses its shared helpers (decode_hdr, body_text,
CF_RE, URL_RE, db, extract_entities, logged_run) rather than duplicating them --
this is the same emails table, just a different upstream sender.

LMS sends exactly two message shapes, confirmed from 8 real samples
(LANDrop/LMS-NOTIFICATION-SAMPLES-Oct-28-2026/, SHA256-verified) -- see
[[project_miki_citywatch_app]] in memory for the full field-by-field writeup:

    CF activity    "New activity on Council File <CF>, "<title>":" then one
                   "- <Month D, YYYY>: <action text>." line per new action, then
                   a lms.lacity.gov/files/<CF> link. Real detail, not a ping.
    Meeting agenda "New regular/special agenda: <body> meeting on <date>" then
                   the meeting date/time in the body sentence (NOT the URL slug
                   -- confirmed independently twice that the slug can lie, see
                   uid3946 in the samples) and a lms.lacity.gov/meetings/<slug>
                   link. No PDF, no item list -- the page itself still needs a
                   fetch for agenda contents.

Unlike ENS mail, LMS notifications carry no attachments (the agenda PDF is
reached via the body's link, not attached) and aren't sent to a shared
listserv address -- To: is the individual subscriber -- so classification is
by subject-line shape, not list address.

No IMAP/credentials path here. Real mail arrives via the Gmail MCP connector,
which only a live Claude session can call -- a separate fetch step (not this
script) pulls new messages and writes them out as ordinary .eml files into a
staging directory; this script then processes that directory exactly like
eml_ingest.py processes the ENS backlog. That split keeps the Gmail-dependent
part tiny and keeps all the actual parsing here, pure Python, testable without
live mail access.

Usage:
    python3 lms_ingest.py /path/to/lms-eml-directory
    python3 lms_ingest.py /path/to/lms-eml-directory --dry-run
"""
import argparse
import datetime
import email
import glob
import json
import os
import re

import db
from eml_ingest import CF_RE, URL_RE, body_text, decode_hdr
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")

CF_ACTIVITY_RE = re.compile(
    r'There is (?:one|\d+) new actions? on Council File ([\d\-S]+), "(.+?)":',
    re.IGNORECASE,
)
# One "- <date>: <action>." line per new action, possibly several per email.
ACTION_LINE_RE = re.compile(
    r'^-\s*([A-Z][a-z]+ \d{1,2}, \d{4}):\s*(.+?)\.?\s*$',
    re.MULTILINE,
)
AGENDA_SUBJECT_RE = re.compile(
    r'New (regular|special)(?: meeting)? agenda:\s*(.+?)\s+meeting on (.+)$',
    re.IGNORECASE,
)
# "...for its meeting on Wednesday, October 28, 2026 at 10:45 AM." -- the real
# date, independent of the URL slug (which is allowed to be wrong; confirmed
# in uid3946).
AGENDA_DATE_RE = re.compile(
    r'meeting on ([A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}, \d{4}) at (\d{1,2}:\d{2}\s*[AP]M)',
)


def classify(subject, body):
    """(source_label, kind) where kind is 'cf_activity' | 'meeting_agenda' | None."""
    if subject.lower().startswith("new activity on council file") or CF_ACTIVITY_RE.search(body):
        return "lms_cf_activity", "cf_activity"
    if re.match(r'New (regular|special)(?: meeting)? agenda:', subject, re.IGNORECASE):
        return "lms_meeting_agenda", "meeting_agenda"
    return "lms_other", None


def parse_cf_activity(body):
    """-> (cf_number, title, [(date, action), ...]) or (None, None, [])."""
    m = CF_ACTIVITY_RE.search(body)
    if not m:
        return None, None, []
    cf, title = m.group(1), m.group(2)
    actions = ACTION_LINE_RE.findall(body)
    return cf, title, actions


def parse_meeting_agenda(subject, body):
    """-> (body_name, agenda_type, meeting_date_str) -- meeting_date_str is the
    body sentence's own date text, read for real, never the URL slug."""
    sm = AGENDA_SUBJECT_RE.search(subject)
    body_name = sm.group(2).strip() if sm else None
    agenda_type = sm.group(1).lower() if sm else None
    dm = AGENDA_DATE_RE.search(body)
    meeting_date = f"{dm.group(1)} {dm.group(2)}" if dm else None
    return body_name, agenda_type, meeting_date


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path", help="directory of LMS .eml files")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    files = sorted(glob.glob(os.path.join(args.path, "*.eml")))
    watchlist = set(json.load(open(WATCHLIST_PATH)).keys()) if os.path.exists(WATCHLIST_PATH) else set()

    with logged_run("lms_ingest", source_dir=args.path) as counts:
        conn = db.init()
        n_mail = n_skip = n_cf_activity = n_agenda = n_other = 0
        hits = {}
        all_cfs = set()

        for path in files:
            msg = email.message_from_file(open(path, encoding="utf-8", errors="replace"))
            message_id = (msg.get("Message-ID") or "").strip() or f"file:{os.path.basename(path)}"

            if conn.execute("SELECT 1 FROM emails WHERE message_id=?", (message_id,)).fetchone():
                n_skip += 1
                continue

            # RFC822 header folding can leave a literal newline inside a decoded
            # subject ("...Management\n Committee meeting..."); collapse it so
            # the agenda-subject regex (and the stored subject) read cleanly.
            subject = re.sub(r"\s+", " ", decode_hdr(msg.get("Subject"))).strip()
            from_addr = decode_hdr(msg.get("From"))
            received = msg.get("Date") or ""
            btext = body_text(msg)

            source, kind = classify(subject, btext)
            extra = {}
            if kind == "cf_activity":
                cf, title, actions = parse_cf_activity(btext)
                extra = {"cf_number": cf, "title": title,
                         "actions": [{"date": d, "action": a} for d, a in actions]}
                n_cf_activity += 1
            elif kind == "meeting_agenda":
                body_name, agenda_type, meeting_date = parse_meeting_agenda(subject, btext)
                extra = {"body": body_name, "agenda_type": agenda_type, "meeting_date": meeting_date}
                n_agenda += 1
            else:
                n_other += 1

            cfs = sorted(set(CF_RE.findall(btext)))
            all_cfs.update(cfs)
            links = sorted(set(URL_RE.findall(btext)))[:80]

            watch_hit = sorted(watchlist & set(cfs))
            if watch_hit:
                hits[subject[:60]] = watch_hit

            if not args.dry_run:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO emails (message_id, source, from_addr, subject, "
                    "received_at, body_text, cfnumbers_json, links_json, processed, retrieved_at) "
                    "VALUES (?,?,?,?,?,?,?,?,1,?)",
                    (message_id, source, from_addr, subject, received, btext[:2_000_000],
                     json.dumps(cfs), json.dumps(links),
                     datetime.datetime.now().isoformat(timespec="seconds")))
                if cur.rowcount:
                    db.index_row(conn, "email", cur.lastrowid, cfs[0] if cfs else "",
                                 subject, btext,
                                 {"from": from_addr, "date": received, "source": source,
                                  "cfnumbers": cfs, **extra})
                conn.commit()
            n_mail += 1
            detail = ""
            if kind == "cf_activity":
                detail = f"CF {extra.get('cf_number')}, {len(extra.get('actions', []))} action(s)"
            elif kind == "meeting_agenda":
                detail = f"{extra.get('body')} -- {extra.get('meeting_date')}"
            print(f"  [{source:<18}] {subject[:60]}  {detail}")

        counts.update(emails=n_mail, skipped=n_skip, cf_activity=n_cf_activity,
                      meeting_agenda=n_agenda, other=n_other, distinct_cfnumbers=len(all_cfs))
        print(f"\n{n_mail} email(s) ingested ({n_cf_activity} CF-activity, "
              f"{n_agenda} meeting-agenda, {n_other} unclassified), {n_skip} already seen, "
              f"{len(all_cfs)} distinct council file(s) referenced")
        if hits:
            print(f"\nWATCHLIST HITS ({len(hits)}):")
            for subj, cfs in hits.items():
                print(f"  {subj}\n    -> {cfs}")
        if args.dry_run:
            print("\n(dry run — nothing written)")


if __name__ == "__main__":
    main()
