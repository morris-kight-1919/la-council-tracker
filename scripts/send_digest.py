#!/usr/bin/env python3
"""Render and (optionally) send the digest of everything queued in the outbox.

Send semantics are deliberate, and follow City Watch.md Section VI, which is
the most emphatic passage in that document:

  * A BARE INVOCATION NEVER SENDS. Dry run is the default; --send is required.
    Their words: "Ours sends on a bare run, and it is a permanent hazard we
    have to remember around forever. Do not reproduce that mistake -- it is
    trivially avoidable on day one and unfixable later without breaking every
    caller."

  * The seen-pointer advances ONLY on a confirmed send. If SMTP fails, rows
    stay unsent and appear in the next digest. Nothing is silently dropped.

  * A STALE LAST-SENT DATE IS NOT A FAULT. Quiet weeks legitimately leave the
    pointer alone; health checks must not treat that as breakage.

  * We log WHAT was sent (the outbox ids), not merely that a send occurred,
    so "did this item go out on Tuesday?" is answerable from sent_digests.

  * The digest states its own coverage -- which sources it drew on and how
    current each was -- so "nothing happened" is distinguishable from "the
    pipeline is dead."

Sends through Mail.app via AppleScript/osascript -- not SMTP. No app
password: it sends as whatever account Mail.app already has configured and
authenticated (see City Watch.md Section V on macOS permission traps -- this
needs a different permission than Full Disk Access, System Settings ->
Privacy & Security -> Automation -> [your terminal app] -> Mail, checked).

Setup (only needed for actual sending):
    In MAX/.env --
        TRACKER_DIGEST_TO=<recipient address>

Usage:
    python3 send_digest.py                 # dry run -- prints, sends nothing
    python3 send_digest.py --send          # actually sends
    python3 send_digest.py --limit 50
"""
import argparse
import datetime
import json
import subprocess
import uuid

import db
from email_ingest import load_env
from run_log import logged_run

PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _applescript_escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_via_apple_mail(subject, body, to_addr):
    """Compose and send through the already-authenticated Mail.app account.
    Raises on failure -- caller must not advance the seen-pointer if this
    raises, same rule as an SMTP failure would follow."""
    script = f'''
tell application "Mail"
    set newMsg to make new outgoing message with properties {{subject:"{_applescript_escape(subject)}", content:"{_applescript_escape(body)}", visible:false}}
    tell newMsg
        make new to recipient at end of to recipients with properties {{address:"{_applescript_escape(to_addr)}"}}
    end tell
    send newMsg
end tell
'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed (check Automation permission for Mail)")


def pending(conn, limit=200):
    rows = conn.execute(
        "SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY created_at LIMIT ?",
        (limit,)).fetchall()
    return sorted(rows, key=lambda r: (PRIORITY_ORDER.get(r["priority"], 3), r["created_at"] or ""))


def coverage(conn):
    """What the digest is built from, and how current each source is --
    so silence is attributable rather than ambiguous."""
    lines = []
    r = conn.execute("SELECT MAX(retrieved_at) m, COUNT(*) n FROM documents").fetchone()
    lines.append(f"Council files / documents: {r['n']} tracked, newest fetch {r['m'] or 'never'}")
    # MAX on the ISO column, not the display date -- "May 29, 2026" sorts
    # above "Aug 05, 2026" as a string, which would silently understate how
    # current the data is. A wrong staleness signal is worse than none.
    r = conn.execute("SELECT COUNT(*) n, MAX(meeting_date_iso) m FROM transcripts").fetchone()
    lines.append(f"Meeting transcripts: {r['n']} meetings, latest {r['m'] or 'n/a'}")
    r = conn.execute("SELECT COUNT(*) n, MAX(retrieved_at) m FROM emails").fetchone()
    lines.append(f"City emails ingested: {r['n']}" +
                 (f", newest {r['m']}" if r["m"] else " (not configured yet)"))
    return lines


def render(rows, conn):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    out = [f"LA Council Tracker digest — {now}", "=" * 60, ""]
    if not rows:
        out.append("No new activity.")
    else:
        by_cf = {}
        for r in rows:
            by_cf.setdefault(r["cfnumber"] or "(no council file)", []).append(r)
        high = sum(1 for r in rows if r["priority"] == "high")
        out.append(f"{len(rows)} item(s) across {len(by_cf)} council file(s)"
                   + (f" — {high} high priority" if high else ""))
        out.append("")
        for cf, items in by_cf.items():
            out.append(f"── {cf}")
            for it in items:
                tag = f"[{(it['priority'] or 'low').upper()}]"
                out.append(f"   {tag} {it['summary']}")
                payload = json.loads(it["payload_json"] or "{}")
                if payload.get("url"):
                    out.append(f"        {payload['url']}")
            out.append("")

    out.append("")
    out.append("Coverage")
    out.append("-" * 60)
    out.extend(coverage(conn))
    out.append("")
    out.append("A quiet digest means the City published nothing new — not that "
               "the pipeline stopped. Run health_check.py to tell the difference.")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true",
                   help="actually send. WITHOUT THIS FLAG NOTHING IS SENT.")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--baseline", action="store_true",
                   help="mark everything currently queued as already-delivered "
                        "without sending, so the digest starts clean. For the "
                        "initial bulk load, where every item qualifies at once "
                        "and a 130-item digest is unreadable. Items stay in the "
                        "database and remain reviewable via score_corpus.py.")
    args = p.parse_args()

    if args.baseline:
        conn = db.init()
        rows = conn.execute("SELECT id FROM outbox WHERE sent_at IS NULL").fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            print("Nothing queued; nothing to baseline.")
            return
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        conn.executemany("UPDATE outbox SET sent_at=?, digest_id='backlog-baseline' WHERE id=?",
                         [(stamp, i) for i in ids])
        conn.execute("INSERT OR REPLACE INTO sent_digests (digest_id, sent_at, recipient, "
                     "subject, item_count, item_ids) VALUES (?,?,?,?,?,?)",
                     ("backlog-baseline", stamp, "(not sent — baseline)",
                      "Initial bulk load marked as seen", len(ids), json.dumps(ids)))
        conn.commit()
        print(f"Baselined {len(ids)} item(s). The digest is now clean; only NEW "
              f"material will appear from here.")
        print("The backlog is still reviewable:  python3 score_corpus.py --top 20 / --fire")
        return

    with logged_run("send_digest", mode="send" if args.send else "dry-run") as counts:
        conn = db.init()
        rows = pending(conn, args.limit)
        body = render(rows, conn)
        counts["pending_items"] = len(rows)

        if not args.send:
            print(body)
            print("\n" + "=" * 60)
            print(f"DRY RUN — nothing sent. {len(rows)} item(s) still queued.")
            print("Re-run with --send to actually deliver.")
            counts["sent"] = 0
            return

        if not rows:
            # Quiet period. Not a fault; do not send an empty email.
            print("Nothing queued; no digest sent. (This is normal, not an error.)")
            counts["sent"] = 0
            return

        env = load_env()
        to_addr = env.get("TRACKER_DIGEST_TO")
        if not to_addr:
            print("Cannot send — missing TRACKER_DIGEST_TO in MAX/.env")
            print("Items remain queued; nothing was lost.")
            counts["sent"] = 0
            return

        digest_id = uuid.uuid4().hex[:12]
        subject = f"LA Council Tracker — {len(rows)} update(s) {datetime.date.today()}"

        try:
            send_via_apple_mail(subject, body, to_addr)
        except Exception as e:
            # Pointer does NOT advance. Items stay queued for the next run.
            print(f"SEND FAILED: {e}")
            print(f"{len(rows)} item(s) remain queued and will go out next time. Nothing lost.")
            counts["sent"] = 0
            counts["send_error"] = str(e)
            raise

        # Only now, after a confirmed send, does the pointer move.
        sent_at = datetime.datetime.now().isoformat(timespec="seconds")
        ids = [r["id"] for r in rows]
        conn.executemany("UPDATE outbox SET sent_at=?, digest_id=? WHERE id=?",
                         [(sent_at, digest_id, i) for i in ids])
        conn.execute("INSERT INTO sent_digests (digest_id, sent_at, recipient, subject, "
                     "item_count, item_ids) VALUES (?,?,?,?,?,?)",
                     (digest_id, sent_at, to_addr, subject, len(ids), json.dumps(ids)))
        conn.commit()
        counts.update(sent=len(ids), digest_id=digest_id)
        print(f"Sent digest {digest_id}: {len(ids)} item(s) to {to_addr}")


if __name__ == "__main__":
    main()
