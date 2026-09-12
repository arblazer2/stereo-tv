from __future__ import annotations

import pygame

from stereotv.channels.base import Channel


class StaticChannel(Channel):
    """Empty channel: snow."""
    name = "STATIC"

    def __init__(self, display, now, number: int):
        super().__init__(display, now)
        self.number = number

    def draw(self, surface: pygame.Surface) -> None:
        self.d.snow(surface)
