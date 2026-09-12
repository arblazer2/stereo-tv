"""Channel 7: Collection Stats — by decade, top artists / labels / styles, totals. Pages cycle."""
from __future__ import annotations

import logging
import time
from collections import Counter

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import Channel

log = logging.getLogger("stereotv.ch.stats")

PAGE_S = 12.0
RELOAD_S = 3600


class StatsChannel(Channel):
    number = 7
    name = "COLLECTION STATS"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._pages: list[tuple[str, str, list[tuple[str, int]]]] = []   # (title, kind, rows)
        self._page = 0
        self._t = 0.0
        self._loaded = 0.0

    def _load(self) -> None:
        con = discogs.open_db()
        rows = con.execute("SELECT artist, year, label, styles, genres, date_added, title FROM releases").fetchall()
        con.close()
        n = len(rows)
        decades = Counter()
        artists, labels, styles = Counter(), Counter(), Counter()
        years = [r["year"] for r in rows if r["year"]]
        for r in rows:
            if r["year"]:
                decades[f"{r['year'] // 10 * 10}s"] += 1
            if r["artist"].lower() not in ("various", "various artists"):
                artists[r["artist"]] += 1
            for lab in (r["label"] or "").split(","):
                lab = lab.strip()
                if lab:
                    labels[lab] += 1
            for st in (r["styles"] or "").split(","):
                st = st.strip()
                if st:
                    styles[st] += 1
        pages = []
        pages.append(("RECORDS BY DECADE", "bars", sorted(decades.items())))
        pages.append(("MOST COLLECTED ARTISTS", "bars", artists.most_common(8)))
        pages.append(("TOP LABELS", "bars", labels.most_common(8)))
        pages.append(("TOP STYLES", "bars", styles.most_common(8)))
        newest = sorted((r for r in rows if r["date_added"]), key=lambda r: r["date_added"], reverse=True)[:6]
        oldest = min((r for r in rows if r["year"]), key=lambda r: r["year"], default=None)
        facts = [("RECORDS", n), ("ARTISTS", len(artists)), ("LABELS", len(labels)),
                 ("AVERAGE YEAR", round(sum(years) / len(years)) if years else 0)]
        if oldest:
            facts.append((f"OLDEST · {oldest['artist']} · {oldest['title']}", oldest["year"]))
        pages.append(("BY THE NUMBERS", "facts", facts))
        pages.append(("NEWEST ARRIVALS", "list", [(f"{r['artist']} · {r['title']}", r["year"] or 0) for r in newest]))
        self._pages = pages
        self._loaded = time.monotonic()

    def enter(self) -> None:
        if not self._pages or time.monotonic() - self._loaded > RELOAD_S:
            try:
                self._load()
            except Exception as e:  # noqa: BLE001
                log.warning("stats: %s", e)
        self._page, self._t = 0, 0.0

    def update(self, dt: float) -> None:
        self._t += dt
        if self._pages and self._t > PAGE_S:
            self._t = 0.0
            self._page = (self._page + 1) % len(self._pages)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.DARK)
        bar = self.header(surface, self.now_line())
        safe = d.safe
        if not self._pages:
            d.text("NO DATA", "md", D.GREY, (safe.left, bar.bottom + 20))
            return
        title, kind, rows = self._pages[self._page]
        y = bar.bottom + 14
        d.text(title, "md", D.YELLOW, (safe.left, y)); y += 40
        if kind == "bars" and rows:
            top = max(v for _, v in rows) or 1
            row_h = min(34, (safe.bottom - y - 10) // max(1, len(rows)))
            labw = 190
            for label, v in rows:
                d.text(d.fit_text(str(label), "sm", labw - 8), "sm", D.WHITE, (safe.left, y), shadow=False)
                w = int((safe.width - labw - 60) * v / top)
                d.panel(surface, pygame.Rect(safe.left + labw, y + 2, max(w, 4), row_h - 8), D.BLUE, None if d.modern else D.CYAN, radius=6 if d.modern else 0)
                d.text(str(v), "sm", D.CYAN, (safe.left + labw + w + 8, y), shadow=False)
                y += row_h
        elif kind == "facts":
            for label, v in rows:
                d.text(d.fit_text(str(label), "sm", safe.width - 120), "sm", D.GREY, (safe.left, y), shadow=False)
                d.text(str(v), "md", D.WHITE, (safe.right, y - 4), anchor="topright")
                y += 44
        else:
            for label, v in rows:
                d.text(d.fit_text(str(label), "sm", safe.width - 80), "sm", D.WHITE, (safe.left, y), shadow=False)
                d.text(str(v or ""), "sm", D.CYAN, (safe.right, y), anchor="topright", shadow=False)
                y += 32
        for i in range(len(self._pages)):
            c = D.CYAN if i == self._page else D.GREY
            pygame.draw.circle(surface, c, (safe.right - 12 - (len(self._pages) - 1 - i) * 16, safe.bottom - 8), 4)
