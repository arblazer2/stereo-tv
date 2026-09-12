"""Channel 3: Liner Notes — artist + album summaries (Wikipedia) and Discogs release notes."""
from __future__ import annotations

import logging
import re
import threading

import pygame

from stereotv import discogs, wiki
from stereotv import display as D
from stereotv.channels.base import Channel, thumb

log = logging.getLogger("stereotv.ch.liner")

PAGE_SECONDS = 14.0
LINE_H = 27


class LinerNotesChannel(Channel):
    number = 3
    name = "LINER NOTES"

    def __init__(self, display, now, user_agent: str):
        super().__init__(display, now)
        self.ua = user_agent
        self._ver = -1
        self._pages: list[tuple[str, list[str]]] = []   # (title, lines)
        self._page = 0
        self._t = 0.0
        self._loading = False
        self._pending: tuple[int, list[tuple[str, str]]] | None = None
        self._lock = threading.Lock()
        self._page_surf: pygame.Surface | None = None    # cached render of current page
        self._page_key = None

    def enter(self) -> None:
        self._ver = -1          # force refresh on entry (wiki may have landed meanwhile)

    # ------------------------------------------------------------ data
    def _fetch(self, rel) -> None:
        """Background: wiki (cached in DB) + discogs notes."""
        sections: list[tuple[str, str]] = []
        try:
            con = discogs.open_db()
            s = wiki.session(self.ua)
            a, b = wiki.for_release(con, s, rel)
            if a:
                sections.append((f"ABOUT {rel.artist.upper()}", a["extract"]))
            if b:
                sections.append((f"ABOUT {rel.title.upper()}", b["extract"]))
            row = con.execute("SELECT notes FROM releases WHERE release_id=?", (rel.release_id,)).fetchone()
            if row and row["notes"]:
                sections.append(("PRESSING NOTES", _clean_notes(row["notes"])))
            con.close()
        except Exception as e:  # noqa: BLE001
            log.warning("liner fetch: %s", e)
        with self._lock:
            self._pending = (rel.release_id, sections)
        self._loading = False

    def _paginate(self, sections: list[tuple[str, str]]) -> list[tuple[str, list[str]]]:
        maxw = self.d.safe.width - 150 - 16
        per_page = (self.d.safe.height - 44 - 16 - 40 - 30) // LINE_H
        pages = []
        for title, text in sections:
            lines: list[str] = []
            for para in re.split(r"\n+", text.strip()):
                lines += self.d.wrap(para, "sm", maxw) + [""]
            while lines and lines[-1] == "":
                lines.pop()
            for i in range(0, len(lines), per_page):
                chunk = lines[i:i + per_page]
                n = (len(lines) + per_page - 1) // per_page
                pages.append((title, chunk))   # page dots show position
        return pages or [("LINER NOTES", ["No notes found for this release."])]

    # ------------------------------------------------------------ loop
    def update(self, dt: float) -> None:
        self._t += dt
        if self.now.version != self._ver:
            self._ver = self.now.version
            self._pages, self._page, self._t = [], 0, 0.0
            rel = self.now.release
            if rel and not self._loading:
                self._loading = True
                threading.Thread(target=self._fetch, args=(rel,), daemon=True).start()
        with self._lock:
            pend, self._pending = self._pending, None
        if pend and self.now.release and pend[0] == self.now.release.release_id:
            self._pages = self._paginate(pend[1])
            self._page, self._t = 0, 0.0
        if self._pages and self._t > PAGE_SECONDS:
            self._t = 0.0
            self._page = (self._page + 1) % len(self._pages)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        rel = self.now.release
        d.backdrop(surface, rel.cover_path if rel else None)
        bar = self.header(surface, self.now_line())
        safe = d.safe
        top = bar.bottom + 16
        if rel:
            img = thumb(rel.cover_path, 150)
            r = img.get_rect(topleft=(safe.left, top))
            d.shadowed(surface, img, r, radius=6)
            y = r.bottom + 10
            d.text(str(rel.year or ""), "mono", D.CYAN, (safe.left, y)); y += 30
            for ln in d.wrap(rel.label, "sm", 150)[:2]:
                d.text(ln, "sm", D.GREY, (safe.left, y)); y += 26
        x = safe.left + 150 + 16
        d.card(surface, pygame.Rect(x - 10, top - 8, safe.right - x + 10, safe.bottom - top - 12))
        if not self._pages:
            d.text("LOADING…" if self._loading else "NOTHING PLAYING", "md", D.GREY, (x, top))
            return
        key = (self._ver, self._page)
        if self._page_key != key:
            # text rendering is the expensive part on the Pi: render the page once
            title, lines = self._pages[self._page]
            w, h = safe.right - x, safe.bottom - top - 20
            ps = pygame.Surface((w, h))          # opaque: plain copy blit, no per-pixel alpha
            ps.fill(D.DARK)
            d.text(d.fit_text(title, "md", w), "md", D.YELLOW, (0, 0), target=ps)
            y = 40
            for ln in lines:
                d.text(ln, "sm", D.WHITE, (0, y), shadow=False, target=ps)
                y += LINE_H
            self._page_surf, self._page_key = ps, key
        surface.blit(self._page_surf, (x, top))
        # page dots
        if len(self._pages) > 1:
            for i in range(len(self._pages)):
                c = D.CYAN if i == self._page else D.GREY
                pygame.draw.circle(surface, c, (safe.right - 12 - (len(self._pages) - 1 - i) * 16, safe.bottom - 8), 4)


def _clean_notes(s: str) -> str:
    s = re.sub(r"\[(/?[a-z]+)(=[^\]]*)?\]", "", s)         # discogs [b], [url=...] markup
    s = re.sub(r"\r\n?", "\n", s)
    return s.strip()
