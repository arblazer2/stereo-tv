"""Channel 11: Weather — current conditions, extended forecast, and the radar loop, cycling."""
from __future__ import annotations

import pygame

from stereotv import display as D
from stereotv.channels.base import Channel

PAGE_S = 12.0


class WeatherChannel(Channel):
    number = 11
    name = "WEATHER"

    def __init__(self, display, now, cfg: dict, radar=None):
        super().__init__(display, now)
        from stereotv.screensaver import WeatherSaver   # lazy: screensaver imports channels.base
        self.saver = WeatherSaver(display, now, cfg)
        self.radar = radar
        self._t = 0.0
        self._page = 0

    def enter(self) -> None:
        self._t, self._page = 0.0, 0
        self.saver.cache.get()
        if self.radar:
            self.radar.enter()

    def _pages(self) -> int:
        return 3 if self.radar else 2

    def update(self, dt: float) -> None:
        self._t += dt
        if self._t >= PAGE_S:
            self._t = 0.0
            self._page = (self._page + 1) % self._pages()
        if self.radar:
            self.radar.update(dt)

    def draw(self, surface: pygame.Surface) -> None:
        if self._page == 2 and self.radar:
            self.radar.draw(surface)
            return
        self.saver._page = self._page
        self.saver.draw(surface)
        d = self.d
        for i in range(self._pages()):
            c = D.CYAN if i == self._page else D.GREY
            pygame.draw.circle(surface, c, (d.w // 2 - (self._pages() - 1) * 8 + i * 16, d.safe.bottom - 42), 4)
