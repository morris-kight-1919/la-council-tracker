#!/usr/bin/env python3
"""On-demand backfill: generate AI summaries for archived documents that
don't have one yet. Run manually when the machine has spare CPU capacity --
NOT wired into check_updates.py's automatic daily run, since local Ollama
inference competes for CPU with the Morris Kight transcription job running
on this same machine. Safe to run anytime; just don't schedule it."""
import json
import os

from ai_summarize import summarize

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DOCS_DIR = os.path.join(ROOT, "documents")
SUMMARIES_PATH = os.path.join(ROOT, "data", "document_summaries.json")


def main():
    summaries = json.load(open(SUMMARIES_PATH)) if os.path.exists(SUMMARIES_PATH) else {}
    done = 0
    skipped = 0

    for cf in sorted(os.listdir(DOCS_DIR)):
        cf_dir = os.path.join(DOCS_DIR, cf)
        if not os.path.isdir(cf_dir):
            continue
        for fname in sorted(os.listdir(cf_dir)):
            if not fname.endswith(".txt"):
                continue
            key = f"{cf}/{fname}"
            if key in summaries:
                continue

            path = os.path.join(cf_dir, fname)
            text = open(path, encoding="utf-8", errors="replace").read()
            if len(text.strip()) < 200:
                skipped += 1
                continue  # too short to be worth an AI summary; gist covers it

            title = fname.rsplit("_", 1)[0].replace("_", " ")
            print(f"Summarizing {key}...")
            summary = summarize(title, text)
            if summary:
                summaries[key] = summary
                done += 1
                json.dump(summaries, open(SUMMARIES_PATH, "w"), indent=1)
            else:
                print("  (failed or timed out, will retry next run)")

    print(f"\nDone: {done} summarized, {skipped} skipped (too short)")


if __name__ == "__main__":
    main()
