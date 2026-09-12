"""Channel 6: Tracklist — sides of the current release, current track highlighted."""
from __future__ import annotations

import logging
import re

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import Channel, thumb

log = logging.getLogger("stereotv.ch.tracklist")

ROW_H = 27


class TracklistChannel(Channel):
    number = 6
    name = "TRACKLIST"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._rid = None
        self._sides: list[tuple[str, list[dict]]] = []
        self._page = 0
        self._t = 0.0

    def _load(self, rel) -> None:
        self._sides = []
        if rel.release_id > 0:
            con = discogs.open_db()
            rows = [dict(r) for r in con.execute(
                "SELECT position, title, duration, seq FROM tracks WHERE release_id=? ORDER BY seq", (rel.release_id,))]
            con.close()
            sides: dict[str, list[dict]] = {}
            for r in rows:
                m = re.match(r"^([A-Za-z]+)", r["position"] or "")
                key = m.group(1).upper() if m else ""
                sides.setdefault(key, []).append(r)
            self._sides = [(f"SIDE {k}" if k else "TRACKS", v) for k, v in sides.items()]
        self._page, self._t = 0, 0.0

    def update(self, dt: float) -> None:
        rel = self.now.release
        rid = rel.release_id if rel else None
        if rid != self._rid:
            self._rid = rid
            if rel:
                self._load(rel)
        self._t += dt
        pages = max(1, len(self._sides))
        if pages > 1 and self._t > 12.0:
            self._t = 0.0
            self._page = (self._page + 1) % pages
            # after cycling, snap back to the side that's playing
            if self.now.side:
                for i, (name, _) in enumerate(self._sides):
                    if name == f"SIDE {self.now.side}":
                        self._page = i if self._page == (i + 1) % pages and pages > 2 else self._page

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        rel = self.now.release
        d.backdrop(surface, rel.cover_path if rel else None, fill=D.NAVY)
        bar = self.header(surface, self.now_line())
        safe = d.safe
        if not rel:
            d.text("NOTHING PLAYING", "md", D.GREY, (safe.left, bar.bottom + 20))
            return
        if not self._sides:
            img = thumb(rel.cover_path, 120)
            surface.blit(img, img.get_rect(topleft=(safe.left, bar.bottom + 16)))
            x = safe.left + 140
            d.text("NO TRACKLIST", "md", D.GREY, (x, bar.bottom + 16))
            if self.now.track_title:
                d.text("NOW: " + d.fit_text(self.now.track_title, "sm", safe.right - x - 60), "sm", D.AMBER, (x, bar.bottom + 56))
            return
        d.card(surface, pygame.Rect(safe.left - 8, bar.bottom + 6, safe.width + 16, safe.bottom - bar.bottom - 10))
        cols = [self._sides[self._page % len(self._sides)]]
        colw = safe.width
        cur_side, cur_pos = self.now.side, self.now.track
        img = thumb(rel.cover_path, 64)
        surface.blit(img, img.get_rect(bottomright=(safe.right, safe.bottom - 20)))
        for ci, (name, tracks) in enumerate(cols):
            x = safe.left + ci * colw
            y = bar.bottom + 14
            d.text(name, "md", D.YELLOW, (x, y)); y += 36
            per = (safe.bottom - y) // ROW_H
            if len(tracks) > per:
                # long side: scroll so the current track stays visible
                cur_i = next((i for i, t in enumerate(tracks) if _is_cur(t, cur_side, cur_pos)), 0)
                start = max(0, min(cur_i - per // 2, len(tracks) - per))
                tracks = tracks[start:start + per]
            for t in tracks:
                cur = _is_cur(t, cur_side, cur_pos)
                if cur:
                    d.panel(surface, pygame.Rect(x - 4, y - 2, colw - 8, ROW_H), D.BLUE, radius=8 if d.modern else 0)
                    pygame.draw.polygon(surface, D.AMBER, [(x + 2, y + 5), (x + 2, y + ROW_H - 9), (x + 12, y + ROW_H // 2 - 2)])
                col = D.WHITE if cur else D.GREY
                pos = (t["position"] or "").upper()
                d.text(pos, "sm", D.CYAN if cur else (110, 140, 190), (x + 20, y), shadow=False)
                dur = t["duration"] or ""
                durw = d.fonts["sm"].size(dur)[0] + 8 if dur else 0
                title = d.fit_text(t["title"], "sm", colw - 70 - durw)
                d.text(title, "sm", col, (x + 62, y), shadow=False)
                if dur:
                    d.text(dur, "sm", (110, 140, 190), (x + colw - 16, y), anchor="topright", shadow=False)
                y += ROW_H
        pages = len(self._sides)
        if pages > 1:
            for i in range(pages):
                c = D.CYAN if i == self._page % pages else D.GREY
                pygame.draw.circle(surface, c, (safe.right - 12 - (pages - 1 - i) * 16, safe.bottom - 8), 4)


def _is_cur(t: dict, side: str | None, pos: str | None) -> bool:
    if not side and not pos:
        return False
    p = (t["position"] or "").upper()
    want = f"{side or ''}{pos or ''}".upper()
    return p == want or p.replace("-", "") == want
