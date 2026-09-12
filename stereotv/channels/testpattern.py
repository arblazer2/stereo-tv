"""Channel 16: Color bars & test pattern — SMPTE bars, then a convergence grid. Also a CRT tuning aid."""
from __future__ import annotations

import math
import time

import pygame

from stereotv import display as D
from stereotv.channels.base import Channel

PAGE_S = 20.0
BARS = [(192, 192, 192), (192, 192, 0), (0, 192, 192), (0, 192, 0), (192, 0, 192), (192, 0, 0), (0, 0, 192)]


class TestPatternChannel(Channel):
    number = 16
    name = "TEST PATTERN"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._t = 0.0
        self._page = 0
        self._bars = self._make_bars()
        self._grid = self._make_grid()

    def _make_bars(self) -> pygame.Surface:
        w, h = self.d.w, self.d.h
        s = pygame.Surface((w, h))
        n = len(BARS)
        top = int(h * 0.67)
        for i, c in enumerate(BARS):
            s.fill(c, (i * w // n, 0, (i + 1) * w // n - i * w // n, top))
        # reverse-ish castellations
        cast = [(0, 0, 192), (19, 19, 19), (192, 0, 192), (19, 19, 19), (0, 192, 192), (19, 19, 19), (192, 192, 192)]
        mid = int(h * 0.75)
        for i, c in enumerate(cast):
            s.fill(c, (i * w // n, top, (i + 1) * w // n - i * w // n, mid - top))
        # bottom: -I, white, +Q, black, PLUGE
        bw = w * 5 // 28
        s.fill((0, 33, 76), (0, mid, bw, h - mid))
        s.fill((255, 255, 255), (bw, mid, bw, h - mid))
        s.fill((50, 0, 106), (2 * bw, mid, bw, h - mid))
        s.fill((19, 19, 19), (3 * bw, mid, w - 3 * bw, h - mid))
        pw = (w - 5 * bw) // 3
        x = 5 * bw
        for c in ((9, 9, 9), (19, 19, 19), (29, 29, 29)):
            s.fill(c, (x, mid, pw, h - mid)); x += pw
        return s

    def _make_grid(self) -> pygame.Surface:
        w, h = self.d.w, self.d.h
        s = pygame.Surface((w, h))
        s.fill(D.BLACK)
        for x in range(0, w + 1, 40):
            pygame.draw.line(s, (200, 200, 200), (x, 0), (x, h), 2)
        for y in range(0, h + 1, 40):
            pygame.draw.line(s, (200, 200, 200), (0, y), (w, y), 2)
        cx, cy = w // 2, h // 2
        pygame.draw.circle(s, D.WHITE, (cx, cy), h // 2 - 4, 2)
        pygame.draw.circle(s, D.WHITE, (cx, cy), h // 4, 2)
        pygame.draw.line(s, D.WHITE, (cx, 0), (cx, h), 2)
        pygame.draw.line(s, D.WHITE, (0, cy), (w, cy), 2)
        # corner circles show geometry/overscan
        for px, py in ((60, 60), (w - 60, 60), (60, h - 60), (w - 60, h - 60)):
            pygame.draw.circle(s, D.WHITE, (px, py), 40, 2)
        # safe-area rectangle
        pygame.draw.rect(s, D.AMBER, self.d.safe, 2)
        # greyscale ramp (bottom, inside safe area)
        for i in range(10):
            g = int(i * 255 / 9)
            s.fill((g, g, g), (cx - 200 + i * 40, self.d.safe.bottom - 50, 40, 36))
        return s

    def update(self, dt: float) -> None:
        self._t += dt
        if self._t > PAGE_S:
            self._t = 0.0
            self._page ^= 1

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        if self._page == 0:
            surface.blit(self._bars, (0, 0))
            box = pygame.Rect(0, 0, 360, 74)
            box.center = (d.w // 2, int(d.h * 0.45))
            pygame.draw.rect(surface, D.BLACK, box)
            pygame.draw.rect(surface, D.WHITE, box, 2)
            d.text("PLEASE STAND BY", "md", D.WHITE, (box.centerx, box.centery - 12), anchor="center", shadow=False)
            d.text(time.strftime("%I:%M:%S %p").lstrip("0"), "sm", D.GREY, (box.centerx, box.centery + 18), anchor="center", shadow=False)
        else:
            surface.blit(self._grid, (0, 0))
            lab = pygame.Rect(0, 0, 300, 40); lab.center = (d.w // 2, d.h // 2 - 60)
            pygame.draw.rect(surface, D.BLACK, lab)
            d.text("640 × 480 · SAFE AREA", "sm", D.AMBER, lab.center, anchor="center", shadow=False)
            clk = pygame.Rect(0, 0, 150, 40); clk.center = (d.w // 2, d.h // 2 + 30)
            pygame.draw.rect(surface, D.BLACK, clk)
            d.text(time.strftime("%I:%M %p").lstrip("0"), "mono", D.WHITE, clk.center, anchor="center", shadow=False)
