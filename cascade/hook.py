#!/usr/bin/env python3
"""Cascade post-process hook.

Invoked by the download client when a torrent completes. It:
  1. runs the sorter to file the media onto your library,
  2. appends an event for the UI feed,
  3. fires notifications,
  4. optionally removes the finished torrent (stops seeding).

It's client-agnostic: the completing client passes the download path and an id
via environment variables. Mappings for each client are below.

  Transmission:  TR_TORRENT_DIR / TR_TORRENT_NAME / TR_TORRENT_ID
  qBittorrent:   pass "%F" (content path) and "%I" (hash) -> CASCADE_PATH / CASCADE_ID
  Deluge:        Execute plugin passes torrentid + name + path -> CASCADE_* (see docs)

Configure via the same .env the app uses.
"""
from __future__ import annotations

import os
import sys
import json
import subprocess
from datetime import datetime
from pathlib import Path

# Make the cascade package importable whether installed or run from source.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cascade.config import config           # noqa: E402
from cascade.notify import notify           # noqa: E402
from cascade.clients import make_client, DownloadClientError  # noqa: E402


def resolve_completion():
    """Return (path, name, tid) from whichever client called us."""
    # Transmission
    td, tn = os.environ.get("TR_TORRENT_DIR"), os.environ.get("TR_TORRENT_NAME")
    if td and tn:
        return os.path.join(td, tn), tn, os.environ.get("TR_TORRENT_ID")
    # Generic (qBittorrent / Deluge / manual) via CASCADE_* vars
    path = os.environ.get("CASCADE_PATH")
    name = os.environ.get("CASCADE_NAME") or (os.path.basename(path) if path else "")
    tid = os.environ.get("CASCADE_ID")
    if path:
        return path, name, tid
    return None, None, None


def write_event(kind: str, name: str, msg: str):
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "event": kind, "name": name, "msg": msg}
    try:
        with open(config.events_file, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def main():
    path, name, tid = resolve_completion()
    if not path:
        print("No completion info in environment; nothing to do.", file=sys.stderr)
        return 0

    write_event("completed", name, "download finished, sorting")
    if "completed" in config.notify_on:
        notify(config.notify_urls, "Download complete", name)

    # 1. sort — delegate to the sorter script, pointed at the completed path
    sorter = Path(__file__).resolve().parent / "sort.py"
    env = dict(os.environ, CASCADE_PATH=path)
    res = subprocess.run([sys.executable, str(sorter)], env=env)
    if res.returncode != 0:
        write_event("sort_failed", name, f"sort failed (rc={res.returncode})")
        if "failed" in config.notify_on:
            notify(config.notify_urls, "Sort failed", name)
        return res.returncode

    write_event("sorted", name, "filed onto library")
    if "sorted" in config.notify_on:
        notify(config.notify_urls, "Sorted to library", name)

    # 2. optional auto-remove
    if os.environ.get("REMOVE_ON_COMPLETE", "0") in ("1", "true", "yes") and tid:
        try:
            make_client(config.client_kind, config.client_url, config.client_user,
                        config.client_pass, config.request_timeout).remove(tid, True)
            write_event("removed", name, "removed from client (auto-cleanup)")
        except DownloadClientError as e:
            write_event("remove_failed", name, f"remove failed: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
