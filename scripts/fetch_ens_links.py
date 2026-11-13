#!/usr/bin/env python3
"""Follow the links inside ingested ENS emails and pull the actual documents.

ENS mail is a pointer, not a payload. A council-agenda notice is a ~2 KB body
with a 500-byte .htm stub attached; the real agenda lives at a URL in the
body, e.g.

    http://ens.lacity.org/clk/councilagendas/clkcouncilagendas3199074_08042026.htm
    https://lacity.primegov.com/Portal/Meeting?meetingTemplateId=150129

So the email tells you *what is worth fetching* -- exactly the inversion the
RIP LA design describes -- and this step does the fetching. That's why the
agenda emails initially yielded zero council-file numbers: the numbers are in
the linked document, not the message.

These are re-fetchable URLs, so the documents are stored WITHOUT
never_delete (unlike email attachments, which exist only in the message).

Usage:
    python3 fetch_ens_links.py            # fetch anything not already stored
    python3 fetch_ens_links.py --dry-run
"""
import argparse
import datetime
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.request

import db
import extract_entities
import primegov_client
from run_log import logged_run

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Only follow links that are actual documents. Skip listserv admin pages
# (listserv.lacity.org), shorteners that need a redirect resolved first
# (bit.ly, tinyurl.com), and non-document destinations (zoom, youtube, drive,
# fcc.gov, usda.gov). ens.lacity.org was the only host originally handled
# here; a direct check against the ingested backlog showed real document and
# agenda content also arrives at these other City hosts -- silently skipped
# before, not even logged as failures (forensic audit finding #4, 2026-08-08).
FETCHABLE = re.compile(
    r"^https?://(ens\.lacity\.org|bpw\.lacity\.org|dpw\.lacity\.gov|"
    r"cityclerk\.lacity\.org|clkrep\.lacity\.org|planning\.lacity\.(org|gov)|"
    r"www\.lacity\.org)/.+\.(htm|html|pdf)$", re.I)
CF_RE = re.compile(r"\b\d{2}-\d{4}(?:-S\d+)?\b")
PRIMEGOV_MEETING = re.compile(r"primegov\.com/Portal/Meeting\?meetingTemplateId=(\d+)", re.I)


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.HTTPError, urllib.error.URLError):
            time.sleep(2 * (attempt + 1))
    return None


