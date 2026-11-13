#!/usr/bin/env python3
"""Build one searchable index across everything the tracker has archived:
watched council files, downloaded/extracted documents (with AI summaries
where available), and cached meeting transcripts. Produces index.html, a
single self-contained local file with a client-side search box."""
import html
import json
import os

from documents import is_public_comment

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA_DIR = os.path.join(ROOT, "data")
DOCS_DIR = os.path.join(ROOT, "documents")
TRANSCRIPT_DIR = os.path.join(ROOT, "transcripts")
SUMMARIES_PATH = os.path.join(DATA_DIR, "document_summaries.json")
OUT_PATH = os.path.join(ROOT, "index.html")


def load(path, default):
    if os.path.exists(path):
        return json.load(open(path))
    return default


def build_entries():
    entries = []
    summaries = load(SUMMARIES_PATH, {})

    # 1. Watched council files
    for fname in sorted(os.listdir(DATA_DIR)):
        if not fname.endswith(".json") or fname == "document_summaries.json":
            continue
        if os.path.isdir(os.path.join(DATA_DIR, fname)):
            continue
        record = json.load(open(os.path.join(DATA_DIR, fname)))
        cf = record.get("cfnumber", fname.replace(".json", ""))
        activities = record.get("activities", [])
        snippet = "; ".join(f"{a['date']}: {a['activity']}" for a in activities[:3])
        entries.append({
            "type": "council_file",
            "cfnumber": cf,
            "title": f"{cf} — {record.get('title', '')}",
            "snippet": snippet,
            "link": record.get("url", ""),
        })

    # 2. Archived documents
    if os.path.isdir(DOCS_DIR):
        for cf in sorted(os.listdir(DOCS_DIR)):
            cf_dir = os.path.join(DOCS_DIR, cf)
            if not os.path.isdir(cf_dir):
                continue
            for fname in sorted(os.listdir(cf_dir)):
                if not fname.endswith(".txt"):
                    continue
                key = f"{cf}/{fname}"
                text = open(os.path.join(cf_dir, fname), encoding="utf-8", errors="replace").read()
                snippet = summaries.get(key) or " ".join(text.split())[:400]
                title = fname.rsplit("_", 1)[0].replace("_", " ")
                entries.append({
                    "type": "document",
                    "cfnumber": cf,
                    "title": f"{cf} — {title}",
                    "snippet": snippet,
                    "link": f"documents/{cf}/{fname}",
                    "ai_summary": key in summaries,
                    "public_comment": is_public_comment(title),
                })

    # 3. Cached meeting transcripts
    if os.path.isdir(TRANSCRIPT_DIR):
        for fname in sorted(os.listdir(TRANSCRIPT_DIR)):
            if not fname.endswith(".json"):
                continue
            meeting_id = fname.replace(".json", "")
            cached = json.load(open(os.path.join(TRANSCRIPT_DIR, fname)))
            segs = cached["segments"] if isinstance(cached, dict) else cached
            if not segs:
                continue
            snippet = " ".join(s["text"] for s in segs[:20])
            entries.append({
                "type": "transcript",
                "cfnumber": None,
                "title": f"Meeting {meeting_id} transcript ({len(segs)} segments)",
                "snippet": snippet,
                "link": f"transcripts/{fname}",
            })

    return entries


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8">
<title>LA Council Tracker — Search Index</title>
<style>
body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 2em auto; padding: 0 1em; color: #222; }}
input {{ width: 100%; padding: 0.6em; font-size: 1.1em; box-sizing: border-box; margin-bottom: 1em; }}
.entry {{ border-bottom: 1px solid #ddd; padding: 0.8em 0; }}
.entry .type {{ font-size: 0.75em; text-transform: uppercase; color: #888; }}
.entry .title {{ font-weight: 600; }}
.entry .snippet {{ color: #444; font-size: 0.92em; }}
.entry a {{ color: #1a5cb0; text-decoration: none; }}
.badge {{ background: #e0e8ff; color: #234; border-radius: 3px; padding: 0 5px; font-size: 0.75em; margin-left: 0.5em; }}
#count {{ color: #666; margin-bottom: 1em; }}
</style></head>
<body>
<h1>LA Council Tracker — Search Index</h1>
<input id="q" type="text" placeholder="Search council files, documents, transcripts...">
<div id="count"></div>
<div id="results"></div>
<script>
const DATA = {data_json};

const q = document.getElementById('q');
const results = document.getElementById('results');
const count = document.getElementById('count');

function render(filtered) {{
  count.textContent = filtered.length + ' result(s)';
  results.innerHTML = filtered.slice(0, 200).map(e => `
    <div class="entry">
      <div class="type">${{e.type}}${{e.ai_summary ? '<span class="badge">AI summary</span>' : ''}}${{e.public_comment ? '<span class="badge" style="background:#ffe0b0;">Public comment</span>' : ''}}</div>
      <div class="title"><a href="${{e.link}}" target="_blank">${{e.title}}</a></div>
      <div class="snippet">${{e.snippet}}</div>
    </div>
  `).join('');
}}

function search() {{
  const term = q.value.toLowerCase().trim();
  if (!term) {{ render(DATA); return; }}
  const filtered = DATA.filter(e =>
    (e.title && e.title.toLowerCase().includes(term)) ||
    (e.snippet && e.snippet.toLowerCase().includes(term))
  );
  render(filtered);
}}

q.addEventListener('input', search);
render(DATA);
</script>
</body></html>
"""


def main():
    entries = build_entries()
    data_json = json.dumps(entries)
    html_out = TEMPLATE.format(data_json=data_json)
    with open(OUT_PATH, "w") as f:
        f.write(html_out)
    print(f"Wrote {len(entries)} entries to {OUT_PATH}")


if __name__ == "__main__":
    main()
