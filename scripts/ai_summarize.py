#!/usr/bin/env python3
"""One-paragraph AI summaries of newly-archived documents, via the local
Ollama instance already set up for the Morris Kight project (no new
dependency, no external API calls)."""
import json
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:7b-instruct-q5_K_M"
MAX_INPUT_CHARS = 6000

PROMPT_TEMPLATE = """You are summarizing a document attached to a Los Angeles City Council file, for someone tracking the file's progress. Write ONE concise paragraph (3-5 sentences) covering: what kind of document this is, its substantive content (dollar amounts, specific actions/instructions, names of people or organizations involved), and why it matters to the file's progress. Do not pad with generic phrases like "this document discusses" -- lead with the substance.

Document title: {title}

Document text:
{text}

Summary:"""


def summarize(title, text):
    if not text or not text.strip():
        return None
    truncated = text[:MAX_INPUT_CHARS]
    prompt = PROMPT_TEMPLATE.format(title=title, text=truncated)

    payload = json.dumps({
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2},
    }).encode()

    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
        return result.get("response", "").strip()
    except Exception as e:
        return None


if __name__ == "__main__":
    import sys
    print(summarize(sys.argv[1], open(sys.argv[2]).read()))
