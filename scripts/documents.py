#!/usr/bin/env python3
"""Download and extract text from PDFs attached to a council file. Mirrors
the download/extract/OCR-fallback pattern used in the court-docket project."""
import datetime
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DOCS_DIR = os.path.join(ROOT, "documents")

MIN_CHARS_PER_PAGE = 40
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def is_public_comment(title):
    """'Communication(s) from Public' documents -- worth flagging distinctly
    since named individuals/organizations often surface here first, before
    anywhere else in a file's official record."""
    t = (title or "").lower()
    return "communication" in t and "public" in t


def _safe_name(title, date, url):
    base = re.sub(r"[^a-zA-Z0-9]+", "_", title or "doc").strip("_")[:60]
    date_part = re.sub(r"[^0-9]", "", date or "")
    url_hash = hashlib.sha1(url.encode()).hexdigest()[:8]
    return f"{date_part}_{base}_{url_hash}"


def _encode_url(url):
    """City Clerk filenames contain literal spaces and parentheses, e.g.
    '..._misc_amd 2A_her_rod_1-24-25.pdf' and '..._misc_2_9-26-22 (1).pdf'.
    urllib rejects those unencoded. Quote the path/query only -- never the
    scheme or host."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        parts.scheme, parts.netloc,
        urllib.parse.quote(parts.path, safe="/%"),
        urllib.parse.quote(parts.query, safe="=&%"),
        parts.fragment,
    ))


def _download(url, dest):
    if os.path.exists(dest):
        return True
    req = urllib.request.Request(_encode_url(url), headers={"User-Agent": UA})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
            return True
        except Exception:
            time.sleep(3 * (attempt + 1))
    return False


def _extract_text(pdf_path, txt_path):
    result = subprocess.run(["pdftotext", "-layout", pdf_path, txt_path],
                             capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not os.path.exists(txt_path):
        return "", False

    text = open(txt_path, encoding="utf-8", errors="replace").read()
    page_count_result = subprocess.run(
        ["pdfinfo", pdf_path], capture_output=True, text=True, timeout=30)
    pages = 1
    m = re.search(r"Pages:\s*(\d+)", page_count_result.stdout)
    if m:
        pages = max(1, int(m.group(1)))

    if len(text.strip()) / pages < MIN_CHARS_PER_PAGE:
        # try OCR fallback
        ocr_pdf = pdf_path.replace(".pdf", "_ocr.pdf")
        ocr_result = subprocess.run(
            ["ocrmypdf", "--skip-text", "--sidecar", txt_path, pdf_path, ocr_pdf],
            capture_output=True, text=True, timeout=180)
        if ocr_result.returncode == 0 and os.path.exists(txt_path):
            text = open(txt_path, encoding="utf-8", errors="replace").read()
            return text, True
        return text, False

    return text, False


def _record_manifest(cfnumber, name, pdf_path, url, sha256):
    """Hash-and-store-on-arrival record, per City Watch.md Section VII:
    agencies deliver via links that can change; work from a local, hashed
    copy and keep a manifest of what was actually retrieved and when."""
    manifest_path = os.path.join(DOCS_DIR, "manifest.json")
    manifest = {}
    if os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path))
    manifest[f"{cfnumber}/{name}.pdf"] = {
        "source_url": url,
        "sha256": sha256,
        "retrieved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    json.dump(manifest, open(manifest_path, "w"), indent=1)


def download_and_extract(cfnumber, doc):
    """doc: {'title', 'url', 'date'}. Returns dict with local paths + extracted
    text (or None fields if the download/extraction failed)."""
    cf_dir = os.path.join(DOCS_DIR, cfnumber)
    os.makedirs(cf_dir, exist_ok=True)

    name = _safe_name(doc["title"], doc.get("date"), doc["url"])
    pdf_path = os.path.join(cf_dir, f"{name}.pdf")
    txt_path = os.path.join(cf_dir, f"{name}.txt")

    ok = _download(doc["url"], pdf_path)
    if not ok:
        return {"pdf_path": None, "txt_path": None, "text": None, "ocr_used": False,
                "error": "download failed", "retrieved_at": None}

    sha256 = hashlib.sha256(open(pdf_path, "rb").read()).hexdigest()
    _record_manifest(cfnumber, name, pdf_path, doc["url"], sha256)

    text, ocr_used = _extract_text(pdf_path, txt_path)
    return {
        "pdf_path": pdf_path,
        "txt_path": txt_path,
        "text": text,
        "ocr_used": ocr_used,
        "char_count": len(text),
        "sha256": sha256,
        "retrieved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