def to_text(url, payload):
    if url.lower().endswith(".pdf"):
        import os, subprocess, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "a.pdf")
            t = os.path.join(tmp, "a.txt")
            open(p, "wb").write(payload)
            try:
                subprocess.run(["pdftotext", "-layout", p, t], capture_output=True, timeout=120)
                if os.path.exists(t):
                    return open(t, encoding="utf-8", errors="replace").read()
            except Exception:
                return ""
        return ""
    # These pages are Word exports and declare windows-1252, not UTF-8.
    # Decoding as UTF-8 mangles names -- "SOTO-MARTÍNEZ" became "SOTO-MART?NEZ".
    # Honour the declared charset before falling back.
    head = payload[:2048].decode("ascii", errors="replace").lower()
    m = re.search(r'charset\s*=\s*["\']?([\w-]+)', head)
    encoding = m.group(1) if m else "utf-8"
    try:
        raw = payload.decode(encoding, errors="replace")
    except LookupError:
        raw = payload.decode("utf-8", errors="replace")
    raw = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    with logged_run("fetch_ens_links") as counts:
        conn = db.init()
        have = {r[0] for r in conn.execute("SELECT source_url FROM documents")}

        targets = []       # (url, email_id, subject, source)
        meeting_ids = set()
        for r in conn.execute("SELECT id, subject, source, links_json FROM emails"):
            for url in json.loads(r["links_json"] or "[]"):
                m = PRIMEGOV_MEETING.search(url)
                if m:
                    meeting_ids.add(m.group(1))
                if FETCHABLE.match(url) and url not in have:
                    targets.append((url, r["id"], r["subject"], r["source"]))

        # de-dupe while preserving order
        seen, uniq = set(), []
        for t in targets:
            if t[0] not in seen:
                seen.add(t[0])
                uniq.append(t)

        print(f"{len(uniq)} ENS document link(s) to fetch; "
              f"{len(meeting_ids)} PrimeGov meeting id(s) referenced\n")

        n_ok = n_fail = 0
        total_cfs = set()
        for url, email_id, subject, source in uniq:
            if args.dry_run:
                print(f"  would fetch: {url}")
                continue
            payload = fetch(url)
            if not payload:
                print(f"  FAILED {url}")
                n_fail += 1
                continue
            text = to_text(url, payload)
            cfs = sorted(set(CF_RE.findall(text)))
            total_cfs.update(cfs)
            title = url.rsplit("/", 1)[-1]
            conn.execute(
                "INSERT OR IGNORE INTO documents "
                "(cfnumber, doc_type, title, source_url, sha256, full_text, fields_json, "
                " retrieved_at, pdf_path, pdf_deleted) VALUES (?,?,?,?,?,?,?,?,NULL,1)",
                (cfs[0] if cfs else None, f"ens_linked_{source}", title, url,
                 hashlib.sha256(payload).hexdigest(), text,
                 json.dumps(extract_entities.extract(text)),
                 datetime.datetime.now().isoformat(timespec="seconds")))
            row = conn.execute("SELECT id FROM documents WHERE source_url=?", (url,)).fetchone()
            if row:
                db.index_row(conn, "document", row["id"], cfs[0] if cfs else "",
                             f"{subject[:60]} — {title}", text,
                             {"doc_type": f"ens_linked_{source}", "url": url})
            conn.commit()
            n_ok += 1
            print(f"  {len(text):>7} chars, {len(cfs):>3} CF#  {title[:56]}")

        # PrimeGov meeting links: an ENS email hands us the exact
        # meetingTemplateId, a stable identifier -- fetch that agenda
        # directly rather than leaving it as an unused count (it used to be
        # extracted into meeting_ids and never fetched at all; forensic
        # audit finding #4, 2026-08-08). Store the whole agenda as one
        # document per meeting so it's searchable and holds real
        # recommendation text straight from the source, no fuzzy date-window
        # search needed for anything that arrived via ENS mail.
        n_pg_ok = n_pg_fail = 0
        for mid in sorted(meeting_ids):
            url = f"https://lacity.primegov.com/Portal/Meeting?meetingTemplateId={mid}"
            if url in have:
                continue
            if args.dry_run:
                print(f"  would fetch primegov meeting: {url}")
                continue
            try:
                items = primegov_client.fetch_agenda_by_template_id(mid)
            except Exception as e:
                print(f"  FAILED primegov meeting {mid}: {e}")
                n_pg_fail += 1
                continue
            if not items:
                n_pg_fail += 1
                continue
            text = "\n\n".join(
                f"{it.get('cfnumber') or ''} — {it.get('title') or ''}\n{it.get('recommendation') or ''}"
                for it in items)
            cfs = sorted({it["cfnumber"] for it in items if it.get("cfnumber")})
            total_cfs.update(cfs)
            title = f"PrimeGov agenda (meeting {mid})"
            conn.execute(
                "INSERT OR IGNORE INTO documents "
                "(cfnumber, doc_type, title, source_url, sha256, full_text, fields_json, "
                " retrieved_at, pdf_path, pdf_deleted) VALUES (?,?,?,?,?,?,?,?,NULL,1)",
                (cfs[0] if cfs else None, "ens_linked_primegov_agenda", title, url,
                 hashlib.sha256(text.encode()).hexdigest(), text,
                 json.dumps(extract_entities.extract(text)),
                 datetime.datetime.now().isoformat(timespec="seconds")))
            row = conn.execute("SELECT id FROM documents WHERE source_url=?", (url,)).fetchone()
            if row:
                db.index_row(conn, "document", row["id"], cfs[0] if cfs else "",
                             title, text, {"doc_type": "ens_linked_primegov_agenda",
                                           "url": url, "cfnumbers": cfs})
            conn.commit()
            n_pg_ok += 1
            print(f"  primegov meeting {mid}: {len(items)} item(s), {len(cfs)} CF#")

        counts.update(links=len(uniq), fetched=n_ok, failed=n_fail,
                      distinct_cfnumbers=len(total_cfs),
                      primegov_meetings=len(meeting_ids),
                      primegov_fetched=n_pg_ok, primegov_failed=n_pg_fail)
        print(f"\nfetched {n_ok}, failed {n_fail}, {len(total_cfs)} distinct council file(s) found")
        print(f"primegov meetings: {n_pg_ok} fetched, {n_pg_fail} failed/empty")
        if args.dry_run:
            print("(dry run — nothing written)")


if __name__ == "__main__":
    main()
