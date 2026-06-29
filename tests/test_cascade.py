"""Cascade test suite. Run with: pytest -q"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from cascade import search
from cascade.clients import make_client, DownloadClientError
from cascade.clients.base import Transfer, TransferFile, AddResult


# ---------------- search / badges ----------------
def test_human_size():
    assert search.human_size(0) == "0.0 B"
    assert search.human_size(1024) == "1.0 KB"
    assert search.human_size(int(1.5 * 1024**3)) == "1.5 GB"


@pytest.mark.parametrize("title,res,src,ext", [
    ("Dune 2024 2160p BluRay x265.mkv", "2160p", "BluRay", "MKV"),
    ("Show S01E01 1080p WEB-DL", "1080p", "WEB-DL", None),
    ("Movie 720p HDTV XviD.avi", "720p", "HDTV", "AVI"),
    ("Plain release name", None, None, None),
])
def test_badges(title, res, src, ext):
    b = search.parse_badges(title)
    assert b["res"] == res and b["source"] == src and b["ext"] == ext


def test_search_requires_key():
    with pytest.raises(search.SearchError):
        search.search("http://x", "", "all", "q", "all", 10)


# ---------------- client factory ----------------
def test_factory_known_clients():
    for kind in ("transmission", "qbittorrent", "deluge"):
        c = make_client(kind, "http://localhost", "u", "p")
        assert c.name == kind


def test_factory_unknown():
    with pytest.raises(DownloadClientError):
        make_client("notaclient", "http://x")


# ---------------- transmission parsing (no network) ----------------
def test_transmission_transfer_mapping(monkeypatch):
    from cascade.clients.transmission import TransmissionClient
    c = TransmissionClient("http://x")
    monkeypatch.setattr(c, "_rpc", lambda m, a: {"torrents": [
        {"id": 1, "name": "T", "percentDone": 0.5, "rateDownload": 1000,
         "rateUpload": 0, "status": 4, "eta": 60, "uploadRatio": 0.5,
         "totalSize": 2000, "errorString": ""}]})
    xs = c.list_transfers()
    assert len(xs) == 1
    t = xs[0]
    assert t.percent == 50.0 and t.status == "downloading" and not t.done


def test_transmission_done_flag():
    t = Transfer("1", "x", 100.0, 0, 0, "seeding", -1, 2.0, 100)
    assert t.done


# ---------------- app endpoints with mocked client ----------------
@pytest.fixture
def client_app(monkeypatch):
    os.environ["JACKETT_API_KEY"] = "test"
    from cascade import app as appmod

    class Mock:
        name = "transmission"
        def test(self): return True
        def add(self, m, d=None): return AddResult(id="1", name="Test")
        def list_transfers(self):
            return [Transfer("1", "Dune", 45.0, 5_000_000, 0, "downloading", 600, 0.0, 4_000_000_000)]
        def files(self, i):
            return [TransferFile("dune.mkv", "Dune/dune.mkv", 4_000_000_000, 45.0, True)]
        def pause(self, i): pass
        def resume(self, i): pass
        def remove(self, i, delete_data=False): pass

    monkeypatch.setattr(appmod, "client", lambda: Mock())
    monkeypatch.setattr(appmod.searchmod, "search",
                        lambda *a, **k: [{"title": "Dune 1080p", "href": "magnet:x",
                                          "is_magnet": True, "seeders": 400, "peers": 9,
                                          "size": 4_000_000_000, "size_h": "3.7 GB",
                                          "tracker": "t", "badges": {"res": "1080p", "source": None, "ext": None}}])
    from fastapi.testclient import TestClient
    return TestClient(appmod.app)


def test_api_search(client_app):
    r = client_app.get("/api/search?q=dune")
    assert r.status_code == 200 and r.json()["total"] == 1


def test_api_add(client_app):
    r = client_app.post("/api/add", json={"magnet": "magnet:x"})
    assert r.status_code == 200 and r.json()["name"] == "Test"


def test_api_add_empty(client_app):
    assert client_app.post("/api/add", json={"magnet": ""}).status_code == 400


def test_api_transfers(client_app):
    r = client_app.get("/api/transfers")
    assert r.json()["transfers"][0]["percent"] == 45.0


def test_api_files(client_app):
    assert client_app.get("/api/torrent/1/files").json()["files"][0]["name"] == "dune.mkv"


def test_api_action(client_app):
    assert client_app.post("/api/torrent/1", json={"action": "pause"}).json()["action"] == "pause"


def test_api_action_bad(client_app):
    assert client_app.post("/api/torrent/1", json={"action": "nope"}).status_code == 400


def test_api_config(client_app):
    cfg = client_app.get("/api/config").json()
    assert cfg["title"] and "accent" in cfg


# ---------------- database ----------------
def test_db_settings_and_history(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_FILE", str(tmp_path / "events.jsonl"))
    import importlib
    from cascade import config as cfgmod
    importlib.reload(cfgmod)
    from cascade import db
    importlib.reload(db)
    db.init()
    db.set_setting("k", {"a": 1})
    assert db.get_setting("k") == {"a": 1}
    db.add_history("completed", "X", "sorted", 1000)
    assert len(db.recent_history()) == 1
    s = db.history_stats()
    assert s["completed_count"] == 1 and s["completed_bytes"] == 1000


def test_db_default_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_FILE", str(tmp_path / "events.jsonl"))
    import importlib
    from cascade import config as cfgmod
    importlib.reload(cfgmod)
    from cascade import db
    importlib.reload(db)
    db.init()
    with db.connect() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()["n"]
    assert n >= 1


# ---------------- tmdb ----------------
def test_tmdb_disabled_without_key(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    import importlib
    from cascade import config as cfgmod
    importlib.reload(cfgmod)
    from cascade import db
    importlib.reload(db)
    db.init()
    from cascade import tmdb
    importlib.reload(tmdb)
    assert not tmdb.enabled()
    assert tmdb.search("dune") == []


def test_tmdb_parse_and_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENTS_FILE", str(tmp_path / "events.jsonl"))
    import importlib
    from cascade import config as cfgmod
    importlib.reload(cfgmod)
    from cascade import db
    importlib.reload(db)
    db.init()
    from cascade import tmdb
    importlib.reload(tmdb)
    db.set_setting("tmdb_key", "k")
    tmdb._get = lambda path, params: {"results": [
        {"id": 1, "media_type": "movie", "title": "Dune", "release_date": "2021-01-01",
         "poster_path": "/p.jpg", "vote_average": 8.0},
        {"id": 2, "media_type": "person", "name": "x"}]}
    res = tmdb.search("dune")
    assert len(res) == 1 and res[0]["year"] == "2021"
    assert res[0]["search_query"] == "Dune 2021"


# ---------------- setup wizard ----------------
def test_config_save_reload(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_CONFIG_FILE", str(tmp_path / "cascade.env"))
    # other tests may have set these in the process env; clear for isolation
    for k in ("JACKETT_API_KEY", "CLIENT_URL", "DOWNLOAD_CLIENT", "UI_ACCENT"):
        monkeypatch.delenv(k, raising=False)
    import importlib
    from cascade import config as cfgmod
    importlib.reload(cfgmod)
    assert not cfgmod.config.configured()
    cfgmod.save({"JACKETT_API_KEY": "k", "CLIENT_URL": "http://c",
                 "DOWNLOAD_CLIENT": "deluge", "UI_ACCENT": "rose"})
    assert cfgmod.config.configured()
    assert cfgmod.config.client_kind == "deluge"
    assert cfgmod.config.ui_accent == "rose"


def test_config_save_whitelist(tmp_path, monkeypatch):
    monkeypatch.setenv("CASCADE_CONFIG_FILE", str(tmp_path / "cascade.env"))
    import importlib
    from cascade import config as cfgmod
    importlib.reload(cfgmod)
    cfgmod.save({"EVIL": "x", "JACKETT_INDEXER": "1337x"})
    body = (tmp_path / "cascade.env").read_text()
    assert "EVIL" not in body
    assert "JACKETT_INDEXER=1337x" in body


# ---------------- content classification ----------------
def test_classify_game_by_platform():
    from cascade.classify import classify, dest_folder
    r = classify("Lego Harry Potter Years 1-4 PS3", 1000)
    assert r["type"] == "game" and r["platform"] == "PS3"
    assert dest_folder("game") == "games"


def test_classify_movie_and_tv_by_category():
    from cascade.classify import classify
    assert classify("Dune 2024 1080p BluRay", 2000)["type"] == "movie"
    assert classify("The Office S03E07", 5000)["type"] == "tv"


def test_classify_game_by_scene_group_no_category():
    from cascade.classify import classify
    r = classify("Cyberpunk 2077 v2.1 REPACK FitGirl", None)
    assert r["type"] == "game"


def test_classify_switch_and_console():
    from cascade.classify import classify
    assert classify("Super Mario Odyssey NSW", None)["platform"] == "Nintendo Switch"
    assert classify("Elden Ring PS5", None)["type"] == "game"
