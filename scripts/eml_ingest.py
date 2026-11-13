#!/usr/bin/env python3
"""Ingest exported .eml files from LA's Early Notification System.

This is the "core inversion" from the RIP LA design working for real: the
City's own mail decides what's worth looking at, rather than a crawler
sweeping everything. ENS mail arrives from ita.ensla@lacity.org via
topic-specific listservs, and the useful ones are:

    CLK_REFERRALMEMO      council-file referral memos -- these ARE stage
                          transitions, often reaching mail before the
                          activity log shows them
    CLK_COUNCILAGENDAS    council agendas -- the leftward push, since an
                          agenda is published before the meeting
    BPW_AGENDAS / BPW_*   Board of Public Works
    PLN_*                 Planning / Cultural Heritage

Messages carry attachments (PDF agendas, HTM referral memos), which is where
the substance lives -- so attachments are extracted, text-mined and stored,
not just noted.

IMPORTANT retention difference: email attachments are NOT re-fetchable from a
URL the way City Clerk onlinedocs are. They exist only in the message. So
they're stored with never_delete=1 -- the same carve-out the RIP LA design
made for CPRA productions, and for the same reason: don't auto-delete
something you cannot get back.

Usage:
    python3 eml_ingest.py /path/to/ens-backlog
    python3 eml_ingest.py /path/to/ens-backlog --dry-run
"""
import argparse
import datetime
import email
import email.header
import glob
import hashlib
import html
import json
import os
import re
import subprocess
import tempfile

import db
import extract_entities
from run_log import logged_run

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
ATTACH_DIR = os.path.join(ROOT, "ens_attachments")
WATCHLIST_PATH = os.path.join(ROOT, "watchlist.json")

CF_RE = re.compile(r"\b\d{2}-\d{4}(?:-S\d+)?\b")
URL_RE = re.compile(r"https?://[^\s<>\"')]+")

# Listserv address -> what the list is. Derived from the actual backlog.
LIST_TYPES = [
    (r"CLK_REFERRALMEMO", "referral_memo"),
    (r"CLK_COUNCILAGENDAS", "council_agenda"),
    (r"BPW_AGENDAS", "bpw_agenda"),
    (r"BPW_", "bpw_other"),
    (r"PLN_", "planning_notice"),
    (r"CLK_", "clerk_notice"),
    (r"LAHD", "housing_notice"),
]


def decode_hdr(raw):
    if not raw:
        return ""
    out = []
    for text, enc in email.header.decode_header(raw):
        out.append(text.decode(enc or "utf-8", errors="replace")
                   if isinstance(text, bytes) else text)
    return "".join(out)


def list_type(to_addr, subject):
    hay = f"{to_addr} {subject}".upper()
    for pat, label in LIST_TYPES:
        if re.search(pat, hay):
            return label
    return "ens_other"


def body_text(msg):
    for part in msg.walk():
        if part.get_content_type() == "text/plain" and not part.get_filename():
            p = part.get_payload(decode=True)
            if p:
                return p.decode(part.get_content_charset() or "utf-8", errors="replace")
    for part in msg.walk():
        if part.get_content_type() == "text/html" and not part.get_filename():
            p = part.get_payload(decode=True)
            if p:
                raw = p.decode(part.get_content_charset() or "utf-8", errors="replace")
                return html.unescape(re.sub(r"<[^>]+>", " ", raw))
    return ""


