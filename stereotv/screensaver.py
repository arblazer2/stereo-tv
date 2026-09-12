"""Idle screensavers, driven by the main loop (not channels).

  bounce   — a random cover drifts and bounces, DVD-logo style
  flying   — covers fly out of a starfield toward the viewer
  weather  — "Local on the 8s": current conditions + 5-day forecast from Home Assistant

`screensaver.modes` in config picks which run; they rotate each idle period.
"""
from __future__ import annotations

import logging
import math
import random
import time

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import thumb
from stereotv.weather import WeatherCache, compass

log = logging.getLogger("stereotv.screensaver")


def _random_release(with_cover: bool = True):
    try:
        con = discogs.open_db()
        rel = discogs.random_release(con, with_cover=with_cover)
        con.close()
        return rel
    except Exception as e:  # noqa: BLE001
        log.debug("random release: %s", e)
        return None


def _clock() -> str:
    return time.strftime("%I:%M %p").lstrip("0")


# ---------------------------------------------------------------- bounce

class BounceSaver:
    name = "bounce"
    SIZE, SWAP_S, SPEED = 200, 20.0, 55.0

    def __init__(self, display, now):
        self.d = display
        self.now = now
        self.rel = None
        self.img: pygame.Surface | None = None
        self.x = float(display.safe.left + 40)
        self.y = float(display.safe.top + 40)
        self.vx = self.SPEED * 0.9 * random.choice((-1, 1))
        self.vy = self.SPEED * random.uniform(0.4, 1.1)
        self._t = self.SWAP_S

    def enter(self) -> None:
        self._t = self.SWAP_S

    def update(self, dt: float) -> None:
        self._t += dt
        if self._t >= self.SWAP_S or self.img is None:
            self._t = 0.0
            self.rel = _random_release()
            self.img = thumb(self.rel.cover_path if self.rel else None, self.SIZE)
        w, h = self.img.get_size()
        safe = self.d.safe
        box = pygame.Rect(safe.left, safe.top, safe.width, safe.height - 60)
        self.x += self.vx * dt
        self.y += self.vy * dt
        if self.x < box.left:
            self.x, self.vx = box.left, abs(self.vx)
        elif self.x + w > box.right:
            self.x, self.vx = box.right - w, -abs(self.vx)
        if self.y < box.top:
            self.y, self.vy = box.top, abs(self.vy)
        elif self.y + h > box.bottom:
            self.y, self.vy = box.bottom - h, -abs(self.vy)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.BLACK)
        if self.img is None:
            return
        r = self.img.get_rect(topleft=(int(self.x), int(self.y)))
        pygame.draw.rect(surface, (30, 30, 40), r.inflate(6, 6))
        surface.blit(self.img, r)
        if self.rel:
            cap = d.fit_text(f"{self.rel.artist} · {self.rel.title}", "sm", r.width + 160)
            d.text(cap, "sm", D.GREY, (r.centerx, r.bottom + 8), anchor="midtop")
        d.text(_clock(), "mono", (70, 90, 70), (d.safe.right, d.safe.top), anchor="topright", shadow=False)
        d.text("STAND BY", "sm", (70, 90, 70), (d.safe.left, d.safe.top), shadow=False)


# ---------------------------------------------------------------- flying covers

