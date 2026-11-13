#!/usr/bin/env python3
"""Ingest City emails (council-file subscriptions, ENS notices, agenda mail)
into the corpus, and use them as the trigger for what to look at.

This is the "core inversion" from the RIP Los Angeles design: the email
decides what is worth fetching, rather than a crawler sweeping everything.
Their framing -- "ENS is the trigger, not a crawler" -- is the right shape,
and it is also the answer to the notification gap in this tracker: an alert
about a hearing three days out is worthless if you only learn about it by
remembering to run a script.

Reads IMAP, stores every message, extracts council-file numbers and document
links, and flags messages that touch anything on the watchlist.

Setup required (nothing here works until this exists):
    In MAX/.env --
        TRACKER_IMAP_HOST=imap.gmail.com
        TRACKER_IMAP_USER=<the address subscribed to City lists>
        TRACKER_IMAP_PASS=<app password, NOT the account password>
        TRACKER_IMAP_FOLDER=INBOX        (optional)

Gmail note, and the exact trap the RIP LA build got stuck on for a week: with
2FA enabled you cannot use the account password. Generate an App Password
(Google Account -> Security -> App passwords) and use that. A dedicated
address is strongly preferred -- City subscriptions are high-volume.

Usage:
    python3 email_ingest.py --check          # verify credentials/connection only
    python3 email_ingest.py --since 7        # ingest last 7 days
    python3 email_ingest.py                  # ingest everything unseen
"""
import argparse
import datetime
import email
import email.header
import imaplib
import json
import os
import re
import sys

import db
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", "..", ".env")   # MAX/.env

CF_RE = re.compile(r"\b(\d{2}-\d{4}(?:-S\d+)?)\b")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")


def load_env():
    """Parse MAX/.env without printing any secret."""
    env = {}
    if os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("TRACKER_IMAP_HOST", "TRACKER_IMAP_USER", "TRACKER_IMAP_PASS",
              "TRACKER_IMAP_FOLDER"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def classify_source(from_addr, subject):
    f = (from_addr or "").lower()
    s = (subject or "").lower()
    if "cityclerk" in f or "council file" in s or "cfms" in s:
        return "cityclerk_cf"
    if "ens" in f or "early notification" in s:
        return "ens"
    if "agenda" in s:
        return "agenda"
    return "unknown"


def decode_header(raw):
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def body_text(msg):
    """Plain text body; falls back to stripping tags from HTML."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    html = payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace")
                    return re.sub(r"<[^>]+>", " ", html)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def connect(env):
    host = env.get("TRACKER_IMAP_HOST")
    user = env.get("TRACKER_IMAP_USER")
    password = env.get("TRACKER_IMAP_PASS")
    missing = [k for k, v in [("TRACKER_IMAP_HOST", host), ("TRACKER_IMAP_USER", user),
                              ("TRACKER_IMAP_PASS", password)] if not v]
    if missing:
        raise RuntimeError(
            "Missing credentials in MAX/.env: " + ", ".join(missing) +
            "\nSee the setup block at the top of this file. Nothing was contacted.")
    conn = imaplib.IMAP4_SSL(host)
    conn.login(user, password)          # value never printed
    conn.select(env.get("TRACKER_IMAP_FOLDER", "INBOX"))
    return conn


def ingest(days=None):
    env = load_env()
    with logged_run("email_ingest") as counts:
        conn_db = db.init()
        watchlist = set()
        wl_path = os.path.join(HERE, "..", "watchlist.json")
        if os.path.exists(wl_path):
            watchlist = set(json.load(open(wl_path)).keys())

        imap = connect(env)
        if days:
            since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = imap.search(None, f'(SINCE "{since}")')
        else:
            typ, data = imap.search(None, "ALL")
        ids = data[0].split()
        print(f"{len(ids)} message(s) in scope")

        added = skipped = hits = 0
        for num in ids:
            typ, msgdata = imap.fetch(num, "(RFC822)")
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            msg = email.message_from_bytes(msgdata[0][1])

            # message_id is the stable upstream identifier -- dedupe on it,
            # never on subject or rendered text.
            message_id = (msg.get("Message-ID") or "").strip()
            if not message_id:
                message_id = f"nomsgid:{msg.get('Date','')}:{decode_header(msg.get('Subject'))[:80]}"

            exists = conn_db.execute("SELECT 1 FROM emails WHERE message_id=?",
                                     (message_id,)).fetchone()
            if exists:
                skipped += 1
                continue

            subject = decode_header(msg.get("Subject"))
            from_addr = decode_header(msg.get("From"))
            received = msg.get("Date") or ""
            text = body_text(msg)
            cfs = sorted(set(CF_RE.findall(text + " " + subject)))
            links = sorted(set(URL_RE.findall(text)))[:60]
            source = classify_source(from_addr, subject)

            cur = conn_db.execute(
                "INSERT OR IGNORE INTO emails (message_id, source, from_addr, subject, "
                "received_at, body_text, cfnumbers_json, links_json, processed, retrieved_at) "
                "VALUES (?,?,?,?,?,?,?,?,0,?)",
                (message_id, source, from_addr, subject, received, text,
                 json.dumps(cfs), json.dumps(links),
                 datetime.datetime.now().isoformat(timespec="seconds")))
            if cur.rowcount:
                added += 1
                db.index_row(conn_db, "email", cur.lastrowid,
                             cfs[0] if cfs else "", subject, text,
                             {"from": from_addr, "date": received, "source": source,
                              "cfnumbers": cfs})
                if watchlist & set(cfs):
                    hits += 1
                    print(f"  WATCH HIT: {subject[:70]}")
                    print(f"    council files: {sorted(watchlist & set(cfs))}")
            conn_db.commit()

        imap.close()
        imap.logout()
        counts.update(scanned=len(ids), added=added, already_seen=skipped, watch_hits=hits)
        print(f"\ningested {added} new, skipped {skipped} already seen, {hits} watchlist hit(s)")


def check():
    env = load_env()
    present = {k: bool(env.get(k)) for k in
               ("TRACKER_IMAP_HOST", "TRACKER_IMAP_USER", "TRACKER_IMAP_PASS")}
    print("Credential check (values never printed):")
    for k, v in present.items():
        print(f"  {k}: {'set' if v else 'MISSING'}")
    if not all(present.values()):
        print("\nNot configured yet. See the setup block at the top of this file.")
        return 1
    try:
        imap = connect(env)
        typ, data = imap.search(None, "ALL")
        print(f"\nConnected OK. {len(data[0].split())} message(s) in "
              f"{env.get('TRACKER_IMAP_FOLDER', 'INBOX')}.")
        imap.close(); imap.logout()
        return 0
    except Exception as e:
        print(f"\nConnection failed: {e}")
        print("If this is Gmail with 2FA, you need an App Password, not the account password.")
        return 1


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="verify credentials and connection only")
    p.add_argument("--since", type=int, help="only messages from the last N days")
    args = p.parse_args()
    if args.check:
        sys.exit(check())
    ingest(days=args.since)
