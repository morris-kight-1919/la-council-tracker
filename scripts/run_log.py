#!/usr/bin/env python3
"""Shared run-logging utility. Every script that runs unattended logs here,
including no-ops -- per City Watch.md Section I/V/IX: the log of every run
(with exit status and counts) is the only evidence the pipeline is alive,
and the health check reads this rather than recomputing anything."""
import contextlib
import datetime
import json
import os
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
RUN_LOG_PATH = os.path.join(ROOT, "run_log.jsonl")


def log_run(script, status, **counts):
    entry = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "script": script,
        "status": status,  # "ok" | "error"
        **counts,
    }
    with open(RUN_LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


@contextlib.contextmanager
def logged_run(script, **initial_counts):
    """Usage: with logged_run("check_updates") as counts:
                  counts["files_checked"] = 5
    Logs 'ok' with whatever counts were set on exit, or 'error' with the
    exception message if the block raises -- so a crash still produces a
    log line instead of silent nothing, per Section V: 'a job that dies at
    startup produces no output whatsoever, indistinguishable from a quiet day.'
    """
    counts = dict(initial_counts)
    try:
        yield counts
        log_run(script, "ok", **counts)
    except Exception as e:
        log_run(script, "error", error=str(e), traceback=traceback.format_exc()[-2000:], **counts)
        raise


def read_recent(limit=200):
    if not os.path.exists(RUN_LOG_PATH):
        return []
    lines = open(RUN_LOG_PATH).readlines()[-limit:]
    return [json.loads(l) for l in lines if l.strip()]


if __name__ == "__main__":
    for entry in read_recent(20):
        print(entry)
