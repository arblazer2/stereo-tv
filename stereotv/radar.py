"""Weather radar frames: RainViewer radar tiles over a cached OpenStreetMap base.

Web-mercator math, tile fetch + disk cache for the base map, in-memory radar
frames. The fetch thread only downloads bytes; decoding/compositing happens on
the main thread (pygame surfaces aren't thread-safe).
"""
from __future__ import annotations

import io
import logging
import math
import threading
import time
from pathlib import Path

import requests

from stereotv import config

log = logging.getLogger("stereotv.radar")

RV_INDEX = "https://api.rainviewer.com/public/weather-maps.json"
OSM_TILE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE = 256
REFRESH_S = 600
from stereotv import config as _cfg
UA = _cfg.user_agent()


def lonlat_to_px(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = TILE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


class Viewport:
    """Pixel window (w×h) centred on lon/lat at zoom z; knows which tiles it needs."""

    def __init__(self, lon: float, lat: float, z: int, w: int, h: int):
        self.z, self.w, self.h = z, w, h
        cx, cy = lonlat_to_px(lon, lat, z)
        self.x0, self.y0 = cx - w / 2, cy - h / 2       # world px of the top-left corner
        n = 2 ** z
        self.tiles = [(tx % n, ty) for tx in range(int(self.x0 // TILE), int((self.x0 + w) // TILE) + 1)
                      for ty in range(int(self.y0 // TILE), int((self.y0 + h) // TILE) + 1)
                      if 0 <= ty < n]

    def tile_offset(self, tx: int, ty: int) -> tuple[int, int]:
        return int(tx * TILE - self.x0), int(ty * TILE - self.y0)

    def project(self, lon: float, lat: float) -> tuple[int, int]:
        x, y = lonlat_to_px(lon, lat, self.z)
        return int(x - self.x0), int(y - self.y0)


class RadarFetcher:
    """Background downloader. .base = {tile: png bytes}; .frames = [(ts, {tile: png bytes})]."""

    def __init__(self, vp: Viewport, color: int = 4, n_frames: int = 8):
        self.vp = vp
        self.color = color
        self.n_frames = n_frames
        self.base: dict[tuple[int, int], bytes] = {}
        self.frames: list[tuple[int, dict[tuple[int, int], bytes]]] = []
        self.fetched_at = 0.0
        self.error = ""
        self.version = 0
        self._busy = False
        self._lock = threading.Lock()
        self.cache_dir = config.DATA_DIR / "tiles" / f"osm{vp.z}"

    def maybe_refresh(self) -> None:
        if not self._busy and time.time() - self.fetched_at > REFRESH_S:
            self._busy = True
            threading.Thread(target=self._run, name="radar", daemon=True).start()

    def _get(self, s: requests.Session, url: str) -> bytes | None:
        r = s.get(url, timeout=20)
        if r.status_code == 200 and r.headers.get("Content-Type", "").startswith("image/"):
            return r.content
        log.debug("tile %s -> %s", url, r.status_code)
        return None

    def _run(self) -> None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        try:
            # base map: disk cache, fetched once
            if not self.base:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                base = {}
                for tx, ty in self.vp.tiles:
                    p = self.cache_dir / f"{tx}_{ty}.png"
                    if p.exists() and p.stat().st_size > 100:
                        base[(tx, ty)] = p.read_bytes()
                    else:
                        data = self._get(s, OSM_TILE.format(z=self.vp.z, x=tx, y=ty))
                        if data:
                            p.write_bytes(data)
                            base[(tx, ty)] = data
                        time.sleep(0.2)   # be polite to OSM
                with self._lock:
                    self.base = base
            idx = s.get(RV_INDEX, timeout=15).json()
            host = idx["host"]
            past = idx.get("radar", {}).get("past", [])[-self.n_frames:]
            have = {ts for ts, _ in self.frames}
            frames = [f for f in self.frames if f[0] in {p["time"] for p in past}]
            for p in past:
                if p["time"] in have:
                    continue
                tiles = {}
                for tx, ty in self.vp.tiles:
                    url = f"{host}{p['path']}/{TILE}/{self.vp.z}/{tx}/{ty}/{self.color}/1_1.png"
                    data = self._get(s, url)
                    if data:
                        tiles[(tx, ty)] = data
                frames.append((p["time"], tiles))
            frames.sort()
            with self._lock:
                self.frames = frames
                self.fetched_at = time.time()
                self.error = ""
                self.version += 1
            log.info("radar: %d frames, latest %s", len(frames),
                     time.strftime("%H:%M", time.localtime(frames[-1][0])) if frames else "-")
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:80]
            log.warning("radar: %s", self.error)
        finally:
            self._busy = False


def home_lonlat(ha_cfg: dict) -> tuple[float, float] | None:
    """Home Assistant's configured home location, if we have a token."""
    p = Path(ha_cfg.get("token_file") or (config.CONFIG_DIR / "ha_token")).expanduser()
    if not p.exists():
        return None
    try:
        r = requests.get(ha_cfg["url"].rstrip("/") + "/api/config",
                         headers={"Authorization": f"Bearer {p.read_text().strip()}"}, timeout=10)
        r.raise_for_status()
        j = r.json()
        return float(j["longitude"]), float(j["latitude"])
    except Exception as e:  # noqa: BLE001
        log.debug("home location: %s", e)
        return None


def decode(png: bytes):
    import pygame
    return pygame.image.load(io.BytesIO(png), "tile.png")
