"""Channel 4: In Your Collection — related releases from the local DB.

Sections cycle: more by this artist / same label, nearby years / shared styles.
"""
from __future__ import annotations

import logging

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import Channel, thumb
from stereotv.state import Release

log = logging.getLogger("stereotv.ch.collection")

SECTION_SECONDS = 10.0
THUMB = 96
PER_ROW = 4


class CollectionChannel(Channel):
    number = 4
    name = "IN YOUR COLLECTION"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._ver = -1
        self._sections: list[tuple[str, list[Release]]] = []
        self._sec = 0
        self._t = 0.0

    def _build(self, rel: Release) -> list[tuple[str, list[Release]]]:
        out = []
        if rel.release_id <= 0:
            return [(f"{rel.artist.upper()} IS NOT IN YOUR COLLECTION", [])]
        con = discogs.open_db()
        q = lambda sql, *a: [Release.from_row(r) for r in con.execute(sql, a)]
        more = q("SELECT * FROM releases WHERE artist=? AND release_id!=? ORDER BY year", rel.artist, rel.release_id)
        if more:
            out.append((f"MORE BY {rel.artist.upper()}", more))
        if rel.label:
            lab = rel.label.split(",")[0].strip()
            y = rel.year or 0
            same = q("SELECT * FROM releases WHERE label LIKE ? AND release_id!=? AND artist!=? "
                     "ORDER BY ABS(COALESCE(year,0)-?) LIMIT 12", f"%{lab}%", rel.release_id, rel.artist, y)
            if same:
                out.append((f"ALSO ON {lab.upper()}", same))
        styles = [s.strip() for s in (rel.styles or rel.genres or "").split(",") if s.strip()]
        for st in styles[:2]:
            rows = q("SELECT * FROM releases WHERE (styles LIKE ? OR genres LIKE ?) AND release_id!=? AND artist!=? "
                     "ORDER BY RANDOM() LIMIT 12", f"%{st}%", f"%{st}%", rel.release_id, rel.artist)
            if rows:
                out.append((f"MORE {st.upper()}", rows))
        con.close()
        if not out:
            out.append(("NOTHING RELATED", []))
        return out

    def update(self, dt: float) -> None:
        self._t += dt
        if self.now.version != self._ver:
            self._ver = self.now.version
            self._sections, self._sec, self._t = [], 0, 0.0
            if self.now.release:
                try:
                    self._sections = self._build(self.now.release)
                except Exception as e:  # noqa: BLE001
                    log.warning("collection build: %s", e)
        if self._sections and self._t > SECTION_SECONDS:
            self._t = 0.0
            self._sec = (self._sec + 1) % len(self._sections)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        d.backdrop(surface, self.now.release.cover_path if self.now.release else None, fill=D.NAVY)
        bar = self.header(surface, self.now_line())
        safe = d.safe
        if not self._sections:
            d.text("NOTHING PLAYING", "md", D.GREY, (safe.left, bar.bottom + 20))
            return
        title, rels = self._sections[self._sec]
        y = bar.bottom + 14
        d.card(surface, pygame.Rect(safe.left - 8, y - 6, safe.width + 16, safe.bottom - y), alpha=120)
        d.text(d.fit_text(title, "md", safe.width), "md", D.YELLOW, (safe.left, y))
        y += 44
        # two rows of PER_ROW thumbs, page through if more
        page_n = PER_ROW * 2
        pages = max(1, (len(rels) + page_n - 1) // page_n)
        page = int(self._t / SECTION_SECONDS * pages) % pages if pages > 1 else 0
        rows = rels[page * page_n:(page + 1) * page_n]
        gap = (safe.width - PER_ROW * THUMB) // (PER_ROW - 1)
        for i, r in enumerate(rows):
            col, row = i % PER_ROW, i // PER_ROW
            x = safe.left + col * (THUMB + gap)
            yy = y + row * (THUMB + 62)
            img = thumb(r.cover_path, THUMB)
            rect = img.get_rect(topleft=(x, yy))
            if d.modern:
                d.shadowed(surface, img, rect, radius=6)
            else:
                pygame.draw.rect(surface, D.BLACK, rect.inflate(4, 4))
                surface.blit(img, rect)
            cap = d.fit_text(r.title, "sm", THUMB + gap - 8)
            d.text(cap, "sm", D.WHITE, (x, yy + THUMB + 2), shadow=False)
            sub = str(r.year or "") if r.artist == self.now.release.artist else d.fit_text(r.artist, "sm", THUMB + gap - 8)
            d.text(sub, "sm", D.GREY, (x, yy + THUMB + 28), shadow=False)
        if pages > 1:
            d.text(f"{page + 1}/{pages}", "sm", D.CYAN, (safe.right, bar.bottom + 18), anchor="topright")