def attachment_text(filename, payload):
    """Text out of an attachment. PDFs via pdftotext, HTM by stripping tags."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in ("htm", "html"):
        # City HTM attachments are Word exports declaring windows-1252.
        # Decoding as UTF-8 mangles names ("SOTO-MARTÍNEZ" -> "SOTO-MART?NEZ").
        head = payload[:4096].decode("ascii", errors="replace").lower()
        m = re.search(r'charset\s*=\s*["\']?([\w-]+)', head)
        try:
            raw = payload.decode(m.group(1) if m else "utf-8", errors="replace")
        except LookupError:
            raw = payload.decode("utf-8", errors="replace")
        raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
    if ext == "pdf":
        with tempfile.TemporaryDirectory() as tmp:
            pdf = os.path.join(tmp, "a.pdf")
            txt = os.path.join(tmp, "a.txt")
            open(pdf, "wb").write(payload)
            try:
                subprocess.run(["pdftotext", "-layout", pdf, txt],
                               capture_output=True, timeout=120)
                if os.path.exists(txt):
                    return open(txt, encoding="utf-8", errors="replace").read()
            except Exception:
                return ""
    return ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path", help="directory of .eml files")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    files = sorted(glob.glob(os.path.join(args.path, "*.eml")))
    watchlist = set(json.load(open(WATCHLIST_PATH)).keys()) if os.path.exists(WATCHLIST_PATH) else set()

    with logged_run("eml_ingest", source_dir=args.path) as counts:
        conn = db.init()
        os.makedirs(ATTACH_DIR, exist_ok=True)
        n_mail = n_skip = n_att = 0
        hits = {}
        all_cfs = set()

        for path in files:
            msg = email.message_from_file(open(path, encoding="utf-8", errors="replace"))
            message_id = (msg.get("Message-ID") or "").strip() or f"file:{os.path.basename(path)}"

            if conn.execute("SELECT 1 FROM emails WHERE message_id=?", (message_id,)).fetchone():
                n_skip += 1
                continue

            subject = decode_hdr(msg.get("Subject"))
            from_addr = decode_hdr(msg.get("From"))
            to_addr = decode_hdr(msg.get("To"))
            received = msg.get("Date") or ""
            source = list_type(to_addr, subject)
            btext = body_text(msg)

            # Attachments: extract text, keep the file (not re-fetchable).
            att_texts, att_names = [], []
            for part in msg.walk():
                fn = part.get_filename()
                if not fn:
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                fn = decode_hdr(fn)
                att_names.append(fn)
                text = attachment_text(fn, payload)
                if text:
                    att_texts.append(f"--- {fn} ---\n{text}")
                if not args.dry_run:
                    dest = os.path.join(ATTACH_DIR, fn)
                    if not os.path.exists(dest):
                        open(dest, "wb").write(payload)
                    sha = hashlib.sha256(payload).hexdigest()
                    doc_text = text or ""
                    cf_in_att = CF_RE.findall(doc_text)
                    conn.execute(
                        "INSERT OR IGNORE INTO documents "
                        "(cfnumber, doc_type, title, doc_date, source_url, sha256, full_text, "
                        " fields_json, retrieved_at, pdf_path, pdf_deleted, never_delete) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                        (cf_in_att[0] if cf_in_att else None, f"ens_{source}", fn, None,
                         f"eml:{message_id}#{fn}", sha, doc_text,
                         json.dumps(extract_entities.extract(doc_text)),
                         datetime.datetime.now().isoformat(timespec="seconds"), dest, 0))
                n_att += 1

            combined = btext + "\n" + "\n".join(att_texts)
            cfs = sorted(set(CF_RE.findall(combined)))
            all_cfs.update(cfs)
            links = sorted(set(URL_RE.findall(combined)))[:80]

            watch_hit = sorted(watchlist & set(cfs))
            if watch_hit:
                hits[subject[:60]] = watch_hit

            if not args.dry_run:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO emails (message_id, source, from_addr, subject, "
                    "received_at, body_text, cfnumbers_json, links_json, processed, retrieved_at) "
                    "VALUES (?,?,?,?,?,?,?,?,1,?)",
                    (message_id, source, from_addr, subject, received, combined[:2_000_000],
                     json.dumps(cfs), json.dumps(links),
                     datetime.datetime.now().isoformat(timespec="seconds")))
                if cur.rowcount:
                    db.index_row(conn, "email", cur.lastrowid, cfs[0] if cfs else "",
                                 subject, combined,
                                 {"from": from_addr, "date": received, "source": source,
                                  "cfnumbers": cfs, "attachments": att_names})
                conn.commit()
            n_mail += 1
            print(f"  [{source:<16}] {subject[:64]}  ({len(cfs)} CF#, {len(att_names)} att)")

        counts.update(emails=n_mail, skipped=n_skip, attachments=n_att,
                      distinct_cfnumbers=len(all_cfs), watch_hits=len(hits))
        print(f"\n{n_mail} email(s) ingested, {n_skip} already seen, {n_att} attachment(s), "
              f"{len(all_cfs)} distinct council file(s) referenced")
        if hits:
            print(f"\nWATCHLIST HITS ({len(hits)}):")
            for subj, cfs in hits.items():
                print(f"  {subj}\n    -> {cfs}")
        if args.dry_run:
            print("\n(dry run — nothing written)")


if __name__ == "__main__":
    main()
