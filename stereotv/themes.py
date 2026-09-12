"""Themes: a palette + font set. `apply(name)` rewrites the shared colours in stereotv.display
and rebuilds its fonts, so every channel and screensaver follows without knowing.

Palette keys are the names channels already use: BLACK WHITE GREY DARK NAVY BLUE CYAN YELLOW AMBER RED GREEN.
"""
from __future__ import annotations

from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEJAVU_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
VT323 = str(FONT_DIR / "VT323-Regular.ttf")
BARLOW = str(FONT_DIR / "BarlowCondensed-SemiBold.ttf")

THEMES: dict[str, dict] = {
    # the original: late-80s cable box
    "cable88": {
        "label": "CABLE '88",
        "colors": {"BLACK": (0, 0, 0), "WHITE": (235, 235, 235), "GREY": (140, 140, 140), "DARK": (18, 18, 28),
                   "NAVY": (12, 24, 72), "BLUE": (30, 60, 160), "CYAN": (90, 220, 240), "YELLOW": (250, 220, 60),
                   "AMBER": (255, 170, 40), "RED": (220, 40, 40), "GREEN": (60, 200, 90)},
        "font": DEJAVU_BOLD, "mono": DEJAVU_MONO, "scale": 1.0, "shadow": True,
    },
    # 90s Prevue Guide: cobalt panels, white + yellow condensed type
    "prevue": {
        "label": "PREVUE",
        "colors": {"BLACK": (0, 0, 0), "WHITE": (255, 255, 255), "GREY": (190, 190, 210), "DARK": (0, 0, 70),
                   "NAVY": (0, 0, 120), "BLUE": (40, 40, 200), "CYAN": (255, 255, 255), "YELLOW": (255, 230, 0),
                   "AMBER": (255, 200, 0), "RED": (255, 70, 70), "GREEN": (120, 255, 120)},
        "font": BARLOW, "mono": DEJAVU_MONO, "scale": 1.12, "shadow": True,
    },
    # BBC Ceefax / teletext: black, saturated primaries, pixel type
    "teletext": {
        "label": "TELETEXT",
        "colors": {"BLACK": (0, 0, 0), "WHITE": (255, 255, 255), "GREY": (170, 170, 170), "DARK": (0, 0, 0),
                   "NAVY": (0, 0, 0), "BLUE": (0, 0, 200), "CYAN": (0, 255, 255), "YELLOW": (255, 255, 0),
                   "AMBER": (255, 170, 0), "RED": (255, 0, 0), "GREEN": (0, 255, 0)},
        "font": VT323, "mono": VT323, "scale": 1.3, "shadow": False,
    },
    # monochrome green phosphor terminal
    "phosphor": {
        "label": "PHOSPHOR",
        "colors": {"BLACK": (0, 6, 0), "WHITE": (140, 255, 150), "GREY": (60, 150, 80), "DARK": (0, 12, 0),
                   "NAVY": (0, 18, 0), "BLUE": (0, 60, 10), "CYAN": (100, 220, 120), "YELLOW": (170, 255, 170),
                   "AMBER": (200, 255, 190), "RED": (220, 255, 200), "GREEN": (90, 255, 110)},
        "font": VT323, "mono": VT323, "scale": 1.3, "shadow": False,
    },
}
ORDER = list(THEMES)


def get(name: str) -> dict:
    return THEMES.get(name) or THEMES["cable88"]


def next_name(name: str) -> str:
    i = ORDER.index(name) if name in ORDER else -1
    return ORDER[(i + 1) % len(ORDER)]
