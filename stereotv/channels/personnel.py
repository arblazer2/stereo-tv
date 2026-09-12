"""Channel 9: Personnel — who played on the current record (Discogs credits), grouped by role."""
from __future__ import annotations

import json
import logging
import re
from collections import OrderedDict

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import Channel, thumb

log = logging.getLogger("stereotv.ch.personnel")

PAGE_S = 12.0
ROW_H = 26
# roles worth leading with, in this order
PRIORITY = ["Vocals", "Lead Vocals", "Guitar", "Lead Guitar", "Rhythm Guitar", "Bass", "Drums", "Piano", "Keyboards",
            "Organ", "Synthesizer", "Saxophone", "Trumpet", "Harmonica", "Percussion", "Backing Vocals", "Strings",
            "Producer", "Engineer", "Mixed By", "Mastered By", "Written-By", "Arranged By", "Photography By",
            "Design", "Artwork", "Cover", "Liner Notes"]


def _norm_role(role: str) -> str:
    r = re.sub(r"\s*\[.*?\]", "", role).strip()          # "Guitar [Lead]" -> "Guitar"
    return r or "Credits"


def group_credits(credits: list[dict]) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = OrderedDict()
    for c in credits:
        for role in re.split(r",\s*(?![^\[]*\])", c.get("role", "")):     # split "Bass, Vocals" but not inside [..]
            role = _norm_role(role)
            name = c["name"]
            groups.setdefault(role, [])
            if name not in groups[role]:
                groups[role].append(name)
    def rank(item):
        role = item[0]
        for i, p in enumerate(PRIORITY):
            if role.lower().startswith(p.lower()):
                return i
        return len(PRIORITY) + (0 if len(item[1]) > 1 else 1)
    return sorted(groups.items(), key=rank)


class PersonnelChannel(Channel):
    number = 9
    name = "PERSONNEL"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._rid = None
        self._pages: list[list[tuple[str, str]]] = []
        self._page = 0
        self._t = 0.0

    def _load(self, rel) -> None:
        self._pages, self._page, self._t = [], 0, 0.0
        if rel.release_id <= 0:
            return
        con = discogs.open_db()
        row = con.execute("SELECT credits FROM releases WHERE release_id=?", (rel.release_id,)).fetchone()
        con.close()
        credits = json.loads(row["credits"]) if row and row["credits"] else []
        lines: list[tuple[str, str]] = []
        maxw = self.d.safe.width - 190
        for role, names in group_credits(credits):
            text = ", ".join(names)
            wrapped = self.d.wrap(text, "sm", maxw)
            for i, ln in enumerate(wrapped):
                lines.append((role.upper() if i == 0 else "", ln))
        per = (self.d.safe.height - 44 - 60 - 20) // ROW_H
        self._pages = [lines[i:i + per] for i in range(0, len(lines), per)]

    def update(self, dt: float) -> None:
        rel = self.now.release
        rid = rel.release_id if rel else None
        if rid != self._rid:
            self._rid = rid
            if rel:
                try:
                    self._load(rel)
                except Exception as e:  # noqa: BLE001
                    log.warning("personnel: %s", e)
        self._t += dt
        if len(self._pages) > 1 and self._t > PAGE_S:
            self._t = 0.0
            self._page = (self._page + 1) % len(self._pages)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.DARK)
        bar = self.header(surface, self.now_line())
        safe = d.safe
        rel = self.now.release
        if not rel:
            d.text("NOTHING PLAYING", "md", D.GREY, (safe.left, bar.bottom + 20))
            return
        img = thumb(rel.cover_path, 56)
        surface.blit(img, img.get_rect(topleft=(safe.left, bar.bottom + 12)))
        d.text(d.fit_text(rel.title.upper(), "md", safe.width - 70), "md", D.YELLOW, (safe.left + 68, bar.bottom + 12))
        d.text(f"{rel.year or ''}  ·  {rel.label}".strip(" ·"), "sm", D.GREY, (safe.left + 68, bar.bottom + 44), shadow=False)
        if not self._pages:
            d.text("NO CREDITS LISTED" if rel.release_id > 0 else "NOT IN COLLECTION", "md", D.GREY, (safe.left, bar.bottom + 90))
            return
        y = bar.bottom + 80
        for role, names in self._pages[self._page]:
            if role:
                d.text(d.fit_text(role, "sm", 180), "sm", D.CYAN, (safe.left, y), shadow=False)
            d.text(names, "sm", D.WHITE, (safe.left + 190, y), shadow=False)
            y += ROW_H
        n = len(self._pages)
        if n > 1:
            for i in range(n):
                c = D.CYAN if i == self._page else D.GREY
                pygame.draw.circle(surface, c, (safe.right - 12 - (n - 1 - i) * 16, safe.bottom - 8), 4)
