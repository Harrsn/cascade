"""Central configuration, loaded once from the environment.

Everything tunable lives here so the rest of the app imports `config` rather
than reading os.environ ad hoc. A .env file (see .env.example) is the intended
way to set these in both bare-metal and Docker deployments.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _list(key: str) -> list[str]:
    raw = os.environ.get(key, "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Config:
    # --- indexer (Jackett/Prowlarr Torznab) ---
    jackett_url: str = os.environ.get("JACKETT_URL", "http://127.0.0.1:9117").rstrip("/")
    jackett_api_key: str = os.environ.get("JACKETT_API_KEY", "")
    jackett_indexer: str = os.environ.get("JACKETT_INDEXER", "all")

    # --- download client ---
    client_kind: str = os.environ.get("DOWNLOAD_CLIENT", "transmission")
    client_url: str = os.environ.get("CLIENT_URL", "http://127.0.0.1:9091/transmission/rpc")
    client_user: str = os.environ.get("CLIENT_USER", "")
    client_pass: str = os.environ.get("CLIENT_PASS", "")
    download_dir: str = os.environ.get("DOWNLOAD_DIR", "")

    # --- paths ---
    disk_path: str = os.environ.get("DISK_PATH", "") or \
        os.environ.get("DOWNLOAD_DIR", "") or "/downloads"
    events_file: str = os.environ.get("EVENTS_FILE", "/config/events.jsonl")

    # --- behavior ---
    request_timeout: int = int(os.environ.get("REQUEST_TIMEOUT", "30"))
    search_limit: int = int(os.environ.get("SEARCH_LIMIT", "150"))
    big_download_gb: int = int(os.environ.get("BIG_DOWNLOAD_GB", "20"))

    # --- notifications ---
    notify_urls: list[str] = field(default_factory=lambda: _list("NOTIFY_URLS"))
    notify_on: list[str] = field(default_factory=lambda: _list("NOTIFY_ON") or
                                 ["completed", "sorted", "failed"])

    # --- UI ---
    ui_theme: str = os.environ.get("UI_THEME", "dark")      # dark | light | auto
    ui_accent: str = os.environ.get("UI_ACCENT", "blue")    # blue | green | purple | amber | rose
    app_title: str = os.environ.get("APP_TITLE", "Cascade")

    def configured(self) -> bool:
        """True when the minimum required settings are present."""
        return bool(self.jackett_api_key and self.client_url)


config = Config()