class FlyingSaver:
    name = "flying"
    N_COVERS, N_STARS, BASE = 9, 50, 128
    Z_FAR, Z_NEAR, SPEED = 6.0, 0.55, 0.9        # z units/s
    FOCAL = 300.0
    MAX_SIZE = 420

    def __init__(self, display, now):
        self.d = display
        self.now = now
        self.covers: list[dict] = []
        self.stars: list[list[float]] = []
        self.cx, self.cy = display.w / 2, display.h / 2

    def _spawn(self, z: float | None = None) -> dict:
        rel = _random_release()
        return {"img": thumb(rel.cover_path if rel else None, self.BASE),
                "x": random.uniform(-2.2, 2.2), "y": random.uniform(-1.6, 1.6),
                "z": z if z is not None else self.Z_FAR,
                "scaled": None, "scaled_size": 0}

    def enter(self) -> None:
        self.covers = [self._spawn(random.uniform(self.Z_NEAR, self.Z_FAR)) for _ in range(self.N_COVERS)]
        self.stars = [[random.uniform(-3, 3), random.uniform(-3, 3), random.uniform(0.3, self.Z_FAR)]
                      for _ in range(self.N_STARS)]

    def update(self, dt: float) -> None:
        for c in self.covers:
            c["z"] -= self.SPEED * dt
            if c["z"] < self.Z_NEAR:
                c.update(self._spawn())
        for s in self.stars:
            s[2] -= self.SPEED * 1.6 * dt
            if s[2] < 0.1:
                s[0], s[1], s[2] = random.uniform(-3, 3), random.uniform(-3, 3), self.Z_FAR

    def _project(self, x: float, y: float, z: float) -> tuple[int, int]:
        return int(self.cx + x * self.FOCAL / z), int(self.cy + y * self.FOCAL / z)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.BLACK)
        for x, y, z in self.stars:
            px, py = self._project(x, y, z)
            if 0 <= px < d.w and 0 <= py < d.h:
                b = int(255 * min(1.0, 1.5 / z))
                sz = 3 if z < 1.5 else 2
                pygame.draw.rect(surface, (b, b, b), (px, py, sz, sz))
        for c in sorted(self.covers, key=lambda c: -c["z"]):
            px, py = self._project(c["x"], c["y"], c["z"])
            size = min(self.MAX_SIZE, int(self.BASE * 2.2 / c["z"]))
            if size < 8:
                continue
            # rescale only when the size moved a few px: scaling is the Pi's bottleneck here
            step = 2 if size < 64 else 4 if size < 200 else 8
            if c["scaled"] is None or abs(size - c["scaled_size"]) >= step:
                c["scaled"] = pygame.transform.scale(c["img"], (size, size))
                c["scaled_size"] = size
            img = c["scaled"]
            r = img.get_rect(center=(px, py))
            if r.colliderect(surface.get_rect()):
                surface.blit(img, r)
        d.text(_clock(), "mono", (70, 90, 70), (d.safe.right, d.safe.top), anchor="topright", shadow=False)


# ---------------------------------------------------------------- Local on the 8s

ICON_COLORS = {"sunny": D.YELLOW, "clear-night": (200, 200, 240), "partlycloudy": (230, 230, 230),
               "cloudy": (190, 190, 200), "rainy": (110, 170, 255), "pouring": (80, 140, 255),
               "lightning": D.AMBER, "lightning-rainy": D.AMBER, "snowy": D.WHITE, "snowy-rainy": D.WHITE,
               "fog": (170, 170, 170), "windy": (200, 220, 230), "hail": D.WHITE, "exceptional": D.RED}
LABELS = {"partlycloudy": "PARTLY CLOUDY", "clear-night": "CLEAR", "lightning-rainy": "T-STORMS",
          "lightning": "T-STORMS", "snowy-rainy": "WINTRY MIX", "pouring": "HEAVY RAIN"}


