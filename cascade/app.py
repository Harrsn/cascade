"""Cascade — self-hosted search → download → sort → manage for media.

One lightweight app over your indexer (Jackett/Prowlarr) and torrent client
(Transmission/qBittorrent/Deluge). This module exposes the HTTP API and serves
the single-page UI.
"""
from __future__ import annotations

import json
import shutil
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import config
from .clients import make_client, DownloadClientError
from . import search as searchmod
from .notify import notify

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cascade")

app = FastAPI(title=config.app_title)
STATIC = Path(__file__).parent / "static"

GB = 1024 ** 3


def client():
    """Build the configured download client per request (cheap, stateless)."""
    return make_client(config.client_kind, config.client_url,
                       config.client_user, config.client_pass, config.request_timeout)


# ----------------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------------
class AddRequest(BaseModel):
    magnet: str
    title: str | None = None


class TorrentAction(BaseModel):
    action: str


# ----------------------------------------------------------------------------
# Search
# ----------------------------------------------------------------------------
@app.get("/api/search")
def api_search(q: str = Query(..., min_length=1), cat: str = Query("all"),
               limit: int = Query(None)):
    lim = limit or config.search_limit
    try:
        results = searchmod.search(config.jackett_url, config.jackett_api_key,
                                   config.jackett_indexer, q, cat, lim,
                                   config.request_timeout)
    except searchmod.SearchError as e:
        raise HTTPException(502, str(e))
    return {"query": q, "category": cat, "total": len(results), "results": results}


@app.post("/api/add")
def api_add(req: AddRequest):
    if not req.magnet:
        raise HTTPException(400, "No magnet/href provided.")
    try:
        res = client().add(req.magnet, config.download_dir or None)
    except DownloadClientError as e:
        raise HTTPException(502, str(e))
    log.info("Added: %s (id=%s, dup=%s)", res.name, res.id, res.duplicate)
    return {"status": "ok", "id": res.id, "name": res.name, "duplicate": res.duplicate}


# ----------------------------------------------------------------------------
# Transfers + controls
# ----------------------------------------------------------------------------
def _fmt_eta(eta: int) -> str:
    if eta is None or eta < 0:
        return "—"
    if eta < 60:
        return f"{eta}s"
    if eta < 3600:
        return f"{eta // 60}m"
    if eta < 86400:
        return f"{eta // 3600}h {(eta % 3600) // 60}m"
    return f"{eta // 86400}d"


@app.get("/api/transfers")
def api_transfers():
    try:
        xs = client().list_transfers()
    except DownloadClientError as e:
        raise HTTPException(502, str(e))
    out = []
    for t in xs:
        out.append({
            "id": t.id, "name": t.name, "percent": t.percent,
            "down_h": searchmod.human_size(t.down_rate) + "/s",
            "status": t.status, "eta_h": _fmt_eta(t.eta), "ratio": t.ratio,
            "size_h": searchmod.human_size(t.size), "error": t.error, "done": t.done,
        })
    out.sort(key=lambda x: (x["done"], -x["percent"]))
    return {"transfers": out}


@app.post("/api/torrent/{tid}")
def api_torrent_action(tid: str, req: TorrentAction):
    c = client()
    try:
        if req.action == "pause":
            c.pause(tid)
        elif req.action == "resume":
            c.resume(tid)
        elif req.action == "remove":
            c.remove(tid, delete_data=False)
        elif req.action == "remove-data":
            c.remove(tid, delete_data=True)
        else:
            raise HTTPException(400, f"Unknown action: {req.action}")
    except DownloadClientError as e:
        raise HTTPException(502, str(e))
    log.info("Torrent %s: %s", tid, req.action)
    return {"status": "ok", "id": tid, "action": req.action}


@app.get("/api/torrent/{tid}/files")
def api_torrent_files(tid: str):
    try:
        files = client().files(tid)
    except DownloadClientError as e:
        raise HTTPException(502, str(e))
    return {"id": tid, "files": [{
        "name": f.name, "path": f.path, "size_h": searchmod.human_size(f.size),
        "percent": f.percent, "wanted": f.wanted} for f in files]}


# ----------------------------------------------------------------------------
# Stats + events
# ----------------------------------------------------------------------------
@app.get("/api/stats")
def api_stats():
    out = {"disk": None, "down_total": 0, "up_total": 0,
           "downloading": 0, "seeding": 0, "total": 0}
    try:
        du = shutil.disk_usage(config.disk_path)
        out["disk"] = {"free": du.free, "total": du.total,
                       "free_h": searchmod.human_size(du.free),
                       "total_h": searchmod.human_size(du.total),
                       "pct_used": round(du.used / du.total * 100, 1) if du.total else 0}
    except OSError:
        pass
    try:
        for t in client().list_transfers():
            out["down_total"] += t.down_rate
            out["up_total"] += t.up_rate
            out["total"] += 1
            if t.status == "downloading":
                out["downloading"] += 1
            elif t.status == "seeding":
                out["seeding"] += 1
    except DownloadClientError:
        pass
    out["down_total_h"] = searchmod.human_size(out["down_total"]) + "/s"
    out["up_total_h"] = searchmod.human_size(out["up_total"]) + "/s"
    return out


@app.get("/api/events")
def api_events(limit: int = Query(60, ge=1, le=300)):
    p = Path(config.events_file)
    if not p.exists():
        return {"events": []}
    try:
        lines = p.read_text(errors="ignore").strip().splitlines()
    except OSError:
        return {"events": []}
    events = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except ValueError:
            events.append({"ts": "", "event": "raw", "msg": line})
    events.reverse()
    return {"events": events}


# ----------------------------------------------------------------------------
# Config / health / setup
# ----------------------------------------------------------------------------
@app.get("/api/config")
def api_config():
    """Non-secret config the UI needs (theme, title, thresholds, client kind)."""
    return {"title": config.app_title, "theme": config.ui_theme,
            "accent": config.ui_accent, "client": config.client_kind,
            "big_download_gb": config.big_download_gb,
            "configured": config.configured()}


@app.get("/health")
def health():
    status = {"indexer": "unknown", "client": "unknown"}
    try:
        import requests
        requests.get(f"{config.jackett_url}/", timeout=5)
        status["indexer"] = "reachable"
    except Exception:
        status["indexer"] = "unreachable"
    try:
        client().test()
        status["client"] = "reachable"
    except Exception as e:
        status["client"] = f"error: {e}"
    return status


@app.get("/")
def index():
    f = STATIC / "index.html"
    if f.exists():
        return FileResponse(f)
    raise HTTPException(404, "UI not installed.")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
