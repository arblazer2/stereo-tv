"""Config + XDG paths."""
from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

APP = "stereo-tv"            # config/data dir name (kept stable)
APP_NAME = "Groove Box TV"   # what it calls itself
UA_NAME = "groove-box-tv/0.1"

import sys

if sys.platform == "win32":
    _cfg_base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    _data_base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
elif sys.platform == "darwin":
    _cfg_base = _data_base = Path.home() / "Library/Application Support"
else:
    _cfg_base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    _data_base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CONFIG_DIR = Path(os.environ.get("STEREOTV_CONFIG_DIR", _cfg_base / APP))
DATA_DIR = Path(os.environ.get("STEREOTV_DATA_DIR", _data_base / APP))
CONFIG_FILE = CONFIG_DIR / "config.toml"
DB_FILE = DATA_DIR / "collection.db"
COVERS_DIR = DATA_DIR / "covers"

DEFAULTS: dict[str, Any] = {
    "app": {
        # Public APIs (Discogs, Wikipedia, MusicBrainz, OSM) ask for a way to contact you: a URL or email.
        "contact": "https://github.com/arblazer2/stereo-tv",
    },
    "location": {"name": "", "lat": 0.0, "lon": 0.0, "units": "imperial"},   # set by `python -m stereotv.setup`
    "discogs": {
        "username": "",
        "token_file": str(CONFIG_DIR / "discogs_token"),
        "user_agent": "",          # derived from app.contact when empty
    },
    "display": {
        "width": 640,
        "height": 480,
        "fps": 30,
        "scanlines": True,
        "fullscreen": True,
        "theme": "cable88",
        "scaling": "gpu",        # gpu = SDL scales the canvas, aspect preserved (letterbox); cpu = software scale
        "font_scale": 1.0,
    },
    "dial": {"mode": "keyboard"},
    "now_playing": {"release_id": 0},
    "audio": {"enabled": True, "device": "plughw:CARD=CODEC,DEV=0", "rate": 44100, "channels": 2},
    "identify": {
        "enabled": True,
        "clip_seconds": 10,
        "interval": 60,
        "settle_seconds": 4,
        "silence_rms": 0.004,
        "silence_gap_seconds": 2.5,
        "min_confidence": 0.55,
        "override_minutes": 30,
        "acoustid_api_key": "",
    },
    "web": {"enabled": True, "host": "0.0.0.0", "port": 8080},
    "visualizer": {"gain_db": 0.0},
    "screensaver": {"enabled": True, "idle_seconds": 90, "modes": ["wall", "flying", "weather", "bounce"]},
    "radar": {"enabled": True, "lat": None, "lon": None, "zoom": 7, "frames": 8, "color": 4, "title": "", "map_brightness": 185},
    "remote": {"enabled": True, "port": "auto", "baud": 115200},
    "weather": {"provider": "open-meteo"},          # open-meteo (no key) | homeassistant
    "ha": {"url": "", "weather_entity": "weather.home", "token_file": str(CONFIG_DIR / "ha_token")},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def user_agent(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load()
    ua = cfg["discogs"].get("user_agent") or ""
    if not ua:
        ua = f"{UA_NAME} (+{cfg['app']['contact']})"
    return ua


def load() -> dict[str, Any]:
    cfg = DEFAULTS
    if CONFIG_FILE.exists():
        with CONFIG_FILE.open("rb") as f:
            cfg = _merge(DEFAULTS, tomllib.load(f))
    if not cfg["discogs"].get("user_agent"):
        cfg["discogs"]["user_agent"] = f"{UA_NAME} (+{cfg['app']['contact']})"
    # radar defaults to the configured location
    loc = cfg["location"]
    if cfg["radar"].get("lat") is None:
        cfg["radar"]["lat"] = float(loc.get("lat") or 0.0)
    if cfg["radar"].get("lon") is None:
        cfg["radar"]["lon"] = float(loc.get("lon") or 0.0)
    if not cfg["radar"].get("title"):
        cfg["radar"]["title"] = (loc.get("name") or "LOCAL").upper()
    return cfg


def ensure_dirs() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)


def read_token(cfg: dict[str, Any]) -> str:
    """Read the Discogs personal access token. Never log it."""
    env = os.environ.get("DISCOGS_TOKEN")
    if env:
        return env.strip()
    p = Path(cfg["discogs"]["token_file"]).expanduser()
    if not p.exists():
        raise SystemExit(
            f"No Discogs token. Put your personal access token in {p} "
            "(https://www.discogs.com/settings/developers -> Generate new token) "
            "or set DISCOGS_TOKEN."
        )
    tok = p.read_text().strip()
    if not tok:
        raise SystemExit(f"Discogs token file {p} is empty.")
    return tok