def _draw_icon(surface, cond: str, cx: int, cy: int, r: int) -> None:
    col = ICON_COLORS.get(cond, (200, 200, 200))
    if cond in ("sunny", "clear-night"):
        pygame.draw.circle(surface, col, (cx, cy), r)
        if cond == "sunny":
            for k in range(8):
                a = k * math.pi / 4
                pygame.draw.line(surface, col, (cx + int(math.cos(a) * r * 1.3), cy + int(math.sin(a) * r * 1.3)),
                                 (cx + int(math.cos(a) * r * 1.7), cy + int(math.sin(a) * r * 1.7)), 3)
    else:
        if cond == "partlycloudy":
            pygame.draw.circle(surface, D.YELLOW, (cx - r // 2, cy - r // 2), r // 2 + 2)
        # cloud: three circles + base
        pygame.draw.circle(surface, col, (cx - r // 2, cy), r // 2 + 2)
        pygame.draw.circle(surface, col, (cx + r // 3, cy - r // 4), r // 2 + 4)
        pygame.draw.circle(surface, col, (cx + r // 2 + 4, cy + r // 6), r // 2)
        pygame.draw.rect(surface, col, (cx - r // 2, cy, r + 6, r // 2 + 2))
        if cond in ("rainy", "pouring", "lightning-rainy", "snowy-rainy"):
            for k in range(3):
                x = cx - r // 2 + k * (r // 2) + 4
                pygame.draw.line(surface, (110, 170, 255), (x, cy + r // 2 + 8), (x - 4, cy + r + 4), 3)
        if cond in ("snowy", "snowy-rainy"):
            for k in range(3):
                pygame.draw.circle(surface, D.WHITE, (cx - r // 2 + k * (r // 2) + 4, cy + r // 2 + 12), 3)
        if cond in ("lightning", "lightning-rainy"):
            pygame.draw.lines(surface, D.YELLOW, False, [(cx + 4, cy + r // 3), (cx - 4, cy + r // 2 + 8),
                                                         (cx + 4, cy + r // 2 + 8), (cx - 4, cy + r + 6)], 3)


class WeatherSaver:
    name = "weather"
    PAGE_S = 10.0

    def __init__(self, display, now, cfg: dict):
        self.d = display
        self.now = now
        self.cache = WeatherCache(cfg)
        self._t = 0.0
        self._page = 0
        self._grad = self._gradient()

    def _gradient(self) -> pygame.Surface:
        s = pygame.Surface((self.d.w, self.d.h))
        for y in range(self.d.h):
            k = y / self.d.h
            s.fill((int(10 + 30 * k), int(20 + 50 * k), int(90 + 90 * k)), (0, y, self.d.w, 1))
        return s

    def enter(self) -> None:
        self._t, self._page = 0.0, 0
        self.cache.get()

    def update(self, dt: float) -> None:
        self._t += dt
        if self._t >= self.PAGE_S:
            self._t = 0.0
            self._page ^= 1

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.blit(self._grad, (0, 0))
        safe = d.safe
        w = self.cache.get()
        # header
        hdr = pygame.Rect(0, safe.top, d.w, 46)
        pygame.draw.rect(surface, (8, 16, 60), hdr)
        pygame.draw.rect(surface, D.AMBER, (0, hdr.bottom - 3, d.w, 3))
        d.text("LOCAL ON THE 8s", "md", D.WHITE, (safe.left + 8, hdr.centery), anchor="midleft")
        d.text(_clock(), "mono", D.AMBER, (safe.right - 8, hdr.centery), anchor="midright")
        if w.error and not w.fetched_at:
            d.text("NO WEATHER DATA", "lg", D.WHITE, (d.w // 2, d.h // 2), anchor="center")
            d.text(w.error, "sm", D.GREY, (d.w // 2, d.h // 2 + 40), anchor="center")
            return
        if self._page == 0 or not w.daily:
            self._draw_current(surface, w)
        else:
            self._draw_forecast(surface, w)
        # footer ticker
        foot = pygame.Rect(0, safe.bottom - 34, d.w, 34)
        pygame.draw.rect(surface, (8, 16, 60), foot)
        rel = self.now.release or self.now.last_played
        tag = "NOW PLAYING" if self.now.release else "LAST PLAYED"
        msg = f"{tag}: {rel.artist} · {rel.title}" if rel else "STEREO-TV · NOTHING PLAYING"
        d.text(d.fit_text(msg, "sm", safe.width), "sm", D.YELLOW, (safe.left + 8, foot.centery), anchor="midleft", shadow=False)

    def _draw_current(self, surface, w) -> None:
        d = self.d
        safe = d.safe
        top = safe.top + 60
        d.text("CURRENT CONDITIONS", "sm", D.AMBER, (safe.left + 8, top))
        _draw_icon(surface, w.condition, safe.left + 90, top + 110, 40)
        if w.temp is not None:
            d.text(f"{round(w.temp)}°", "xl", D.WHITE, (safe.left + 190, top + 60))
        cond = LABELS.get(w.condition, w.condition.upper().replace("-", " "))
        d.text(cond, "md", D.WHITE, (safe.left + 190, top + 120))
        y = top + 170
        rows = []
        if w.humidity is not None:
            rows.append(("HUMIDITY", f"{round(w.humidity)}%"))
        if w.wind_speed is not None:
            rows.append(("WIND", f"{compass(w.wind_bearing)} {round(w.wind_speed)} {w.wind_unit}"))
        if w.pressure is not None:
            rows.append(("PRESSURE", f"{w.pressure:.2f} in"))
        if w.daily:
            t = w.daily[0]
            rows.append(("TODAY", f"HI {round(t.get('temperature', 0))}°  LO {round(t.get('templow', 0))}°"))
        for k, v in rows:
            d.text(k, "sm", D.CYAN, (safe.left + 30, y), shadow=False)
            d.text(v, "sm", D.WHITE, (safe.left + 200, y), shadow=False)
            y += 28

    def _draw_forecast(self, surface, w) -> None:
        d = self.d
        safe = d.safe
        top = safe.top + 60
        d.text("EXTENDED FORECAST", "sm", D.AMBER, (safe.left + 8, top))
        days = w.daily[:5]
        colw = safe.width // max(1, len(days))
        for i, day in enumerate(days):
            cx = safe.left + colw * i + colw // 2
            try:
                name = time.strftime("%a", time.strptime(day["datetime"][:10], "%Y-%m-%d")).upper()
            except (KeyError, ValueError):
                name = ""
            d.text(name, "md", D.WHITE, (cx, top + 34), anchor="midtop")
            _draw_icon(surface, day.get("condition", ""), cx, top + 110, 26)
            d.text(f"{round(day.get('temperature', 0))}°", "md", D.WHITE, (cx, top + 165), anchor="midtop")
            d.text(f"{round(day.get('templow', 0))}°", "sm", D.CYAN, (cx, top + 200), anchor="midtop")
            pp = day.get("precipitation_probability")
            if pp:
                d.text(f"{round(pp)}%", "sm", (110, 170, 255), (cx, top + 228), anchor="midtop", shadow=False)


# ---------------------------------------------------------------- spinning record

class RecordSaver:
    name = "record"
    RPM = 33.3
    R = 190          # platter radius px

    def __init__(self, display, now):
        self.d = display
        self.now = now
        self.angle = 0.0
        self.disc: pygame.Surface | None = None
        self._rid = None
        self.rel = None

    def _build(self, rel) -> None:
        R = self.R
        s = pygame.Surface((2 * R, 2 * R), pygame.SRCALPHA)
        c = (R, R)
        pygame.draw.circle(s, (16, 16, 18), c, R)
        for r in range(R - 6, 78, -5):                 # groove bands (≥2 px apart, no 1-px lines)
            pygame.draw.circle(s, (28, 28, 32) if (r // 5) % 2 else (20, 20, 23), c, r, 2)
        label = thumb(rel.cover_path if rel else None, 150)
        lab = pygame.Surface((150, 150), pygame.SRCALPHA)
        lab.blit(label, (0, 0))
        mask = pygame.Surface((150, 150), pygame.SRCALPHA)
        pygame.draw.circle(mask, (255, 255, 255, 255), (75, 75), 75)
        lab.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        s.blit(lab, (R - 75, R - 75))
        pygame.draw.circle(s, (60, 60, 65), c, 75, 2)
        pygame.draw.circle(s, (200, 200, 205), c, 5)
        self.disc = s

    def enter(self) -> None:
        self.rel = self.now.release or self.now.last_played or _random_release()
        self._rid = self.rel.release_id if self.rel else None
        self._build(self.rel)

    def update(self, dt: float) -> None:
        rel = self.now.release or self.now.last_played
        if rel and rel.release_id != self._rid:
            self.rel, self._rid = rel, rel.release_id
            self._build(rel)
        self.angle = (self.angle + self.RPM * 6.0 * dt) % 360.0     # 33⅓ rpm = 200°/s

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill((10, 8, 8))
        if not self.disc:
            return
        cx, cy = d.w // 2 - 40, d.h // 2 + 10
        rot = pygame.transform.rotate(self.disc, -self.angle)
        surface.blit(rot, rot.get_rect(center=(cx, cy)))
        # tonearm: pivot top-right, resting on the outer groove
        px, py = cx + 245, cy - 140
        tip = (cx + self.R - 24, cy - 40)
        pygame.draw.circle(surface, (70, 70, 78), (px, py), 22)
        pygame.draw.line(surface, (150, 150, 158), (px, py), tip, 7)
        pygame.draw.circle(surface, (150, 150, 158), tip, 8)
        label = f"{'NOW PLAYING' if self.now.release else 'LAST PLAYED'}"
        if self.rel:
            d.text(label, "sm", (90, 110, 90), (d.safe.left, d.safe.top), shadow=False)
            d.text(d.fit_text(f"{self.rel.artist} · {self.rel.title}", "sm", d.safe.width - 130), "sm", (140, 140, 140),
                   (d.safe.left, d.safe.bottom - 26), shadow=False)
        d.text(_clock(), "mono", (70, 90, 70), (d.safe.right, d.safe.top), anchor="topright", shadow=False)


# ---------------------------------------------------------------- cover wall

class WallSaver:
    name = "wall"
    COLS, ROWS = 8, 6
    FLIP_EVERY = 0.7
    FLIP_S = 0.35

    def __init__(self, display, now):
        self.d = display
        self.now = now
        self.tile = display.w // self.COLS
        self.grid: list[pygame.Surface | None] = [None] * (self.COLS * self.ROWS)
        self.pool: list = []
        self._t = 0.0
        self._flip: tuple[int, pygame.Surface, float] | None = None   # (index, new surface, progress)
        self._cache: dict = {}

    def _thumb(self, rel) -> pygame.Surface:
        key = rel.release_id if rel else 0
        s = self._cache.get(key)
        if s is None:
            s = pygame.transform.smoothscale(thumb(rel.cover_path if rel else None, 128), (self.tile, self.tile))
            if len(self._cache) > 120:
                self._cache.clear()
            self._cache[key] = s
        return s

    def _refill(self) -> None:
        try:
            con = discogs.open_db()
            rows = con.execute("SELECT * FROM releases WHERE cover_path IS NOT NULL ORDER BY RANDOM() LIMIT 80").fetchall()
            con.close()
            from stereotv.state import Release
            self.pool = [Release.from_row(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            log.debug("wall: %s", e)

    def enter(self) -> None:
        self._refill()
        random.shuffle(self.pool)
        for i in range(len(self.grid)):
            self.grid[i] = self._thumb(self.pool[i % len(self.pool)]) if self.pool else None
        self._t, self._flip = 0.0, None

    def update(self, dt: float) -> None:
        if not self.pool:
            return
        if self._flip:
            i, new, prog = self._flip
            prog += dt / self.FLIP_S
            if prog >= 1.0:
                self.grid[i] = new
                self._flip = None
            else:
                self._flip = (i, new, prog)
        else:
            self._t += dt
            if self._t >= self.FLIP_EVERY:
                self._t = 0.0
                if len(self.pool) < 10:
                    self._refill()
                self._flip = (random.randrange(len(self.grid)), self._thumb(self.pool.pop()), 0.0)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.BLACK)
        t = self.tile
        for i, img in enumerate(self.grid):
            x, y = (i % self.COLS) * t, (i // self.COLS) * t
            if self._flip and self._flip[0] == i:
                _, new, prog = self._flip
                # horizontal squash: old shrinks to a line, new grows out
                if prog < 0.5:
                    w = max(2, int(t * (1 - prog * 2)))
                    src = img
                else:
                    w = max(2, int(t * ((prog - 0.5) * 2)))
                    src = new
                if src is not None:
                    surface.blit(pygame.transform.scale(src, (w, t)), (x + (t - w) // 2, y))
            elif img is not None:
                surface.blit(img, (x, y))
        # dim veil + clock so it isn't full brightness on the CRT all night
        veil = pygame.Surface((d.w, d.h)); veil.fill((70, 70, 70))
        surface.blit(veil, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        box = pygame.Rect(0, 0, 150, 40); box.topright = (d.safe.right, d.safe.top)
        pygame.draw.rect(surface, D.BLACK, box)
        d.text(_clock(), "mono", (200, 200, 200), box.center, anchor="center", shadow=False)


# ---------------------------------------------------------------- rotation

class Screensaver:
    """Facade used by main.py: rotates through the configured savers."""

    def __init__(self, display, now, cfg: dict):
        sc = cfg.get("screensaver", {})
        modes = sc.get("modes", ["wall", "flying", "weather", "bounce"])
        self.savers = []
        for m in modes:
            if m == "bounce":
                self.savers.append(BounceSaver(display, now))
            elif m == "flying":
                self.savers.append(FlyingSaver(display, now))
            elif m == "weather":
                self.savers.append(WeatherSaver(display, now, cfg))
            elif m == "record":
                self.savers.append(RecordSaver(display, now))
            elif m == "wall":
                self.savers.append(WallSaver(display, now))
            else:
                log.warning("unknown screensaver mode %r", m)
        if not self.savers:
            self.savers.append(BounceSaver(display, now))
        self.i = -1
        self.cur = self.savers[0]

    def enter(self) -> None:
        self.i = (self.i + 1) % len(self.savers)
        self.cur = self.savers[self.i]
        self.cur.enter()
        log.info("screensaver on (%s)", self.cur.name)

    def leave(self) -> None:
        log.info("screensaver off")

    def update(self, dt: float) -> None:
        self.cur.update(dt)

    def draw(self, surface: pygame.Surface) -> None:
        self.cur.draw(surface)
