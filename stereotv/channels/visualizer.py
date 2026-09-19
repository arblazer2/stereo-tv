"""Channel 2: Visualizer — several modes on the live ring buffer.

  bars       spectrum bars + oscilloscope strip (the original)
  vu         stereo analogue VU meters with peak LEDs
  xy         oscilloscope in XY mode (L on X, R on Y) with phosphor persistence
  waterfall  scrolling spectrogram
V (or Enter / the remote's SPIN on this channel) cycles modes.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pygame

from stereotv import display as D
from stereotv.channels.base import Channel

N_FFT = 2048
N_BARS = 32
F_LO, F_HI = 40.0, 16000.0
DB_FLOOR = -60.0     # dBFS mapped to bar height 0
DB_CEIL = -6.0       # dBFS mapped to full height
MODES = ["bars", "vu", "xy", "waterfall"]
MODE_LABELS = {"bars": "SPECTRUM", "vu": "VU METERS", "xy": "XY SCOPE", "waterfall": "WATERFALL"}


class VisualizerChannel(Channel):
    number = 2
    name = "VISUALIZER"

    def __init__(self, display, now, audio, gain_db: float = 0.0):
        super().__init__(display, now)
        self.audio = audio
        self.gain_db = gain_db          # shifts the dB->height mapping; +12 for a mic feed
        self.mode = "bars"
        self._mode_osd = 0.0
        self._t = 0.0
        # spectrum state
        self.bars = np.zeros(N_BARS)
        self.peaks = np.zeros(N_BARS)
        self.peak_v = np.zeros(N_BARS)
        self.window = np.hanning(N_FFT).astype(np.float32)
        rate = audio.rate if audio else 44100
        freqs = np.fft.rfftfreq(N_FFT, 1.0 / rate)
        edges = np.geomspace(F_LO, F_HI, N_BARS + 1)
        self.bins = [np.where((freqs >= lo) & (freqs < hi))[0] for lo, hi in zip(edges[:-1], edges[1:])]
        self.bins = [b if len(b) else np.array([int(np.searchsorted(freqs, lo))]) for b, lo in zip(self.bins, edges[:-1])]
        self.scope = np.zeros(N_FFT // 2, dtype=np.float32)
        self.freqs = freqs
        # vu state
        self.vu = np.zeros(2)          # needle positions 0..1
        self.vu_peak = np.zeros(2)     # peak LED hold timers
        # xy / waterfall surfaces (built lazily at draw size)
        self._xy: pygame.Surface | None = None
        self._wf: pygame.Surface | None = None
        self._wf_edges = np.geomspace(F_LO, F_HI, 129)
        self._wf_bins = [np.where((freqs >= lo) & (freqs < hi))[0] for lo, hi in zip(self._wf_edges[:-1], self._wf_edges[1:])]
        self._wf_bins = [b if len(b) else np.array([int(np.searchsorted(freqs, lo))]) for b, lo in zip(self._wf_bins, self._wf_edges[:-1])]
        self._wf_pal = self._heat_palette()

    # ------------------------------------------------------------ mode switching
    def handle_key(self, key: int) -> bool:
        if key in (pygame.K_v, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
            self._mode_osd = 2.0
            return True
        return False

    # ------------------------------------------------------------ update
    def update(self, dt: float) -> None:
        self._t += dt
        self._mode_osd = max(0.0, self._mode_osd - dt)
        if not self.audio or not self.audio.alive:
            self.bars *= 0.9
            self.peaks *= 0.95
            self.vu *= 0.9
            return
        x = self.audio.latest(N_FFT / self.audio.rate)
        if len(x) < N_FFT:
            x = np.pad(x, (N_FFT - len(x), 0))
        g = 10 ** (self.gain_db / 20.0)
        self.scope = x[-len(self.scope):] * g
        self._spec = np.abs(np.fft.rfft(x * self.window)) * (2.0 / N_FFT)
        db = 20.0 * np.log10(self._spec + 1e-9) + self.gain_db
        levels = np.array([db[b].max() for b in self.bins])
        levels = np.clip((levels - DB_FLOOR) / (DB_CEIL - DB_FLOOR), 0.0, 1.0)
        self.bars = np.clip(np.where(levels > self.bars, levels, self.bars - dt * 2.0), 0.0, 1.0)
        rise = self.bars > self.peaks
        self.peaks = np.where(rise, self.bars, self.peaks)
        self.peak_v = np.where(rise, 0.0, self.peak_v + dt * 3.0)
        self.peaks = np.clip(self.peaks - self.peak_v * dt, 0.0, 1.0)
        if self.mode == "vu":
            st = self.audio.latest_stereo(0.3)                 # raw level: the VU is a meter, not a show
            if len(st):
                rms = np.sqrt((st * st).mean(axis=0)) + 1e-9
                # VU scale: -20 dB .. +3 dB over the arc, 0 VU at -10 dBFS
                target = np.clip((20 * np.log10(rms) + 10 + 20) / 23.0, 0.0, 1.0)
                # ballistics: ~300 ms rise, slower fall
                k_up, k_dn = min(1.0, dt / 0.15), min(1.0, dt / 0.4)
                self.vu = np.where(target > self.vu, self.vu + (target - self.vu) * k_up, self.vu + (target - self.vu) * k_dn)
                pk = np.abs(st).max(axis=0)
                self.vu_peak = np.where(pk > 0.95, 1.0, np.maximum(0.0, self.vu_peak - dt))

    # ------------------------------------------------------------ draw
    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.BLACK)
        bar = self.header(surface, self.now_line())
        area = pygame.Rect(d.safe.left, bar.bottom + 14, d.safe.width, d.safe.bottom - bar.bottom - 14)
        if not (self.audio and self.audio.alive):
            d.text("NO SIGNAL", "md", D.RED, area.center, anchor="center")
        elif self.mode == "bars":
            self._draw_bars(surface, area)
        elif self.mode == "vu":
            self._draw_vu(surface, area)
        elif self.mode == "xy":
            self._draw_xy(surface, area)
        else:
            self._draw_waterfall(surface, area)
        if self._mode_osd > 0:
            label = MODE_LABELS[self.mode]
            w = d.fonts["md"].size(label)[0] + 40
            box = pygame.Rect(0, 0, w, 44); box.center = (d.w // 2, area.top + 30)
            d.panel(surface, box, D.BLACK, D.YELLOW)
            d.text(label, "md", D.YELLOW, box.center, anchor="center", shadow=False)

    # ---- bars (original)
    def _draw_bars(self, surface, area) -> None:
        d = self.d
        scope = pygame.Rect(area.left, area.top, area.width, 90)
        d.panel(surface, scope, (8, 20, 8) if not d.modern else D.DARK, D.GREEN)
        npts = scope.width // 2
        idx = np.linspace(0, len(self.scope) - 1, npts).astype(int)
        ys = scope.centery - (np.clip(self.scope[idx], -1, 1) * (scope.height // 2 - 4)).astype(int)
        xs = scope.left + np.arange(npts) * 2
        pygame.draw.lines(surface, D.GREEN, False, list(zip(xs.tolist(), ys.tolist())), 2)
        bars = pygame.Rect(area.left, scope.bottom + 14, area.width, area.bottom - scope.bottom - 14)
        gap = 4
        bw = (bars.width - gap * (N_BARS - 1)) // N_BARS
        for i in range(N_BARS):
            x = bars.left + i * (bw + gap)
            h = int(self.bars[i] * bars.height)
            if h >= 2:
                pygame.draw.rect(surface, D.GREEN, (x, bars.bottom - h, bw, h))
                if self.bars[i] > 0.6:
                    pygame.draw.rect(surface, D.AMBER, (x, bars.bottom - h, bw, h - int(0.6 * bars.height)))
                if self.bars[i] > 0.85:
                    pygame.draw.rect(surface, D.RED, (x, bars.bottom - h, bw, h - int(0.85 * bars.height)))
            ph = int(self.peaks[i] * bars.height)
            if ph >= 2:
                pygame.draw.rect(surface, D.WHITE, (x, bars.bottom - ph - 2, bw, 3))
        pygame.draw.rect(surface, D.GREY, (bars.left, bars.bottom, bars.width, 2))

    # ---- VU meters
    def _draw_vu(self, surface, area) -> None:
        d = self.d
        gap = 20
        w = (area.width - gap) // 2
        face_col = (236, 226, 190) if not d.modern else D.DARK
        ink = (40, 30, 20) if not d.modern else D.WHITE
        red = (200, 40, 40)
        sweep = math.radians(42)                                   # ±42° about vertical
        for ch, label in enumerate(("LEFT", "RIGHT")):
            r = pygame.Rect(area.left + ch * (w + gap), area.top, w, area.height)
            d.panel(surface, r, (30, 26, 22) if not d.modern else D.NAVY, D.GREY, radius=10)
            face = r.inflate(-16, -16)
            face.height = int(face.height * 0.70)
            d.panel(surface, face, face_col, None, radius=8)
            # pivot below the face; radius chosen so the arc spans the face width and sits high in it
            cx = face.centerx
            R = int((face.width / 2 - 14) / math.sin(sweep))
            cy = face.top + 56 + R                                    # arc apex 56 px under the face top
            def ang(vu_db):  # -20 .. +3 across the sweep, measured from straight up
                return -math.pi / 2 + sweep * ((vu_db + 20) / 23.0 * 2 - 1)
            def pt(vu_db, rad):
                a_ = ang(vu_db); return (cx + int(math.cos(a_) * rad), cy + int(math.sin(a_) * rad))
            surface.set_clip(face)
            pygame.draw.lines(surface, ink, False, [pt(v, R - 20) for v in np.linspace(-20, 0, 30)], 3)
            pygame.draw.lines(surface, red, False, [pt(v, R - 20) for v in np.linspace(0, 3, 10)], 5)
            for vdb, txt in ((-20, "20"), (-10, "10"), (-7, ""), (-5, "5"), (-3, "3"), (-2, ""), (-1, ""), (0, "0"), (1, ""), (2, ""), (3, "3")):
                c = red if vdb > 0 else ink
                pygame.draw.line(surface, c, pt(vdb, R - 20), pt(vdb, R - 32), 3 if txt else 2)
                if txt:
                    d.text(txt, "xs", c, pt(vdb, R - 46), anchor="center", shadow=False)
            d.text("VU", "sm_bold", ink, (face.centerx, face.bottom - 22), anchor="center", shadow=False)
            vu_db = -20 + float(self.vu[ch]) * 23.0
            pygame.draw.line(surface, (20, 20, 20) if not d.modern else D.CYAN, (cx, cy), pt(vu_db, R - 6), 3)
            surface.set_clip(None)
            pygame.draw.circle(surface, (60, 50, 40), (cx, min(cy, face.bottom - 4)), 6)
            d.text(label, "sm_bold", D.WHITE, (r.left + 16, r.bottom - 40), shadow=False)
            led = (r.right - 28, r.bottom - 30)
            pygame.draw.circle(surface, (255, 60, 60) if self.vu_peak[ch] > 0 else (70, 20, 20), led, 8)
            d.text("PEAK", "xs", D.GREY, (led[0] - 16, led[1]), anchor="midright", shadow=False)

    # ---- XY scope
    def _draw_xy(self, surface, area) -> None:
        d = self.d
        size = min(area.width, area.height)
        box = pygame.Rect(0, 0, size, size); box.center = area.center
        if self._xy is None or self._xy.get_size() != (size, size):
            self._xy = pygame.Surface((size, size)); self._xy.fill((0, 8, 0))
        # phosphor persistence: fade the previous trace
        fade = pygame.Surface((size, size)); fade.fill((18, 22, 18))
        self._xy.blit(fade, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        st = self.audio.latest_stereo(N_FFT / self.audio.rate) * (10 ** (self.gain_db / 20.0))
        n = min(len(st), 1024)
        if n > 2:
            st = st[-n:]
            # auto-scale so the trace fills the tube whatever the record's level
            rms = float(np.sqrt((st * st).mean())) + 1e-6
            peak = float(np.abs(st).max()) + 1e-6
            self._xy_scale = 0.9 * getattr(self, "_xy_scale", 1.0) + 0.1 * min(8.0, 0.9 / peak)
            st = np.clip(st * self._xy_scale, -1.0, 1.0)
            c = size // 2; k = (size // 2 - 8)
            # rotate 45°: mono content draws a vertical line, stereo width spreads sideways (the classic view)
            xs = (c + ((st[:, 0] - st[:, 1]) * 0.7071 * k)).astype(int)
            ys = (c - ((st[:, 0] + st[:, 1]) * 0.7071 * k)).astype(int)
            pts = list(zip(np.clip(xs, 0, size - 1).tolist(), np.clip(ys, 0, size - 1).tolist()))
            pygame.draw.lines(self._xy, D.GREEN, False, pts, 1)
            pygame.draw.lines(self._xy, (120, 255, 140), False, pts[::4], 1)
        surface.blit(self._xy, box)
        # graticule
        pygame.draw.rect(surface, D.GREEN, box, 2)
        pygame.draw.line(surface, (40, 90, 40), (box.centerx, box.top), (box.centerx, box.bottom), 1)
        pygame.draw.line(surface, (40, 90, 40), (box.left, box.centery), (box.right, box.centery), 1)
        d.text("L", "xs", D.GREY, (box.left + 8, box.top + 6), shadow=False)
        d.text("R", "xs", D.GREY, (box.right - 8, box.top + 6), anchor="topright", shadow=False)
        d.text("MONO = VERTICAL LINE", "xs", D.GREY, (box.centerx, box.bottom - 22), anchor="midtop", shadow=False)

    # ---- waterfall
    @staticmethod
    def _heat_palette() -> np.ndarray:
        stops = [(0, (0, 0, 0)), (60, (20, 0, 90)), (120, (170, 0, 120)), (180, (255, 80, 0)), (230, (255, 220, 40)), (255, (255, 255, 255))]
        pal = np.zeros((256, 3), dtype=np.uint8)
        for (p0, c0), (p1, c1) in zip(stops[:-1], stops[1:]):
            for i in range(p0, p1 + 1):
                t = (i - p0) / max(1, p1 - p0)
                pal[i] = [int(c0[j] + (c1[j] - c0[j]) * t) for j in range(3)]
        return pal

    def _draw_waterfall(self, surface, area) -> None:
        d = self.d
        if self._wf is None or self._wf.get_size() != area.size:
            self._wf = pygame.Surface(area.size); self._wf.fill(D.BLACK)
        # scroll down 2 px and paint the new row across the top (log-spaced 128 bins -> width)
        self._wf.scroll(0, 2)
        db = 20.0 * np.log10(getattr(self, "_spec", np.zeros(N_FFT // 2 + 1)) + 1e-9) + self.gain_db
        lv = np.array([db[b].max() for b in self._wf_bins])
        lv = np.clip((lv - DB_FLOOR) / (DB_CEIL - DB_FLOOR), 0.0, 1.0)
        cols = np.interp(np.linspace(0, len(lv) - 1, area.width), np.arange(len(lv)), lv)
        row = self._wf_pal[(cols * 255).astype(int)]
        strip = pygame.surfarray.make_surface(np.repeat(row[:, None, :], 2, axis=1))   # width x 2 x rgb
        self._wf.blit(strip, (0, 0))
        surface.blit(self._wf, area)
        pygame.draw.rect(surface, D.GREY, area, 2)
        for fz, lab in ((100, "100"), (1000, "1k"), (10000, "10k")):
            xf = area.left + int(np.log(fz / F_LO) / np.log(F_HI / F_LO) * area.width)
            pygame.draw.line(surface, (90, 90, 90), (xf, area.bottom - 12), (xf, area.bottom - 2), 2)
            d.text(lab, "xs", D.GREY, (xf, area.bottom - 14), anchor="midbottom", shadow=False)
