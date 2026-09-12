"""Channel 1: Now Playing — cover art + release info."""
from __future__ import annotations

import logging
import math

import pygame

from stereotv import display as D
from stereotv.channels.base import Channel

log = logging.getLogger("stereotv.ch.now_playing")

COVER = 300  # px square


class NowPlayingChannel(Channel):
    number = 1
    name = "NOW PLAYING"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._ver = -1
        self._rid = None
        self._cover: pygame.Surface | None = None
        self._t = 0.0

    # ------------------------------------------------------------
    def _load_cover(self) -> None:
        self._cover = None
        rel = self.now.release
        if rel and rel.cover_path:
            try:
                img = pygame.image.load(rel.cover_path).convert()
                w, h = img.get_size()
                scale = COVER / max(w, h)
                img = pygame.transform.smoothscale(img, (int(w * scale), int(h * scale)))
                self._cover = img
            except Exception as e:  # noqa: BLE001
                log.warning("cover load failed %s: %s", rel.cover_path, e)
        if self._cover is None:
            self._cover = self._placeholder()

    def _placeholder(self) -> pygame.Surface:
        s = pygame.Surface((COVER, COVER))
        s.fill((40, 40, 50))
        pygame.draw.circle(s, (20, 20, 25), (COVER // 2, COVER // 2), COVER // 2 - 10)
        pygame.draw.circle(s, (70, 70, 80), (COVER // 2, COVER // 2), COVER // 2 - 10, 4)
        pygame.draw.circle(s, D.AMBER, (COVER // 2, COVER // 2), 42)
        pygame.draw.circle(s, (20, 20, 25), (COVER // 2, COVER // 2), 6)
        return s

    def update(self, dt: float) -> None:
        self._t += dt
        if self.now.version != self._ver:
            self._ver = self.now.version
            rid = self.now.release.release_id if self.now.release else None
            if rid != self._rid:
                self._rid = rid
                self._load_cover()

    # ------------------------------------------------------------
    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        if d.modern:
            self._draw_modern(surface)
            return
        surface.fill(D.NAVY)
        safe = d.safe

        src = self.now.source.upper()
        if self.now.status:
            src = f"{src} · {self.now.status}"
        bar = self.header(surface, src)

        rel = self.now.release
        top = bar.bottom + 16
        if rel is None:
            self._draw_idle(surface, top)
            return

        # cover art (left)
        if self._cover:
            cr = self._cover.get_rect(topleft=(safe.left, top))
            d.shadowed(surface, self._cover, cr)
        else:
            cr = pygame.Rect(safe.left, top, COVER, COVER)

        # text block (right)
        x = cr.right + 24
        maxw = safe.right - x
        y = top
        afont, astep = "lg", 40
        artist_lines = d.wrap(rel.artist, afont, maxw)
        if len(artist_lines) > 2:
            afont, astep = "md", 32
            artist_lines = d.wrap(rel.artist, afont, maxw)[:3]
        for ln in artist_lines:
            d.text(d.fit_text(ln, afont, maxw), afont, D.YELLOW, (x, y)); y += astep
        y += 6
        for ln in d.wrap(rel.title, "md", maxw)[:3]:
            d.text(d.fit_text(ln, "md", maxw), "md", D.WHITE, (x, y)); y += 32
        y += 10
        if rel.year:
            d.text(str(rel.year), "mono", D.CYAN, (x, y)); y += 30
        if rel.label:
            lab = rel.label + (f"  {rel.catno}" if rel.catno else "")
            d.text(d.fit_text(lab, "sm", maxw), "sm", D.GREY, (x, y)); y += 28
        if rel.formats:
            col = D.AMBER if rel.release_id <= 0 else D.GREY
            d.text(d.fit_text(rel.formats, "sm", maxw), "sm", col, (x, y)); y += 28
        if self.now.side or self.now.track:
            st = " ".join(p for p in (f"SIDE {self.now.side}" if self.now.side else "",
                                      f"TRK {self.now.track}" if self.now.track else "") if p)
            d.text(st, "mono", D.AMBER, (x, y)); y += 30
        if self.now.track_title:
            for ln in d.wrap(self.now.track_title, "sm", maxw)[:2]:
                d.text(ln, "sm", D.AMBER, (x, y)); y += 26

        # footer: genre/styles
        fy = safe.bottom - 30
        g = rel.styles or rel.genres
        if g:
            d.text(d.fit_text(g, "sm", safe.width), "sm", D.CYAN, (safe.left, fy))

        # blinking "play" indicator
        if math.sin(self._t * 3) > 0:
            pygame.draw.polygon(surface, D.GREEN,
                                [(safe.right - 26, fy), (safe.right - 26, fy + 22), (safe.right - 6, fy + 11)])


    def _draw_idle(self, surface: pygame.Surface, top: int) -> None:
        import time as _t
        d = self.d
        safe = d.safe
        cx = d.w // 2
        # a resting tonearm + platter, drawn simply
        pygame.draw.circle(surface, (25, 25, 35), (cx, top + 120), 96)
        pygame.draw.circle(surface, (60, 60, 75), (cx, top + 120), 96, 3)
        pygame.draw.circle(surface, D.AMBER, (cx, top + 120), 30)
        pygame.draw.circle(surface, (25, 25, 35), (cx, top + 120), 5)
        pygame.draw.line(surface, (140, 140, 150), (cx + 150, top + 20), (cx + 120, top + 60), 5)
        pygame.draw.circle(surface, (140, 140, 150), (cx + 150, top + 20), 9)
        y = top + 240
        d.text("NOTHING ON THE TURNTABLE", "md", D.WHITE, (cx, y), anchor="midtop"); y += 36
        st = self.now.status or ""
        hint = {"NO LINE-IN": "line-in not connected", "SILENCE": "listening for a needle drop",
                "NO RECOGNIZER": "recognizer unavailable"}.get(st, st.lower() if st else "")
        if hint:
            d.text(hint, "sm", D.GREY, (cx, y), anchor="midtop"); y += 30
        lp = self.now.last_played
        if lp:
            when = _t.strftime("%I:%M %p", _t.localtime(self.now.last_played_at)).lstrip("0")
            d.text(d.fit_text(f"LAST PLAYED · {lp.artist} · {lp.title} · {when}", "sm", safe.width), "sm", D.CYAN,
                   (cx, safe.bottom - 30), anchor="midtop")


    def _draw_modern(self, surface: pygame.Surface) -> None:
        d = self.d
        safe = d.safe
        rel = self.now.release
        d.backdrop(surface, rel.cover_path if rel else None)
        src = self.now.source.upper()
        if self.now.status:
            src = f"{src} · {self.now.status}"
        bar = self.header(surface, src)
        top = bar.bottom + 14
        if rel is None:
            self._draw_idle(surface, top)
            return
        # big art with a soft shadow, rounded
        if self._cover:
            cr = self._cover.get_rect(topleft=(safe.left, top + 6))
            d.shadowed(surface, self._cover, cr, radius=8)
        else:
            cr = pygame.Rect(safe.left, top, COVER, COVER)
        x = cr.right + 26
        maxw = safe.right - x
        y = top + 4
        afont, astep = "lg", 40
        lines = d.wrap(rel.artist, afont, maxw)
        if len(lines) > 2:
            afont, astep = "md", 32
            lines = d.wrap(rel.artist, afont, maxw)[:3]
        for ln in lines:
            d.text(d.fit_text(ln, afont, maxw), afont, D.WHITE, (x, y), shadow=False); y += astep
        y += 4
        for ln in d.wrap(rel.title, "md", maxw)[:2]:
            d.text(d.fit_text(ln, "md", maxw), "md", D.GREY, (x, y), shadow=False); y += 32
        y += 10
        meta = " · ".join(p for p in (str(rel.year or ""), rel.label.split(",")[0]) if p)
        if meta:
            d.text(d.fit_text(meta, "sm", maxw), "sm", D.GREY, (x, y), shadow=False); y += 30
        if self.now.track_title:
            # pill with the current track
            label = " ".join(p for p in ((f"{self.now.side}{self.now.track}" if self.now.side else ""), self.now.track_title) if p)
            label = d.fit_text(label, "sm", maxw - 24)
            w = d.fonts["sm"].size(label)[0] + 24
            pill = pygame.Rect(x, y + 4, w, 34)
            d.panel(surface, pill, D.BLUE, radius=17)
            d.text(label, "sm", D.WHITE, pill.center, anchor="center", shadow=False)
            y += 44
        g = rel.styles or rel.genres
        if g:
            d.text(d.fit_text(g, "xs", safe.width), "xs", D.GREY, (safe.left, safe.bottom - 24), shadow=False)
        if math.sin(self._t * 3) > 0:
            pygame.draw.circle(surface, D.GREEN, (safe.right - 10, safe.bottom - 14), 6)
