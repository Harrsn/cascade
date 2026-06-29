"""Monitored movies (Radarr-side).

Mirrors the series module for movies, but movies are simpler — there's no
episode list, just "do we have this movie or not." Add a movie (from TMDb),
the library scanner records movies on disk, and reconcile() marks each monitored
movie as have/wanted by matching normalized title + year. The hunter searches
for wanted movies and grabs the best release per profile.
"""
from __future__ import annotations

import logging
from datetime import datetime

from . import db
from . import tmdb
from . import library

log = logging.getLogger("cascade.movies")


def add_movie(tmdb_id: int, title: str, year: int | None, poster: str | None,
              profile_id: int | None = None) -> int:
    db.init()
    now = datetime.now().isoformat(timespec="seconds")
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO movies (tmdb_id, title, year, poster, profile_id, monitored, added_ts) "
            "VALUES (?,?,?,?,?,1,?) "
            "ON CONFLICT(tmdb_id) DO UPDATE SET monitored=1, profile_id=excluded.profile_id",
            (tmdb_id, title, year, poster, profile_id, now))
        mid = cur.lastrowid
        if not mid:
            mid = c.execute("SELECT id FROM movies WHERE tmdb_id=?", (tmdb_id,)).fetchone()["id"]
    reconcile(mid)
    return mid


def get_movie(movie_id: int) -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT * FROM movies WHERE id=?", (movie_id,)).fetchone()
    return dict(r) if r else None


def list_movies() -> list[dict]:
    with db.connect() as c:
        rows = c.execute("SELECT * FROM movies ORDER BY title").fetchall()
    return [dict(r) for r in rows]


def delete_movie(movie_id: int) -> None:
    with db.connect() as c:
        c.execute("DELETE FROM movies WHERE id=?", (movie_id,))
        c.execute("DELETE FROM wanted WHERE kind='movie' AND series_id=?", (movie_id,))


def reconcile(movie_id: int) -> dict:
    """Mark a monitored movie have/wanted by matching the library (normalized
    title + year). Populates the wanted table for missing movies."""
    m = get_movie(movie_id)
    if not m:
        return {"have": False}
    key = library.normalize_title(m["title"])
    with db.connect() as c:
        lib = c.execute("SELECT title, year, quality FROM library_movies").fetchall()
    owned = None
    for r in lib:
        if library.normalize_title(r["title"]) == key:
            # year match if both known; otherwise title-only is acceptable
            if not m.get("year") or not r["year"] or abs((r["year"] or 0) - m["year"]) <= 1:
                owned = r
                break
    have = owned is not None
    with db.connect() as c:
        c.execute("UPDATE movies SET status=? WHERE id=?",
                  ("have" if have else "wanted", movie_id))
        if have:
            c.execute("DELETE FROM wanted WHERE kind='movie' AND series_id=? AND status='wanted'",
                      (movie_id,))
        else:
            title = f"{m['title']} {m['year']}" if m.get("year") else m["title"]
            exists = c.execute(
                "SELECT 1 FROM wanted WHERE kind='movie' AND series_id=? AND status='wanted'",
                (movie_id,)).fetchone()
            if not exists:
                c.execute(
                    "INSERT INTO wanted (kind, series_id, title, reason, status) "
                    "VALUES ('movie',?,?, 'missing','wanted')",
                    (movie_id, title))
    return {"have": have}


def reconcile_all() -> dict:
    total = {"have": 0, "wanted": 0}
    for m in list_movies():
        if not m.get("monitored"):
            continue
        r = reconcile(m["id"])
        total["have" if r["have"] else "wanted"] += 1
    return total
