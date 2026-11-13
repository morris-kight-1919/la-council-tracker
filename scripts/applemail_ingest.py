#!/usr/bin/env python3
"""Ingest City mail straight out of Apple Mail's local store -- no IMAP
credentials, no app password.

Mail keeps every message on disk as a .emlx file:

    ~/Library/Mail/V10/<account-uuid>/<Mailbox>.mbox/.../Messages/12345.emlx

The .emlx format is trivial: a first line giving the byte length of the
message, then that many bytes of ordinary RFC822, then an XML plist of
Mail's own metadata. So we read the RFC822 part and hand it to the same
parsing path the ENS backlog uses.

REQUIRES FULL DISK ACCESS. ~/Library/Mail is protected by macOS TCC, and an
unauthorised process gets "Operation not permitted" -- which looks like an
empty mailbox rather than a permission error. City Watch.md Section V calls
this exact trap out: Full Disk Access is granted **per binary, not per
script**, and their scheduled jobs failed under /bin/zsh while working under
/bin/bash, with the failures going to a log nobody read. So this script
checks access explicitly and says so loudly rather than reporting zero
messages.

Grant access to your terminal app (or the `claude` binary):
    System Settings -> Privacy & Security -> Full Disk Access -> +

Usage:
    python3 applemail_ingest.py --check
    python3 applemail_ingest.py --from lacity.org
    python3 applemail_ingest.py --from lacity.org --since 30
"""
import argparse
import datetime
import email
import glob
import json
import os
import re
import sys

import db
from eml_ingest import (CF_RE, URL_RE, attachment_text, body_text, decode_hdr,
                        list_type)
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")

MAIL_ROOTS = [
    os.path.expanduser("~/Library/Mail"),
    os.path.expanduser("~/Library/Containers/com.apple.mail/Data/Library/Mail"),
]


def check_access():
    """(ok, message). Distinguishes 'no permission' from 'no mail'."""
    for root in MAIL_ROOTS:
        if not os.path.exists(root):
            continue
        try:
            os.listdir(root)
            return True, root
        except PermissionError:
            return False, root
    return False, None


def read_emlx(path):
    """Parse a .emlx: length line, RFC822 payload, then a plist we ignore."""
    with open(path, "rb") as f:
        first = f.readline()
        try:
            length = int(first.strip())
        except ValueError:
            f.seek(0)
            return email.message_from_bytes(f.read())
        return email.message_from_bytes(f.read(length))


def find_messages(root, since_days=None):
    paths = glob.glob(os.path.join(root, "**", "*.emlx"), recursive=True)
    if since_days:
        cutoff = datetime.datetime.now().timestamp() - since_days * 86400
        paths = [p for p in paths if os.path.getmtime(p) >= cutoff]
    return sorted(paths)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--check", action="store_true", help="verify access only")
    p.add_argument("--from", dest="from_filter", default="lacity.org",
                   help="only messages whose From contains this (default: lacity.org)")
    p.add_argument("--since", type=int, help="only messages modified in the last N days")
    p.add_argument("--all", action="store_true", help="ingest regardless of sender")
    args = p.parse_args()

    ok, root = check_access()
    if not ok:
        print("NO ACCESS to Apple Mail's local store.")
        if root:
            print(f"  Blocked at: {root}")
        print("\n  macOS protects ~/Library/Mail behind Full Disk Access, and it is granted")
        print("  PER BINARY, not per script. Grant it to your terminal app:")
        print("    System Settings -> Privacy & Security -> Full Disk Access -> +")
        print("    then fully quit and reopen the terminal (a reload is not enough).")
        print("\n  Until then, exported .eml files work with no permissions at all:")
        print("    python3 eml_ingest.py /path/to/exported-mail")
        sys.exit(2)

    print(f"Access OK: {root}")
    if args.check:
        n = len(find_messages(root))
        print(f"  {n:,} .emlx message(s) visible")
        return

    watchlist = set(json.load(open(WATCHLIST_PATH)).keys()) if os.path.exists(WATCHLIST_PATH) else set()

    with logged_run("applemail_ingest") as counts:
        conn = db.init()
        paths = find_messages(root, args.since)
        print(f"{len(paths):,} message file(s) in scope\n")

        n_mail = n_skip = n_filtered = 0
        hits, all_cfs = {}, set()

        for path in paths:
            try:
                msg = read_emlx(path)
            except Exception:
                continue

            from_addr = decode_hdr(msg.get("From"))
            if not args.all and args.from_filter.lower() not in from_addr.lower():
                n_filtered += 1
                continue

            message_id = (msg.get("Message-ID") or "").strip() or f"emlx:{os.path.basename(path)}"
            if conn.execute("SELECT 1 FROM emails WHERE message_id=?", (message_id,)).fetchone():
                n_skip += 1
                continue

            subject = decode_hdr(msg.get("Subject"))
            to_addr = decode_hdr(msg.get("To"))
            source = list_type(to_addr, subject)
            text = body_text(msg)

            att_texts = []
            for part in msg.walk():
                fn = part.get_filename()
                if not fn:
                    continue
                payload = part.get_payload(decode=True)
                if payload:
                    t = attachment_text(decode_hdr(fn), payload)
                    if t:
                        att_texts.append(f"--- {decode_hdr(fn)} ---\n{t}")

            combined = text + "\n" + "\n".join(att_texts)
            cfs = sorted(set(CF_RE.findall(combined)))
            all_cfs.update(cfs)
            links = sorted(set(URL_RE.findall(combined)))[:80]

            watch_hit = sorted(watchlist & set(cfs))
            if watch_hit:
                hits[subject[:60]] = watch_hit

            cur = conn.execute(
                "INSERT OR IGNORE INTO emails (message_id, source, from_addr, subject, "
                "received_at, body_text, cfnumbers_json, links_json, processed, retrieved_at) "
                "VALUES (?,?,?,?,?,?,?,?,1,?)",
                (message_id, source, from_addr, subject, msg.get("Date") or "",
                 combined[:2_000_000], json.dumps(cfs), json.dumps(links),
                 datetime.datetime.now().isoformat(timespec="seconds")))
            if cur.rowcount:
                db.index_row(conn, "email", cur.lastrowid, cfs[0] if cfs else "",
                             subject, combined,
                             {"from": from_addr, "date": msg.get("Date"), "source": source,
                              "cfnumbers": cfs})
                n_mail += 1
                print(f"  [{source:<16}] {subject[:62]}  ({len(cfs)} CF#)")
            conn.commit()

        counts.update(scanned=len(paths), ingested=n_mail, already_seen=n_skip,
                      filtered_out=n_filtered, distinct_cfnumbers=len(all_cfs))
        print(f"\n{n_mail} ingested, {n_skip} already seen, {n_filtered} filtered by sender, "
              f"{len(all_cfs)} distinct council file(s)")
        if hits:
            print(f"\nWATCHLIST HITS ({len(hits)}):")
            for subj, cfs in hits.items():
                print(f"  {subj}\n    -> {cfs}")
        print("\nNext: python3 fetch_ens_links.py  (pulls the documents those emails point to)")


if __name__ == "__main__":
    main()
