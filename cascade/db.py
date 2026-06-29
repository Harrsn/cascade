"""Persistence — a single SQLite file under /config.

Cascade was stateless (live reads from the client). Features like TMDb caching,
saved settings, requests, RSS subscriptions, quality profiles, and history all
need durable state, so this module owns a lightweight SQLite database. SQLite
keeps the self-hosted story simple: one file, no extra container, easy backup.

Connections are opened per-call (SQLite handles this fine for our load) with
WAL mode for concurrent reads. Schema is created/migrated idempotently on init.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import config

_lock = threading.Lock()
_initialized = False


def _db_path() -> Path:
    # store next to the config/events files
    return Path(config.events_file).parent / "cascade.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS history (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    event     TEXT NOT NULL,          -- added | completed | sorted | removed | failed
    title     TEXT,
    detail    TEXT,
    size      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    user        TEXT,                 -- email/identity from auth header, if any
    media_type  TEXT,                 -- movie | tv
    tmdb_id     INTEGER,
    title       TEXT,
    year        INTEGER,
    poster      TEXT,
    status      TEXT DEFAULT 'pending', -- pending | approved | declined | fulfilled
    note        TEXT
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    title       TEXT NOT NULL,
    media_type  TEXT DEFAULT 'tv',
    query       TEXT,                 -- search query used against indexers
    profile_id  INTEGER,
    enabled     INTEGER DEFAULT 1,
    last_check  TEXT,
    last_grab   TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    min_seeders INTEGER DEFAULT 1,
    resolutions TEXT,                 -- JSON list, preference order e.g. ["1080p","720p"]
    sources     TEXT,                 -- JSON list e.g. ["WEB-DL","BluRay"]
    max_size_gb REAL DEFAULT 0,       -- 0 = no cap
    min_size_gb REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS grabbed (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    sub_id      INTEGER,
    title       TEXT,                 -- release title we grabbed (dedupe key)
    UNIQUE(title)
);
"""


def init() -> None:
    global _initialized
    with _lock:
        if _initialized:
            return
        with connect() as c:
            c.executescript(SCHEMA)
            # seed a sensible default quality profile if none exist
            n = c.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
            if n == 0:
                c.execute(
                    "INSERT INTO profiles (name, min_seeders, resolutions, sources, max_size_gb) "
                    "VALUES (?,?,?,?,?)",
                    ("Default", 3, json.dumps(["1080p", "720p"]),
                     json.dumps(["WEB-DL", "BluRay", "WEBRip"]), 8.0))
        _initialized = True


# ---- generic settings KV (the in-app settings editor uses this) ----
def get_setting(key: str, default: Any = None) -> Any:
    with connect() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (ValueError, TypeError):
        return row["value"]


def set_setting(key: str, value: Any) -> None:
    with connect() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, json.dumps(value)))


def all_settings() -> dict:
    with connect() as c:
        rows = c.execute("SELECT key, value FROM settings").fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except (ValueError, TypeError):
            out[r["key"]] = r["value"]
    return out


# ---- history ----
def add_history(event: str, title: str = "", detail: str = "", size: int = 0) -> None:
    from datetime import datetime
    with connect() as c:
        c.execute("INSERT INTO history (ts, event, title, detail, size) VALUES (?,?,?,?,?)",
                  (datetime.now().isoformat(timespec="seconds"), event, title, detail, size))


def recent_history(limit: int = 100) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def history_stats() -> dict:
    """Aggregate counts + total size for the stats dashboard."""
    with connect() as c:
        by_event = {r["event"]: r["n"] for r in c.execute(
            "SELECT event, COUNT(*) AS n FROM history GROUP BY event").fetchall()}
        total = c.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS s FROM history "
                          "WHERE event='completed'").fetchone()
    return {"by_event": by_event, "completed_count": total["n"],
            "completed_bytes": total["s"]}


# ---- subscriptions (scheduled-search auto-grab) ----
def list_subscriptions(enabled_only: bool = False) -> list[dict]:
    q = "SELECT * FROM subscriptions"
    if enabled_only:
        q += " WHERE enabled=1"
    q += " ORDER BY id"
    with connect() as c:
        return [dict(r) for r in c.execute(q).fetchall()]


def get_subscription(sid: int) -> dict | None:
    with connect() as c:
        r = c.execute("SELECT * FROM subscriptions WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def create_subscription(title: str, query: str, media_type: str = "tv",
                        profile_id: int | None = None) -> int:
    from datetime import datetime
    with connect() as c:
        cur = c.execute(
            "INSERT INTO subscriptions (ts, title, media_type, query, profile_id, enabled) "
            "VALUES (?,?,?,?,?,1)",
            (datetime.now().isoformat(timespec="seconds"), title, media_type, query, profile_id))
        return cur.lastrowid


def update_subscription(sid: int, **fields) -> None:
    allowed = {"title", "query", "media_type", "profile_id", "enabled",
               "last_check", "last_grab"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=?" for k in sets)
    with connect() as c:
        c.execute(f"UPDATE subscriptions SET {cols} WHERE id=?",
                  (*sets.values(), sid))


def delete_subscription(sid: int) -> None:
    with connect() as c:
        c.execute("DELETE FROM subscriptions WHERE id=?", (sid,))


def already_grabbed(title: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM grabbed WHERE title=?", (title,)).fetchone() is not None


def mark_grabbed(title: str, sub_id: int | None = None) -> bool:
    """Record a grabbed release. Returns False if it was already there (the
    UNIQUE(title) constraint dedupes), True if newly inserted."""
    from datetime import datetime
    try:
        with connect() as c:
            c.execute("INSERT INTO grabbed (ts, sub_id, title) VALUES (?,?,?)",
                      (datetime.now().isoformat(timespec="seconds"), sub_id, title))
        return True
    except sqlite3.IntegrityError:
        return False
