"""Channel 10: Local Radar — animated RainViewer radar centred on the configured location."""
from __future__ import annotations

import logging
import threading
import time

import pygame

from stereotv import display as D
from stereotv import radar as R
from stereotv.channels.base import Channel

log = logging.getLogger("stereotv.ch.radar")

FRAME_S = 0.45      # animation step
HOLD_S = 1.6        # pause on the newest frame


class RadarChannel(Channel):
    number = 10
    name = "LOCAL RADAR"

    def __init__(self, display, now, cfg: dict):
        super().__init__(display, now)
        rc = cfg.get("radar", {})
        self.vp = R.Viewport(float(rc.get("lon") or 0.0), float(rc.get("lat") or 0.0), int(rc.get("zoom", 7)),
                             display.w, display.h)
        self.fetcher = R.RadarFetcher(self.vp, int(rc.get("color", 4)), int(rc.get("frames", 8)))
        self.title = rc.get("title") or "LOCAL"
        self.map_brightness = max(0, min(255, int(rc.get("map_brightness", 185))))
        self.base: pygame.Surface | None = None
        self.frames: list[tuple[int, pygame.Surface]] = []
        self._built_version = -1
        self._pending: list[tuple[int, dict]] = []
        self._i = 0
        self._t = 0.0
        self.home: tuple[int, int] | None = None
        self._ha = cfg.get("ha", {})
        self._cfg = cfg
        threading.Thread(target=self._find_home, daemon=True).start()

    def _find_home(self) -> None:
        loc = self._cfg.get("location", {})
        ll = (float(loc["lon"]), float(loc["lat"])) if loc.get("lat") and loc.get("lon") else None
        if not ll and self._ha.get("url"):
            ll = R.home_lonlat(self._ha)
        if ll:
            self.home = self.vp.project(*ll)

    def enter(self) -> None:
        self.fetcher.maybe_refresh()

    # ------------------------------------------------------------ compositing (main thread, incremental)
    def _build_base(self) -> None:
        s = pygame.Surface((self.d.w, self.d.h))
        s.fill((40, 50, 60))
        for (tx, ty), png in self.fetcher.base.items():
            try:
                img = R.decode(png).convert()
                # mute the map so radar reads on top; keep it dark for the CRT
                b = self.map_brightness
                dark = pygame.Surface(img.get_size()); dark.fill((b, b, min(255, b + 10)))
                img.blit(dark, (0, 0), special_flags=pygame.BLEND_RGB_MULT)
                s.blit(img, self.vp.tile_offset(tx, ty))
            except Exception as e:  # noqa: BLE001
                log.debug("base tile: %s", e)
        self.base = s

    def _build_frame(self, ts: int, tiles: dict) -> pygame.Surface:
        s = self.base.copy()
        for (tx, ty), png in tiles.items():
            try:
                s.blit(R.decode(png).convert_alpha(), self.vp.tile_offset(tx, ty))
            except Exception as e:  # noqa: BLE001
                log.debug("radar tile: %s", e)
        return s

    def update(self, dt: float) -> None:
        self.fetcher.maybe_refresh()
        if self.fetcher.version != self._built_version and self.fetcher.base:
            self._built_version = self.fetcher.version
            if self.base is None:
                self._build_base()
            have = {ts for ts, _ in self.frames}
            keep = {ts for ts, _ in self.fetcher.frames}
            self.frames = [f for f in self.frames if f[0] in keep]
            self._pending = [(ts, tiles) for ts, tiles in self.fetcher.frames if ts not in have]
        if self._pending:                       # one frame per tick: no hitch
            ts, tiles = self._pending.pop(0)
            self.frames.append((ts, self._build_frame(ts, tiles)))
            self.frames.sort(key=lambda f: f[0])
        if len(self.frames) > 1:
            self._t += dt
            step = HOLD_S if self._i == len(self.frames) - 1 else FRAME_S
            if self._t >= step:
                self._t = 0.0
                self._i = (self._i + 1) % len(self.frames)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        if not self.frames:
            surface.fill(D.DARK)
            msg = "LOADING RADAR…" if not self.fetcher.error else f"NO RADAR: {self.fetcher.error}"
            d.text(d.fit_text(msg, "md", d.w - 40), "md", D.GREY, (d.w // 2, d.h // 2), anchor="center")
            self.header(surface, self.title)
            return
        i = min(self._i, len(self.frames) - 1)
        ts, img = self.frames[i]
        surface.blit(img, (0, 0))
        if self.home:
            pygame.draw.circle(surface, D.BLACK, self.home, 7)
            pygame.draw.circle(surface, D.YELLOW, self.home, 5)
        # chrome: translucent header strip
        bar = pygame.Rect(0, d.safe.top, d.w, 40)
        strip = pygame.Surface((bar.w, bar.h), pygame.SRCALPHA); strip.fill((0, 0, 40, 170))
        surface.blit(strip, bar)
        d.text(f"{self.number:02d}  {self.name} · {self.title}", "sm", D.WHITE, (d.safe.left + 8, bar.centery), anchor="midleft")
        d.text(time.strftime("%I:%M %p", time.localtime(ts)).lstrip("0"), "mono", D.YELLOW,
               (d.safe.right - 8, bar.centery), anchor="midright")
        # progress pips along the bottom
        n = len(self.frames)
        for k in range(n):
            c = D.YELLOW if k == i else D.GREY
            pygame.draw.rect(surface, c, (d.safe.left + k * 14, d.safe.bottom - 8, 10, 5))
        if i == n - 1:
            d.text("LATEST", "sm", D.YELLOW, (d.safe.right, d.safe.bottom - 30), anchor="topright")
