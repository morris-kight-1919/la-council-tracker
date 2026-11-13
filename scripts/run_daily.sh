#!/bin/bash
# Daily pipeline: discover new files, ingest City mail (ENS), fetch linked
# documents, check every watched file for changes, scan upcoming agendas,
# refresh scoring, sweep old PDFs, and report health. Safe to run manually
# or on a schedule -- every step here is idempotent/resumable.
#
# This used to run only discover.py + check_updates.py, from before most of
# the pipeline existed -- so everything built since (ENS ingestion, scoring,
# upcoming-agenda alerts, the retention sweep) only ever ran when someone
# typed each command by hand, and there was no LaunchAgent or crontab
# running even that (forensic audit finding #1, 2026-08-08). This script,
# wired to com.morriskight.la-council-tracker.daily.plist, is the fix.
#
# Deliberately /bin/bash, not /bin/zsh -- City Watch.md Section V: Full Disk
# Access is granted per binary, and their scheduled jobs failed under zsh
# while working under bash.
#
# send_digest.py runs dry-run only here (no --send): the AppleMail send path
# has a known formatting bug (forensic audit finding #3, unresolved as of
# 2026-08-08). Add --send once that's fixed.
#
# No `set -e`: one script failing (a network hiccup, a site change) must not
# stop the rest of the pipeline from running -- these are independent steps.
#
# PYTHONUNBUFFERED: under launchd, stdout is a redirected file, not a TTY,
# so Python fully buffers it by default -- a run's progress becomes
# invisible until the whole process exits (confirmed: an unattended run
# showed almost no incremental log output for ~5 minutes, then flushed it
# all at once on completion). If a scheduled run ever hangs or crashes
# mid-way, that buffering would hide exactly how far it got.
export PYTHONUNBUFFERED=1
# launchd's default PATH (/usr/bin:/bin:/usr/sbin:/sbin) doesn't include
# /usr/local/bin, where pdftotext lives -- confirmed live: check_updates.py
# crashed with FileNotFoundError on every scheduled run's document-extract
# step (stderr log, 2026-08-10) while working fine when run by hand from an
# interactive shell that already has /usr/local/bin on PATH.
export PATH="/usr/local/bin:$PATH"
set -uo pipefail
cd "$(dirname "$0")"
echo "=== $(date) ==="

echo "--- discover ---"
python3 discover.py
echo

echo "--- applemail_ingest ---"
python3 applemail_ingest.py --from lacity.org
echo

ENS_BACKLOG="/Users/morriskight/Public/LANDrop/ens-backlog"
if [ -d "$ENS_BACKLOG" ]; then
    echo "--- eml_ingest (backlog) ---"
    python3 eml_ingest.py "$ENS_BACKLOG"
    echo
fi

echo "--- fetch_ens_links ---"
python3 fetch_ens_links.py
echo

echo "--- check_updates ---"
python3 check_updates.py
echo

echo "--- upcoming_agendas ---"
python3 upcoming_agendas.py
echo

echo "--- extract_entities --reextract ---"
python3 extract_entities.py --reextract
echo

echo "--- score_corpus ---"
python3 score_corpus.py
echo

echo "--- queue_relevance ---"
python3 queue_relevance.py
echo

echo "--- vote_patterns ---"
python3 vote_patterns.py
echo

echo "--- doc_retention ---"
python3 doc_retention.py --delete
echo

echo "--- health_check ---"
python3 health_check.py
echo

echo "--- send_digest (dry-run) ---"
python3 send_digest.py
echo

echo "=== done $(date) ==="
