"""Channel 8: Random Spin — slot-machine picker. Enter re-spins, P plays the pick (manual override)."""
from __future__ import annotations

import logging
import random

import pygame

from stereotv import discogs
from stereotv import display as D
from stereotv.channels.base import Channel, thumb
from stereotv.state import Release

log = logging.getLogger("stereotv.ch.spin")

SIZE = 220
SPIN_S = 3.2


class RandomSpinChannel(Channel):
    number = 8
    name = "RANDOM SPIN"

    def __init__(self, display, now, override_minutes: float = 30):
        super().__init__(display, now)
        self.override_minutes = override_minutes
        self.pool: list[Release] = []
        self.pick: Release | None = None
        self.shown: Release | None = None
        self._spin_t = 0.0
        self._next_flip = 0.0
        self._spinning = False
        self._played = False

    def _refill(self) -> None:
        con = discogs.open_db()
        rows = con.execute("SELECT * FROM releases WHERE cover_path IS NOT NULL ORDER BY RANDOM() LIMIT 40").fetchall()
        con.close()
        self.pool = [Release.from_row(r) for r in rows]

    def enter(self) -> None:
        self.spin()

    def spin(self) -> None:
        try:
            self._refill()
        except Exception as e:  # noqa: BLE001
            log.warning("spin: %s", e)
            self.pool = []
        if not self.pool:
            return
        self.pick = self.pool[-1]
        self._spin_t, self._next_flip = 0.0, 0.0
        self._spinning, self._played = True, False
        log.info("spin -> %s - %s", self.pick.artist, self.pick.title)

    def play(self) -> None:
        if self.pick and not self._spinning:
            self.now.set(self.pick, source="manual", confidence=1.0, override_minutes=self.override_minutes)
            self._played = True
            log.info("spin played: %s - %s", self.pick.artist, self.pick.title)

    def handle_key(self, key: int) -> bool:
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.spin()
            return True
        if key == pygame.K_p:
            self.play()
            return True
        return False

    def update(self, dt: float) -> None:
        if not self._spinning or not self.pool:
            return
        self._spin_t += dt
        k = self._spin_t / SPIN_S
        if k >= 1.0:
            self._spinning = False
            self.shown = self.pick
            return
        # flip interval grows: fast at first, slows down (ease-out)
        interval = 0.05 + 0.45 * k * k
        if self._spin_t >= self._next_flip:
            self._next_flip = self._spin_t + interval
            self.shown = random.choice(self.pool[:-1]) if k < 0.85 else self.pick

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.DARK)
        bar = self.header(surface, "ENTER = SPIN · P = PLAY IT", color=D.CYAN)
        safe = d.safe
        rel = self.shown
        if not rel:
            d.text("NO COVERS YET", "md", D.GREY, (safe.left, bar.bottom + 20))
            return
        img = thumb(rel.cover_path, SIZE)
        r = img.get_rect(midtop=(d.w // 2, bar.bottom + 18))
        pygame.draw.rect(surface, D.BLACK, r.inflate(10, 10))
        surface.blit(img, r)
        frame_col = D.YELLOW if not self._spinning else (90, 60, 140)
        pygame.draw.rect(surface, frame_col, r.inflate(10, 10), 3)
        y = r.bottom + 14
        if self._spinning:
            d.text("SPINNING…", "md", (170, 140, 220), (d.w // 2, y), anchor="midtop")
            return
        d.text(d.fit_text(rel.artist, "md", safe.width), "md", D.YELLOW, (d.w // 2, y), anchor="midtop"); y += 34
        d.text(d.fit_text(rel.title, "md", safe.width), "md", D.WHITE, (d.w // 2, y), anchor="midtop"); y += 34
        meta = " · ".join(p for p in (str(rel.year or ""), rel.label.split(",")[0], rel.styles.split(",")[0]) if p)
        d.text(d.fit_text(meta, "sm", safe.width), "sm", D.GREY, (d.w // 2, y), anchor="midtop"); y += 30
        if self._played:
            d.text("NOW PLAYING (MANUAL)", "sm", D.GREEN, (d.w // 2, y), anchor="midtop")
        else:
            d.text("GO PULL IT", "sm", D.AMBER, (d.w // 2, y), anchor="midtop")
