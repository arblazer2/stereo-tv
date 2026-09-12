"""Channel 2: Visualizer — spectrum bars + oscilloscope from the live ring buffer."""
from __future__ import annotations

import math

import numpy as np
import pygame

from stereotv import display as D
from stereotv.channels.base import Channel

N_FFT = 2048
N_BARS = 32
F_LO, F_HI = 40.0, 16000.0
DB_FLOOR = -60.0     # dBFS mapped to bar height 0
DB_CEIL = -6.0       # dBFS mapped to full height


class VisualizerChannel(Channel):
    number = 2
    name = "VISUALIZER"

    def __init__(self, display, now, audio, gain_db: float = 0.0):
        super().__init__(display, now)
        self.audio = audio
        self.gain_db = gain_db          # shifts the dB->height mapping; +12 for a mic feed
        self.bars = np.zeros(N_BARS)
        self.peaks = np.zeros(N_BARS)
        self.peak_v = np.zeros(N_BARS)
        self.window = np.hanning(N_FFT).astype(np.float32)
        self._t = 0.0
        # log-spaced bin edges
        rate = audio.rate if audio else 44100
        freqs = np.fft.rfftfreq(N_FFT, 1.0 / rate)
        edges = np.geomspace(F_LO, F_HI, N_BARS + 1)
        self.bins = [np.where((freqs >= lo) & (freqs < hi))[0] for lo, hi in zip(edges[:-1], edges[1:])]
        self.bins = [b if len(b) else np.array([int(np.searchsorted(freqs, lo))]) for b, lo in zip(self.bins, edges[:-1])]
        self.scope = np.zeros(N_FFT // 2, dtype=np.float32)

    def update(self, dt: float) -> None:
        self._t += dt
        if not self.audio or not self.audio.alive:
            self.bars *= 0.9
            self.peaks *= 0.95
            return
        x = self.audio.latest(N_FFT / self.audio.rate)
        if len(x) < N_FFT:
            x = np.pad(x, (N_FFT - len(x), 0))
        self.scope = x[-len(self.scope):] * (10 ** (self.gain_db / 20.0))
        spec = np.abs(np.fft.rfft(x * self.window)) * (2.0 / N_FFT)
        db = 20.0 * np.log10(spec + 1e-9) + self.gain_db
        levels = np.array([db[b].max() for b in self.bins])
        levels = np.clip((levels - DB_FLOOR) / (DB_CEIL - DB_FLOOR), 0.0, 1.0)
        # fast attack, slow decay
        self.bars = np.where(levels > self.bars, levels, self.bars - dt * 2.0)
        self.bars = np.clip(self.bars, 0.0, 1.0)
        # peak caps: hold then fall with gravity
        rise = self.bars > self.peaks
        self.peaks = np.where(rise, self.bars, self.peaks)
        self.peak_v = np.where(rise, 0.0, self.peak_v + dt * 3.0)
        self.peaks = np.clip(self.peaks - self.peak_v * dt, 0.0, 1.0)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.BLACK)
        safe = d.safe
        bar = pygame.Rect(0, safe.top, d.w, 44)
        pygame.draw.rect(surface, D.BLUE, bar)
        pygame.draw.rect(surface, D.CYAN, bar.inflate(0, 4), 2)
        d.text(f"{self.number:02d}  {self.name}", "md", D.WHITE, (safe.left + 8, bar.centery), anchor="midleft")
        rel = self.now.release
        if rel:
            d.text(d.fit_text(f"{rel.artist} · {rel.title}", "sm", 330), "sm", D.YELLOW,
                   (safe.right - 8, bar.centery), anchor="midright")

        # oscilloscope strip
        scope = pygame.Rect(safe.left, bar.bottom + 14, safe.width, 90)
        pygame.draw.rect(surface, (8, 20, 8), scope)
        pygame.draw.rect(surface, D.GREEN, scope, 2)
        if self.audio and self.audio.alive:
            npts = scope.width // 2
            idx = np.linspace(0, len(self.scope) - 1, npts).astype(int)
            ys = scope.centery - (np.clip(self.scope[idx], -1, 1) * (scope.height // 2 - 4)).astype(int)
            xs = scope.left + np.arange(npts) * 2
            pygame.draw.lines(surface, D.GREEN, False, list(zip(xs.tolist(), ys.tolist())), 2)
        else:
            d.text("NO SIGNAL", "md", D.RED, scope.center, anchor="center")

        # spectrum bars
        area = pygame.Rect(safe.left, scope.bottom + 14, safe.width, safe.bottom - scope.bottom - 14)
        gap = 4
        bw = (area.width - gap * (N_BARS - 1)) // N_BARS
        for i in range(N_BARS):
            x = area.left + i * (bw + gap)
            h = int(self.bars[i] * area.height)
            if h >= 2:
                # 3-colour segments: green / amber / red by height
                r = pygame.Rect(x, area.bottom - h, bw, h)
                pygame.draw.rect(surface, D.GREEN, r)
                if self.bars[i] > 0.6:
                    top = pygame.Rect(x, area.bottom - h, bw, h - int(0.6 * area.height))
                    pygame.draw.rect(surface, D.AMBER, top)
                if self.bars[i] > 0.85:
                    top = pygame.Rect(x, area.bottom - h, bw, h - int(0.85 * area.height))
                    pygame.draw.rect(surface, D.RED, top)
            ph = int(self.peaks[i] * area.height)
            if ph >= 2:
                pygame.draw.rect(surface, D.WHITE, (x, area.bottom - ph - 2, bw, 3))
        # baseline
        pygame.draw.rect(surface, D.GREY, (area.left, area.bottom, area.width, 2))
