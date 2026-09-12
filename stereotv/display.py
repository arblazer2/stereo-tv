"""pygame init, 640x480 surface, fonts, CRT effects."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np
import pygame

log = logging.getLogger("stereotv.display")

# Palette: 80s cable box (the "cable88" theme). themes.apply() rewrites these at runtime.
BLACK = (0, 0, 0)
WHITE = (235, 235, 235)
GREY = (140, 140, 140)
DARK = (18, 18, 28)
NAVY = (12, 24, 72)
BLUE = (30, 60, 160)
CYAN = (90, 220, 240)
YELLOW = (250, 220, 60)
AMBER = (255, 170, 40)
RED = (220, 40, 40)
GREEN = (60, 200, 90)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
MONO_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def _font_path(cands: list[str]) -> str | None:
    for c in cands:
        if Path(c).exists():
            return c
    return None


SHADOW = True          # themes can switch drop shadows off (pixel fonts look wrong with them)
THEME = "cable88"


class Display:
    def __init__(self, width: int = 640, height: int = 480, fps: int = 30,
                 fullscreen: bool = True, scanlines: bool = True, sdl_debug: bool = False,
                 drift: bool = False, drift_px: int = 2, drift_seconds: float = 180.0, theme: str = "cable88"):
        self.w, self.h = width, height
        self.theme = theme
        self.fps = fps
        self.scanlines_on = scanlines
        # CRT burn-in guard: shift the whole picture by up to ±drift_px every drift_seconds
        self.drift = drift
        self.drift_px = drift_px
        self.drift_seconds = drift_seconds
        self._drift_off = (0, 0)
        self._drift_t = 0.0
        # no audio here; audio.py owns the line-in later
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
        # Pi has GLES2 (libGLESv2) but no desktop libGL; without this SDL's kmsdrm
        # backend picks the "opengl" renderer, fails to load libGL, and presents nothing.
        os.environ.setdefault("SDL_RENDER_DRIVER", "opengles2")
        os.environ.setdefault("SDL_OPENGL_ES_DRIVER", "1")
        if sdl_debug:
            os.environ["SDL_LOGGING"] = "*=verbose"
        pygame.display.init()
        pygame.font.init()
        pygame.mouse.set_visible(False)
        flags = 0
        if fullscreen and os.environ.get("SDL_VIDEODRIVER") != "dummy":
            flags |= pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((width, height), flags)
        pygame.display.set_caption("stereo-tv")
        log.info("video driver=%s mode=%s flags=%#x", pygame.display.get_driver(),
                 self.screen.get_size(), self.screen.get_flags())
        # Everything renders into a 640x480 surface; if the screen is a different
        # size we scale on flip (kmsdrm may hand us the panel's native mode).
        self.surface = self.screen if self.screen.get_size() == (width, height) and not drift \
            else pygame.Surface((width, height))
        self.clock = pygame.time.Clock()
        self.safe = pygame.Rect(int(width * 0.06), int(height * 0.06),
                                int(width * 0.88), int(height * 0.88))

        self.fonts: dict[str, pygame.font.Font] = {}
        self.apply_theme(theme)
        self._scan = self._make_scanlines()
        self._rng = np.random.default_rng()

    # ------------------------------------------------------------ themes
    def apply_theme(self, name: str) -> None:
        """Swap palette + fonts. Colours are module globals so every channel sees them."""
        from stereotv import themes
        t = themes.get(name)
        g = globals()
        for k, v in t["colors"].items():
            g[k] = v
        g["SHADOW"] = t.get("shadow", True)
        g["THEME"] = name if name in themes.THEMES else "cable88"
        self.theme = g["THEME"]
        fp = t["font"] if Path(t["font"]).exists() else _font_path(FONT_CANDIDATES)
        mp = t["mono"] if Path(t["mono"]).exists() else (_font_path(MONO_CANDIDATES) or fp)
        k = float(t.get("scale", 1.0))
        self.fonts = {
            "xl": pygame.font.Font(fp, int(48 * k)),
            "lg": pygame.font.Font(fp, int(36 * k)),
            "md": pygame.font.Font(fp, int(28 * k)),
            "sm": pygame.font.Font(fp, int(24 * k)),
            "mono": pygame.font.Font(mp, int(26 * k)),
            "mono_lg": pygame.font.Font(mp, int(40 * k)),
        }
        self.theme_version = getattr(self, "theme_version", 0) + 1
        log.info("theme %s", self.theme)

    # ------------------------------------------------------------ effects
    def _make_scanlines(self) -> pygame.Surface:
        # 2-px period, darken every other line a little. Applied with BLEND_RGB_SUB,
        # which is ~2x cheaper than an alpha blit on the Pi 3 (3.4 vs 6.5 ms).
        s = pygame.Surface((self.w, self.h))
        s.fill((0, 0, 0))
        for y in range(0, self.h, 2):
            pygame.draw.line(s, (40, 40, 40), (0, y), (self.w, y))
        return s

    def snow(self, target: pygame.Surface | None = None) -> None:
        """Draw TV static onto target (default: main surface). Cheap: 160x120 noise scaled up."""
        target = target or self.surface
        noise = self._rng.integers(0, 256, size=(160, 120), dtype=np.uint8)
        rgb = np.stack([noise, noise, noise], axis=-1)
        small = pygame.surfarray.make_surface(rgb)
        pygame.transform.scale(small, (self.w, self.h), target)

    # ------------------------------------------------------------ text helpers
    def text(self, s: str, font: str, color, pos, anchor: str = "topleft",
             shadow: bool = True, target: pygame.Surface | None = None) -> pygame.Rect:
        target = target or self.surface
        f = self.fonts[font]
        if shadow and SHADOW:
            sh = f.render(s, True, BLACK)
            r = sh.get_rect(**{anchor: (pos[0] + 2, pos[1] + 2)})
            target.blit(sh, r)
        img = f.render(s, True, color)
        r = img.get_rect(**{anchor: pos})
        target.blit(img, r)
        return r

    def fit_text(self, s: str, font: str, max_w: int) -> str:
        """Ellipsize s to fit max_w pixels in font."""
        f = self.fonts[font]
        if f.size(s)[0] <= max_w:
            return s
        while s and f.size(s + "…")[0] > max_w:
            s = s[:-1]
        return s.rstrip() + "…"

    def wrap(self, s: str, font: str, max_w: int) -> list[str]:
        f = self.fonts[font]
        words, lines, cur = s.split(), [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if f.size(t)[0] <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    # ------------------------------------------------------------ frame
    def flip(self) -> float:
        if self.scanlines_on:
            self.surface.blit(self._scan, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        if self.surface is not self.screen:
            if self.screen.get_size() != (self.w, self.h):
                pygame.transform.scale(self.surface, self.screen.get_size(), self.screen)
            else:
                if self._drift_off != (0, 0):
                    self.screen.fill(BLACK)
                self.screen.blit(self.surface, self._drift_off)
        pygame.display.flip()
        dt = self.clock.tick(self.fps) / 1000.0
        if self.drift:
            self._drift_t += dt
            if self._drift_t >= self.drift_seconds:
                self._drift_t = 0.0
                import random
                p = self.drift_px
                self._drift_off = (random.randint(-p, p), random.randint(-p, p))
        return dt

    def quit(self) -> None:
        pygame.quit()
