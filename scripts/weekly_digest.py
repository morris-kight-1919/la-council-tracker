#!/usr/bin/env python3
"""Condense the last 7 days of CHANGELOG.md into a single digest: what
changed, grouped by file, with high-signal items and public comments/
recommendation diffs called out separately from routine housekeeping."""
import datetime
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
CHANGELOG_PATH = os.path.join(ROOT, "CHANGELOG.md")
OUT_PATH = os.path.join(ROOT, "WEEKLY_DIGEST.md")

DAYS = 7


def parse_changelog():
    if not os.path.exists(CHANGELOG_PATH):
        return []
    text = open(CHANGELOG_PATH).read()
    # split on run headers: "## YYYY-MM-DD HH:MM"
    runs = re.split(r"\n(?=## \d{4}-\d{2}-\d{2} \d{2}:\d{2})", text)
    parsed = []
    for run in runs:
        m = re.match(r"## (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})", run)
        if not m:
            continue
        run_date = datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date()
        # split into per-file blocks: "### CF — title"
        file_blocks = re.split(r"\n(?=### )", run)[1:]
        for block in file_blocks:
            fm = re.match(r"### (\S+) — (.+)", block)
            if not fm:
                continue
            cf, title = fm.group(1), fm.group(2)
            lines = [l for l in block.split("\n") if l.strip().startswith("-")]
            parsed.append({"date": run_date, "cf": cf, "title": title, "lines": lines, "full_text": block})
    return parsed


def main():
    entries = parse_changelog()
    cutoff = datetime.date.today() - datetime.timedelta(days=DAYS)
    recent = [e for e in entries if e["date"] >= cutoff]

    if not recent:
        print(f"No changelog activity in the last {DAYS} days.")
        return

    by_cf = {}
    for e in recent:
        by_cf.setdefault(e["cf"], {"title": e["title"], "lines": [], "full_text": ""})
        by_cf[e["cf"]]["lines"].extend(e["lines"])
        by_cf[e["cf"]]["full_text"] += e["full_text"]

    high_signal = []
    public_comments = []
    rec_diffs = []
    total_lines = 0
    for cf, data in by_cf.items():
        for line in data["lines"]:
            total_lines += 1
            if "[HIGH/" in line:
                high_signal.append((cf, data["title"], line))
            if "PUBLIC COMMENT" in line:
                public_comments.append((cf, data["title"], line))
        if "Recommendation text changed" in data["full_text"]:
            rec_diffs.append((cf, data["title"], None))

    out = [f"# Weekly Digest — last {DAYS} days ({cutoff.isoformat()} to {datetime.date.today().isoformat()})\n"]
    out.append(f"\n{len(by_cf)} file(s) touched, {total_lines} total line item(s).\n")

    if high_signal:
        out.append("\n## High-signal events\n")
        for cf, title, line in high_signal:
            out.append(f"- **{cf}** ({title[:60]}): {line.strip('- ')}")

    if rec_diffs:
        out.append("\n## Recommendation text changes\n")
        for cf, title, line in rec_diffs:
            out.append(f"- **{cf}** ({title[:60]})")

    if public_comments:
        out.append(f"\n## New public comments ({len(public_comments)})\n")
        for cf, title, line in public_comments:
            out.append(f"- **{cf}**: {line.strip('- ')}")

    out.append("\n## All files touched this week\n")
    for cf, data in sorted(by_cf.items()):
        out.append(f"- **{cf}** — {data['title'][:80]} ({len(data['lines'])} line item(s))")

    with open(OUT_PATH, "w") as f:
        f.write("\n".join(out) + "\n")
    print(f"Wrote digest to {OUT_PATH}: {len(by_cf)} files, {total_lines} line items, "
          f"{len(high_signal)} high-signal, {len(public_comments)} public comments, {len(rec_diffs)} rec-diffs")


if __name__ == "__main__":
    main()
