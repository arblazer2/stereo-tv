from __future__ import annotations

import logging

import pygame

from stereotv import display as D
from stereotv.display import Display
from stereotv.state import NowPlaying

log = logging.getLogger("stereotv.channels")

_thumbs: dict[tuple[str, int], pygame.Surface] = {}


def thumb(path: str | None, size: int) -> pygame.Surface:
    """Scaled cover (cached). Placeholder if missing."""
    key = (path or "", size)
    s = _thumbs.get(key)
    if s is None:
        s = None
        if path:
            try:
                img = pygame.image.load(path).convert()
                w, h = img.get_size()
                k = size / max(w, h)
                s = pygame.transform.smoothscale(img, (max(1, int(w * k)), max(1, int(h * k))))
            except Exception as e:  # noqa: BLE001
                log.debug("thumb %s: %s", path, e)
        if s is None:
            s = pygame.Surface((size, size))
            s.fill((40, 40, 50))
            pygame.draw.circle(s, (20, 20, 25), (size // 2, size // 2), size // 2 - 4)
            pygame.draw.circle(s, D.AMBER, (size // 2, size // 2), size // 7)
        if len(_thumbs) > 200:
            _thumbs.clear()
        _thumbs[key] = s
    return s


class Channel:
    number: int = 0
    name: str = "STATIC"

    def __init__(self, display: Display, now: NowPlaying):
        self.d = display
        self.now = now

    def enter(self) -> None:
        """Called when the dial lands on this channel."""

    def leave(self) -> None:
        pass

    def update(self, dt: float) -> None:
        pass

    def draw(self, surface: pygame.Surface) -> None:
        raise NotImplementedError

    # ------------------------------------------------------------ shared chrome
    def header(self, surface: pygame.Surface, right: str = "", color=None) -> pygame.Rect:
        d = self.d
        color = D.YELLOW if color is None else color
        if d.modern:
            # minimal: small-caps label top-left, context top-right, thin rule; no bar
            bar = pygame.Rect(0, d.safe.top, d.w, 40)
            d.text(f"{self.number:02d}", "sm_bold", D.GREY, (d.safe.left, bar.top + 4), shadow=False)
            left = d.text(self.name, "sm_bold", D.WHITE, (d.safe.left + 44, bar.top + 4), shadow=False)
            room = d.safe.right - left.right - 24
            if right and room > 80:
                d.text(d.fit_text(right, "xs", room), "xs", D.GREY, (d.safe.right, bar.top + 8), anchor="topright", shadow=False)
            pygame.draw.rect(surface, D.BLUE, (d.safe.left, bar.bottom - 4, d.safe.width, 2))
            return bar
        bar = pygame.Rect(0, d.safe.top, d.w, 44)
        pygame.draw.rect(surface, D.BLUE, bar)
        pygame.draw.rect(surface, D.CYAN, bar.inflate(0, 4), 2)
        left = d.text(f"{self.number:02d}  {self.name}", "md", D.WHITE, (d.safe.left + 8, bar.centery), anchor="midleft")
        room = d.safe.right - 8 - left.right - 24
        if right and room > 80:
            d.text(d.fit_text(right, "sm", room), "sm", color, (d.safe.right - 8, bar.centery), anchor="midright")
        return bar

    def now_line(self) -> str:
        rel = self.now.release
        return f"{rel.artist} · {rel.title}" if rel else ""
