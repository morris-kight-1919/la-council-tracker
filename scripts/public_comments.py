#!/usr/bin/env python3
"""List every archived 'Communication(s) from Public' document across the
whole tracker -- these often surface named individuals/organizations before
anywhere else in a file's official record, so they're worth scanning as a
group rather than only encountering them one at a time in the changelog."""
import os

from documents import is_public_comment

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DOCS_DIR = os.path.join(ROOT, "documents")


def main():
    found = []
    for cf in sorted(os.listdir(DOCS_DIR)):
        cf_dir = os.path.join(DOCS_DIR, cf)
        if not os.path.isdir(cf_dir):
            continue
        for fname in sorted(os.listdir(cf_dir)):
            if not fname.endswith(".txt"):
                continue
            title = fname.rsplit("_", 1)[0].replace("_", " ")
            if not is_public_comment(title):
                continue
            path = os.path.join(cf_dir, fname)
            text = open(path, encoding="utf-8", errors="replace").read()
            excerpt = " ".join(text.split())[:300]
            found.append((cf, fname, excerpt))

    if not found:
        print("No public-comment documents archived yet.")
        return

    print(f"{len(found)} public-comment document(s) archived:\n")
    for cf, fname, excerpt in found:
        print(f"[{cf}] {fname}")
        print(f"  {excerpt}...\n")


if __name__ == "__main__":
    main()
