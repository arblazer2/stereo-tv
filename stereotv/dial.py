"""Channel-dial input. Phase 1: keyboard stand-in. Later: MCP3008 ADC or rotary encoder."""
from __future__ import annotations

import pygame


class Dial:
    """Yields channel deltas (+1/-1) or absolute channel numbers from events."""

    def poll(self, events: list[pygame.event.Event]) -> tuple[int, int | None]:
        """Return (delta, absolute). absolute is a channel number if a digit was pressed."""
        return 0, None


class KeyboardDial(Dial):
    def poll(self, events):
        delta, absolute = 0, None
        for e in events:
            if e.type != pygame.KEYDOWN:
                continue
            if e.key in (pygame.K_RIGHT, pygame.K_UP, pygame.K_PAGEUP, pygame.K_PLUS, pygame.K_EQUALS):
                delta += 1
            elif e.key in (pygame.K_LEFT, pygame.K_DOWN, pygame.K_PAGEDOWN, pygame.K_MINUS):
                delta -= 1
            elif pygame.K_1 <= e.key <= pygame.K_9:  # 1-9 direct
                absolute = e.key - pygame.K_0
            elif e.key == pygame.K_0:
                absolute = 10
        return delta, absolute


def make(mode: str) -> Dial:
    if mode == "keyboard":
        return KeyboardDial()
    raise NotImplementedError(f"dial mode {mode!r} not implemented yet")
