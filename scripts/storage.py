#!/usr/bin/env python3
"""Where data lives. One switch, one place.

Default is internal disk (no config needed, nothing breaks). Set a
`data_root` in config.json and everything moves to that volume instead.

    config.json:
        "data_root": "/Volumes/Archive/la-council-tracker"     # external
        "data_root": null                                       # internal (default)

Or per-run:  LA_TRACKER_DATA_ROOT=/Volumes/... python3 check_updates.py

TWO GUARDS, both of which exist because the failure modes are silent:

1. MOUNT GUARD. If data_root is configured but the volume isn't mounted,
   os.makedirs() would cheerfully rebuild the whole tree on the internal
   disk. You'd then have two partial archives, a health check reporting
   green, and cumulative silent corruption. So: if a data_root is set and
   the sentinel file isn't there, we refuse to run rather than guess.

2. NETWORK-FILESYSTEM GUARD. corpus.db must not live on a LAN share.
   SQLite's locking depends on POSIX advisory locks, which are unreliable
   over SMB/NFS, and WAL mode (which this schema uses) does not work over a
   network filesystem at all -- it needs shared memory that doesn't cross a
   network boundary. Bulk files over LAN are fine; the database is not.
   We detect network mounts and refuse to put the DB there.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_ROOT = os.path.abspath(os.path.join(HERE, ".."))
CONFIG_PATH = os.path.join(CODE_ROOT, "config.json")

SENTINEL_NAME = ".tracker_volume_id"
NETWORK_FSTYPES = {"smbfs", "afpfs", "nfs", "cifs", "webdav", "ftp"}

# Directories/files that make up the data set (everything except code).
DATA_ENTRIES = ["documents", "transcripts", "data", "corpus.db",
                "watchlist.json", "CHANGELOG.md", "run_log.jsonl", "index.html"]


class StorageNotReady(RuntimeError):
    pass


def _config():
    if os.path.exists(CONFIG_PATH):
        try:
            return json.load(open(CONFIG_PATH))
        except json.JSONDecodeError:
            return {}
    return {}


def configured_root():
    """(path, source) or (None, None) when running on internal disk."""
    env = os.environ.get("LA_TRACKER_DATA_ROOT")
    if env:
        return env, "LA_TRACKER_DATA_ROOT env var"
    root = _config().get("data_root")
    if root:
        return root, "config.json data_root"
    return None, None


def filesystem_type(path):
    """macOS: fstype of the volume containing path, via `mount`."""
    try:
        target = os.path.realpath(path)
        out = subprocess.run(["mount"], capture_output=True, text=True, timeout=10).stdout
        best, best_type = "", None
        for line in out.splitlines():
            # "  /dev/disk2s2 on /Volumes/X (apfs, local, ...)"
            if " on " not in line or "(" not in line:
                continue
            mount_point = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
            fstype = line.rsplit("(", 1)[1].split(",", 1)[0].strip(") ")
            if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
                if len(mount_point) > len(best):
                    best, best_type = mount_point, fstype
        return best_type
    except Exception:
        return None


def is_network_volume(path):
    return (filesystem_type(path) or "").lower() in NETWORK_FSTYPES


def data_root(require_local_db=False):
    """Resolved data root. Falls back to internal disk when unconfigured."""
    root, source = configured_root()
    if not root:
        return CODE_ROOT

    if not os.path.isdir(root):
        raise StorageNotReady(
            f"data_root does not exist: {root}\n  (set via {source})\n"
            "  The external drive is probably not mounted. Refusing to run --\n"
            "  rebuilding this tree on the internal disk would create a second,\n"
            "  partial archive that looks healthy and is not.")

    if not os.path.exists(os.path.join(root, SENTINEL_NAME)):
        raise StorageNotReady(
            f"data_root exists but has no {SENTINEL_NAME}: {root}\n"
            "  Usually means the drive is unmounted and an empty directory is\n"
            "  standing in for it, or this is the wrong volume.\n"
            "  If it really is a fresh data volume:  python3 scripts/storage.py --init")

    if require_local_db and is_network_volume(root):
        raise StorageNotReady(
            f"data_root is on a network filesystem ({filesystem_type(root)}): {root}\n"
            "  SQLite must not live on a LAN share: POSIX advisory locks are\n"
            "  unreliable over SMB/NFS and WAL mode does not work there at all.\n"
            "  Keep corpus.db on local/attached storage; bulk files over LAN are fine.")

    return root


def path(*parts, require_local_db=False):
    full = os.path.join(data_root(require_local_db=require_local_db), *parts)
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return full


def subdir(name):
    full = os.path.join(data_root(), name)
    os.makedirs(full, exist_ok=True)
    return full


def db_path():
    """corpus.db -- always guarded against network volumes."""
    return os.path.join(data_root(require_local_db=True), "corpus.db")


def free_space_gb(p=None):
    st = os.statvfs(p or data_root())
    return (st.f_bavail * st.f_frsize) / (1024 ** 3)


def describe():
    root, source = configured_root()
    lines = []
    if not root:
        lines.append(f"Storage: INTERNAL (default) — {CODE_ROOT}")
        lines.append(f"  free: {free_space_gb(CODE_ROOT):.1f} GB")
        lines.append("  No data_root configured. Set one in config.json to move data external.")
    else:
        lines.append(f"Storage: EXTERNAL — {root}")
        lines.append(f"  configured via {source}")
        mounted = os.path.isdir(root)
        lines.append(f"  mounted: {'yes' if mounted else 'NO — scripts will refuse to run'}")
        if mounted:
            fs = filesystem_type(root)
            lines.append(f"  filesystem: {fs}"
                         + ("  ** NETWORK — corpus.db cannot live here **"
                            if (fs or "").lower() in NETWORK_FSTYPES else ""))
            lines.append(f"  sentinel: {'present' if os.path.exists(os.path.join(root, SENTINEL_NAME)) else 'MISSING'}")
            lines.append(f"  free: {free_space_gb(root):.1f} GB")
    return "\n".join(lines)


if __name__ == "__main__":
    if "--init" in sys.argv:
        root, source = configured_root()
        if not root:
            print("No data_root set in config.json — nothing to initialise.", file=sys.stderr)
            sys.exit(2)
        os.makedirs(root, exist_ok=True)
        sentinel = os.path.join(root, SENTINEL_NAME)
        if not os.path.exists(sentinel):
            json.dump({"purpose": "la-council-tracker data volume"}, open(sentinel, "w"), indent=1)
        print(f"Initialised {root}")
        print(describe())
    else:
        try:
            print(describe())
        except StorageNotReady as e:
            print(f"STORAGE NOT READY\n{e}", file=sys.stderr)
            sys.exit(2)
