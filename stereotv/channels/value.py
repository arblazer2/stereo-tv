"""Channel 15: Collection Value — 80s financial-channel take on Discogs marketplace data."""
from __future__ import annotations

import logging
import random
import time

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import Channel

log = logging.getLogger("stereotv.ch.value")

RELOAD_S = 1800
TICKER_PX_S = 70.0
GREEN, RED, AMBER = (60, 220, 90), (240, 70, 70), D.AMBER


def _fmt(v: float | None) -> str:
    return "—" if v is None else (f"${v:,.0f}" if v >= 100 else f"${v:,.2f}")


class ValueChannel(Channel):
    number = 15
    name = "COLLECTION VALUE"

    def __init__(self, display, now):
        super().__init__(display, now)
        self.history: list = []
        self.top: list = []
        self.ticker: list[tuple[str, float | None, float | None]] = []
        self.counts = (0, 0)
        self._loaded = 0.0
        self._tick_surf: pygame.Surface | None = None
        self._tick_x = 0.0

    def _load(self) -> None:
        con = discogs.open_db()
        self.history = con.execute("SELECT * FROM value_history ORDER BY day").fetchall()
        self.top = con.execute("SELECT r.artist, r.title, r.year, m.lowest, m.for_sale FROM market m JOIN releases r USING(release_id) "
                               "WHERE m.lowest IS NOT NULL ORDER BY m.lowest DESC LIMIT 8").fetchall()
        rows = con.execute("SELECT r.artist, r.title, m.lowest, m.prev_lowest FROM market m JOIN releases r USING(release_id) "
                           "WHERE m.lowest IS NOT NULL ORDER BY RANDOM() LIMIT 40").fetchall()
        priced = con.execute("SELECT COUNT(*) FROM market WHERE lowest IS NOT NULL").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
        con.close()
        self.counts = (priced, total)
        self.ticker = [(f"{r['artist']} · {r['title']}", r["lowest"], r["prev_lowest"]) for r in rows]
        self._loaded = time.monotonic()
        self._tick_surf = self._render_ticker()
        self._tick_x = 0.0

    def _render_ticker(self) -> pygame.Surface | None:
        if not self.ticker:
            return None
        f = self.d.fonts["sm"]
        parts = []
        for name, low, prev in self.ticker:
            arrow, col = "", D.WHITE
            if prev is not None and low is not None and abs(low - prev) >= 0.01:
                arrow, col = ("▲", GREEN) if low > prev else ("▼", RED)
            parts.append((name.upper(), D.YELLOW))
            parts.append((f" {_fmt(low)} {arrow}    ", col))
        w = sum(f.size(t)[0] for t, _ in parts)
        s = pygame.Surface((w, 30))
        s.fill((8, 12, 30))
        x = 0
        for t, col in parts:
            img = f.render(t, True, col)
            s.blit(img, (x, (30 - img.get_height()) // 2))
            x += img.get_width()
        return s

    def enter(self) -> None:
        if time.monotonic() - self._loaded > RELOAD_S or not self.history:
            try:
                self._load()
            except Exception as e:  # noqa: BLE001
                log.warning("value: %s", e)

    def update(self, dt: float) -> None:
        if self._tick_surf:
            self._tick_x = (self._tick_x + TICKER_PX_S * dt) % self._tick_surf.get_width()

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill((8, 12, 30))
        bar = self.header(surface, time.strftime("%a %b %d · %I:%M %p").upper().replace(" 0", " "), color=D.CYAN)
        safe = d.safe
        if not self.history:
            d.text("NO MARKET DATA YET", "md", D.GREY, (safe.left, bar.bottom + 20))
            d.text("the nightly sync fills this in", "sm", D.GREY, (safe.left, bar.bottom + 56), shadow=False)
            return
        latest = self.history[-1]
        y = bar.bottom + 12
        # headline numbers
        d.text("DISCOGS ESTIMATE", "sm", D.GREY, (safe.left, y), shadow=False)
        d.text(f"{self.counts[0]}/{self.counts[1]} priced", "sm", D.GREY, (safe.right, y), anchor="topright", shadow=False)
        y += 26
        for i, (lab, key, col) in enumerate((("MIN", "minimum", D.GREY), ("MEDIAN", "median", GREEN), ("MAX", "maximum", AMBER))):
            x = safe.left + i * (safe.width // 3)
            d.text(lab, "sm", col, (x, y), shadow=False)
            d.text(_fmt(latest[key]), "lg" if key == "median" else "md", D.WHITE, (x, y + 22))
        y += 70
        # chart of median over time (needs ≥2 days) else a flat reference line
        chart = pygame.Rect(safe.left, y + 26, 200, safe.bottom - y - 76)
        d.text("MEDIAN TREND", "sm", GREEN, (safe.left, y), shadow=False)
        pygame.draw.rect(surface, (14, 20, 48), chart)
        pygame.draw.rect(surface, (40, 60, 120), chart, 2)
        vals = [h["median"] for h in self.history if h["median"] is not None]
        if len(vals) >= 2:
            lo, hi = min(vals), max(vals)
            span = (hi - lo) or 1.0
            pts = [(chart.left + 6 + i * (chart.width - 12) // (len(vals) - 1),
                    chart.bottom - 6 - int((v - lo) / span * (chart.height - 12))) for i, v in enumerate(vals)]
            pygame.draw.lines(surface, GREEN, False, pts, 3)
            delta = vals[-1] - vals[0]
            d.text(d.fit_text(f"{'+' if delta >= 0 else '-'}{_fmt(abs(delta))} since {self.history[0]['day'][5:]}", "sm", chart.width - 12),
                   "sm", GREEN if delta >= 0 else RED, (chart.left + 6, chart.bottom - 30), shadow=False)
        else:
            pygame.draw.rect(surface, GREEN, (chart.left + 6, chart.centery - 1, chart.width - 12, 3))
            d.text("1 day of data", "sm", D.GREY, (chart.left + 6, chart.bottom - 30), shadow=False)
            d.text("builds nightly", "sm", D.GREY, (chart.left + 6, chart.top + 6), shadow=False)
        # most valuable
        x = chart.right + 16
        d.text("TOP ASKS", "sm", AMBER, (x, y), shadow=False)
        yy = y + 26
        for r in self.top[:8]:
            price = _fmt(r["lowest"])
            pw = d.fonts["sm"].size(price)[0] + 10
            d.text(d.fit_text(f"{r['artist']} · {r['title']}", "sm", safe.right - x - pw), "sm", D.WHITE, (x, yy), shadow=False)
            d.text(price, "sm", GREEN, (safe.right, yy), anchor="topright", shadow=False)
            yy += 26
        # ticker crawl
        band = pygame.Rect(0, safe.bottom - 36, d.w, 34)
        pygame.draw.rect(surface, (8, 12, 30), band)
        pygame.draw.rect(surface, (40, 60, 120), (0, band.top, d.w, 2))
        if self._tick_surf:
            tw = self._tick_surf.get_width()
            x0 = safe.left - int(self._tick_x)
            surface.set_clip(pygame.Rect(safe.left, band.top, safe.width, band.height))
            surface.blit(self._tick_surf, (x0, band.top + 2))
            surface.blit(self._tick_surf, (x0 + tw, band.top + 2))
            surface.set_clip(None)
