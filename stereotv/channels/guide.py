"""Channel 5: Guide — Prevue-style: now playing panel on top, scrolling collection listing below."""
from __future__ import annotations

import logging
import time

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import Channel, thumb

log = logging.getLogger("stereotv.ch.guide")

ROW_H = 30
SCROLL_PX_S = 28.0
RELOAD_S = 600


class GuideChannel(Channel):
    number = 5
    name = "GUIDE"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._rows: list = []
        self._loaded = 0.0
        self._offset = 0.0
        self._cache: dict[int, pygame.Surface] = {}

    def _load(self) -> None:
        con = discogs.open_db()
        self._rows = con.execute(
            "SELECT release_id, artist, title, year, label FROM releases ORDER BY date_added DESC").fetchall()
        con.close()
        self._loaded = time.monotonic()
        self._cache.clear()

    def enter(self) -> None:
        if not self._rows or time.monotonic() - self._loaded > RELOAD_S:
            self._load()

    def update(self, dt: float) -> None:
        if self._rows:
            self._offset = (self._offset + SCROLL_PX_S * dt) % (len(self._rows) * ROW_H)

    def _row_surface(self, i: int, width: int) -> pygame.Surface:
        r = self._rows[i]
        s = self._cache.get(r["release_id"])
        if s is None:
            s = pygame.Surface((width, ROW_H))
            s.fill(D.NAVY if i % 2 else D.DARK)
            f = self.d.fonts["sm"]
            yr = f.render(str(r["year"] or "----"), True, D.CYAN)
            s.blit(yr, (8, (ROW_H - yr.get_height()) // 2))
            artist = self.d.fit_text(r["artist"], "sm", 200)
            a = f.render(artist, True, D.YELLOW)
            s.blit(a, (86, (ROW_H - a.get_height()) // 2))
            title = self.d.fit_text(r["title"], "sm", width - 300)
            t = f.render(title, True, D.WHITE)
            s.blit(t, (292, (ROW_H - t.get_height()) // 2))
            if len(self._cache) > 300:
                self._cache.clear()
            self._cache[r["release_id"]] = s
        return s

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.NAVY)
        safe = d.safe
        # top panel: now playing + clock
        top = pygame.Rect(safe.left, safe.top, safe.width, 170)
        pygame.draw.rect(surface, D.BLACK, top)
        pygame.draw.rect(surface, D.CYAN, top, 2)
        rel = self.now.release
        clock = time.strftime("%I:%M %p").lstrip("0")
        d.text(clock, "mono_lg", D.GREEN, (top.right - 10, top.top + 8), anchor="topright")
        d.text(f"{len(self._rows)} RECORDS", "sm", D.CYAN, (top.right - 10, top.bottom - 34), anchor="topright")
        d.text("NOW PLAYING", "sm", D.CYAN, (top.left + 12, top.top + 10))
        if rel:
            img = thumb(rel.cover_path, 120)
            r = img.get_rect(topleft=(top.left + 12, top.top + 38))
            surface.blit(img, r)
            x = r.right + 14
            maxw = top.right - x - 12
            y = top.top + 40
            d.text(d.fit_text(rel.artist, "md", maxw), "md", D.YELLOW, (x, y)); y += 34
            d.text(d.fit_text(rel.title, "sm", maxw), "sm", D.WHITE, (x, y)); y += 28
            meta = " · ".join(p for p in (str(rel.year or ""), rel.label.split(",")[0]) if p)
            d.text(d.fit_text(meta, "sm", maxw), "sm", D.GREY, (x, y)); y += 28
            if self.now.track_title:
                d.text(d.fit_text(self.now.track_title, "sm", maxw), "sm", D.AMBER, (x, y))
        else:
            d.text("NOTHING PLAYING", "md", D.GREY, (top.left + 12, top.top + 60))
            lp = self.now.last_played
            if lp:
                d.text(d.fit_text(f"LAST: {lp.artist} · {lp.title}", "sm", top.width - 24), "sm", D.CYAN, (top.left + 12, top.top + 100))

        # listing header
        hdr = pygame.Rect(safe.left, top.bottom + 8, safe.width, 32)
        pygame.draw.rect(surface, D.BLUE, hdr)
        d.text("YEAR", "sm", D.WHITE, (hdr.left + 8, hdr.centery), anchor="midleft", shadow=False)
        d.text("ARTIST", "sm", D.WHITE, (hdr.left + 86, hdr.centery), anchor="midleft", shadow=False)
        d.text("ALBUM", "sm", D.WHITE, (hdr.left + 292, hdr.centery), anchor="midleft", shadow=False)

        # scrolling rows, clipped
        area = pygame.Rect(safe.left, hdr.bottom, safe.width, safe.bottom - hdr.bottom)
        if not self._rows:
            return
        surface.set_clip(area)
        n = len(self._rows)
        first = int(self._offset // ROW_H)
        y = area.top - (self._offset % ROW_H)
        i = first
        while y < area.bottom:
            surface.blit(self._row_surface(i % n, area.width), (area.left, int(y)))
            y += ROW_H
            i += 1
        surface.set_clip(None)
        pygame.draw.rect(surface, D.CYAN, area, 2)
