"""Design themes: faithful ports of the three 960x720 artboard sets (Cable Guide, Faceplate, Sleeve Poster).

Channels 1 (Now Playing) and 4 (Similar Sounds) get their own full-screen layouts, drawn in the
artboard's own coordinate system and scaled to the canvas (640x480 -> x2/3), letterboxed if the
canvas isn't 4:3. Every other channel draws itself inside the theme's frame: the guide's header
bar, the receiver's display window, the poster's masthead. Static layers are pre-rendered once.

Entry points (called from main / base.Channel):
  draw(app, channel)             render the current channel in the active design theme
  header(channel, surface, right) in-frame header for ordinary channels
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np
import pygame

from stereotv import display as D

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"
F = {
    "barlow5": "BarlowCondensed-Medium.ttf", "barlow6": "BarlowCondensed-SemiBold.ttf", "barlow7": "BarlowCondensed-Bold.ttf",
    "jost4": "Jost-Regular.ttf", "jost5": "Jost-Medium.ttf", "jost6": "Jost-SemiBold.ttf",
    "vt": "VT323-Regular.ttf", "archivo": "ArchivoBlack-Regular.ttf",
    "mono4": "SpaceMono-Regular.ttf", "mono7": "SpaceMono-Bold.ttf",
}
DESIGN_W, DESIGN_H = 960, 720
MIN_PX = 10                       # nothing renders smaller than this, whatever the scale

# design accents (the artboards' default {{accent}} / {{amber}} props)
GUIDE_ACCENT = (255, 225, 77)
GUIDE_NAVY, GUIDE_DEEP, GUIDE_CELL, GUIDE_SUB = (10, 20, 112), (5, 11, 61), (36, 71, 214), (185, 200, 255)
AMBER = (255, 176, 46)
FACE_INK, FACE_MUTED = (42, 38, 33), (90, 84, 74)
PAPER, INK, POSTER_RED = (241, 237, 228), (17, 17, 17), (230, 58, 30)

# channel shorthand shown on the faceplate tuner dial
DIAL_LABELS = {1: "PHONO", 2: "SCOPE", 3: "NOTES", 4: "SIMILAR", 5: "GUIDE", 6: "TRACKS", 7: "STATS", 8: "SHELF",
               9: "CREW", 10: "RADAR", 11: "WX", 12: "SYSTEM", 13: "NEWS", 14: "TODAY", 15: "VALUE", 16: "TEST"}


# ---------------------------------------------------------------- geometry + text helpers
class Ctx:
    """Maps artboard coordinates onto the canvas; caches fonts, text renders and static layers."""

    def __init__(self, d):
        self.d = d
        self.s = min(d.w / DESIGN_W, d.h / DESIGN_H)
        self.ox = int((d.w - DESIGN_W * self.s) / 2)
        self.oy = int((d.h - DESIGN_H * self.s) / 2)
        self._fonts: dict = {}
        self._text: dict = {}
        self.static: dict = {}
        self.data: dict = {}

    def X(self, v): return self.ox + int(round(v * self.s))
    def Y(self, v): return self.oy + int(round(v * self.s))
    def L(self, v): return max(1, int(round(v * self.s)))
    def R(self, x, y, w, h): return pygame.Rect(self.X(x), self.Y(y), self.L(w), self.L(h))

    def font(self, key: str, size_design: float) -> pygame.font.Font:
        px = max(MIN_PX, int(round(size_design * self.s)))
        f = self._fonts.get((key, px))
        if f is None:
            f = self._fonts[(key, px)] = pygame.font.Font(str(FONT_DIR / F[key]), px)
        return f

    def render(self, text: str, key: str, size: float, color, spacing: float = 0.0) -> pygame.Surface:
        """Text with CSS-style letter-spacing (em). Cached."""
        ck = (text, key, size, color, spacing)
        img = self._text.get(ck)
        if img is None:
            f = self.font(key, size)
            if not spacing or len(text) < 2:
                img = f.render(text, True, color)
            else:
                px = f.get_height() and max(MIN_PX, int(round(size * self.s)))
                gap = spacing * px
                glyphs = [f.render(ch, True, color) for ch in text]
                w = int(sum(g.get_width() for g in glyphs) + gap * (len(glyphs) - 1)) + 1
                img = pygame.Surface((max(1, w), f.get_height()), pygame.SRCALPHA)
                x = 0.0
                for g in glyphs:
                    img.blit(g, (int(x), 0))
                    x += g.get_width() + gap
            if len(self._text) > 900:
                self._text.clear()
            self._text[ck] = img
        return img

    def text(self, surf, text, key, size, color, x, y, spacing=0.0, anchor="topleft", alpha=255):
        """x, y in canvas pixels. Returns the drawn rect."""
        img = self.render(text, key, size, color, spacing)
        r = img.get_rect(**{anchor: (x, y)})
        if alpha < 255:
            img = img.copy(); img.set_alpha(alpha)
        surf.blit(img, r)
        return r

    def fit(self, text, key, hi, lo, max_w, spacing=0.0):
        """Largest design size in [lo, hi] at which text fits max_w canvas px (steps of 2)."""
        size = hi
        while size > lo and self.render(text, key, size, (0, 0, 0), spacing).get_width() > max_w:
            size -= 2
        return size

    def ellipsize(self, text, key, size, max_w, spacing=0.0):
        if self.render(text, key, size, (0, 0, 0), spacing).get_width() <= max_w:
            return text
        while text and self.render(text + "…", key, size, (0, 0, 0), spacing).get_width() > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    def glow_text(self, surf, text, key, size, color, x, y, spacing=0.0, anchor="topleft", alpha=255):
        """VT323 display text with the amber text-shadow glow (0 0 8px, 50%). Cached."""
        ck = ("glow", text, key, size, color, spacing, alpha)
        img = self._text.get(ck)
        if img is None:
            base = self.render(text, key, size, color, spacing)
            pad = self.L(8) + 2
            w, h = base.get_width() + 2 * pad, base.get_height() + 2 * pad
            img = pygame.Surface((w, h), pygame.SRCALPHA)
            img.blit(base, (pad, pad))
            small = pygame.transform.smoothscale(img, (max(1, w // 4), max(1, h // 4)))
            halo = pygame.transform.smoothscale(small, (w, h))
            halo.fill((255, 255, 255, 110), special_flags=pygame.BLEND_RGBA_MULT)
            out = pygame.Surface((w, h), pygame.SRCALPHA)
            out.blit(halo, (0, 0)); out.blit(halo, (0, 0)); out.blit(img, (0, 0))
            if alpha < 255:
                out.fill((255, 255, 255, alpha), special_flags=pygame.BLEND_RGBA_MULT)
            img = self._text[ck] = (out, pad)
        out, pad = img
        r = out.get_rect()
        tr = pygame.Rect(0, 0, r.w - 2 * pad, r.h - 2 * pad)
        setattr(tr, anchor, (x, y))
        surf.blit(out, (tr.x - pad, tr.y - pad))
        return tr


_ctx: Ctx | None = None


def ctx(d) -> Ctx:
    global _ctx
    if _ctx is None or _ctx.d is not d or _ctx.s != min(d.w / DESIGN_W, d.h / DESIGN_H):
        _ctx = Ctx(d)
    return _ctx


def reset():
    """Theme changed: drop caches (colours baked into renders)."""
    global _ctx
    _ctx = None


# ---------------------------------------------------------------- data helpers
def _fmt_t(s: float) -> str:
    s = max(0, int(s)); return f"{s // 60}:{s % 60:02d}"


def _progress(now):
    p = now.progress() if hasattr(now, "progress") else None
    if not p:
        return None
    el, dur = p
    return max(0.0, min(1.0, el / dur)) if dur else 0.0, f"{_fmt_t(el)} / {_fmt_t(dur)}"


def _cover(c: Ctx, path: str | None, size_px: int):
    key = ("cover", path, size_px)
    img = c.static.get(key)
    if img is None and path:
        try:
            src = pygame.image.load(path).convert()
            img = pygame.transform.smoothscale(src, (size_px, size_px))
        except Exception:  # noqa: BLE001
            img = None
        c.static[key] = img
    return img


def _similar(app, rel, n=5):
    """[(Release, reason)] from the In Your Collection logic, cached per release."""
    c = ctx(app.d)
    key = ("similar", rel.release_id)
    if key in c.data:
        return c.data[key]
    out, seen = [], {rel.release_id}
    try:
        sections = app.channels[4]._build(rel) if rel.release_id > 0 else []
    except Exception:  # noqa: BLE001
        sections = []
    for title, rels in sections:
        for r in rels:
            if r.release_id not in seen:
                seen.add(r.release_id); out.append((r, title))
            if len(out) >= n:
                break
        if len(out) >= n:
            break
    c.data[key] = out
    return out


def _reason(section_title: str) -> str:
    t = section_title or ""
    if t.startswith("MORE BY "):
        return f"More from {t[8:].title()} on your shelf."
    if t.startswith("ALSO ON "):
        return f"Also on {t[8:].title()} — same label, from your shelf."
    if t.startswith("MORE "):
        return f"Shares the {t[5:].title()} style — picked from your shelf by style and year."
    return "Picked from your shelf by genre, style and year."


def _next_up(now) -> str:
    rel = now.release
    if not rel or rel.release_id <= 0:
        return ""
    try:
        from stereotv import discogs
        con = discogs.open_db()
        rows = con.execute("SELECT position, title FROM tracks WHERE release_id=? ORDER BY seq", (rel.release_id,)).fetchall()
        con.close()
    except Exception:  # noqa: BLE001
        return ""
    cur = f"{now.side or ''}{now.track or ''}".upper()
    for i, r in enumerate(rows):
        if (r["position"] or "").upper() == cur and i + 1 < len(rows):
            nxt = rows[i + 1]
            ns = (nxt["position"] or "")[:1].upper()
            return f"Up next: {nxt['title']}" if ns == (now.side or "").upper() else f"Up next: Side {ns}"
    return ""


def _track_line(now) -> str:
    if now.track_title:
        bits = []
        if now.side:
            bits.append(f"Side {now.side}")
        if now.track:
            bits.append(f"Track {now.track}")
        return (" · ".join(bits) + " — " if bits else "") + now.track_title
    rel = now.release
    return " · ".join(p for p in (str(rel.year or ""), (rel.label or "").split(",")[0]) if p) if rel else ""


def _date_short(fmt: str) -> str:
    t = time.localtime()
    if fmt == "guide":
        return f"{time.strftime('%a', t).upper()} {t.tm_mon}/{t.tm_mday}"
    return f"{time.strftime('%a', t)} {t.tm_mday} {time.strftime('%b', t)}"


def _clock() -> str:
    return time.strftime("%I:%M %p").lstrip("0")


# ================================================================ CABLE GUIDE
def _guide_bg(c: Ctx) -> pygame.Surface:
    bg = c.static.get("guide_bg")
    if bg is None:
        d = c.d
        bg = pygame.Surface((d.w, d.h))
        bg.fill(GUIDE_NAVY)
        top, mid = GUIDE_NAVY, (15, 34, 150)
        for y in range(d.h):
            k = (y - c.oy) / max(1, DESIGN_H * c.s)
            if k < 0.55:
                t = k / 0.55; col = [int(top[i] + (mid[i] - top[i]) * t) for i in range(3)]
            else:
                t = (k - 0.55) / 0.45; col = [int(mid[i] + (top[i] - mid[i]) * t) for i in range(3)]
            bg.fill(col, (0, y, d.w, 1))
        c.static["guide_bg"] = bg
    return bg


def _guide_header(c: Ctx, surf) -> None:
    bar = c.R(0, 0, DESIGN_W, 56)
    pygame.draw.rect(surf, GUIDE_DEEP, (0, bar.y, c.d.w, bar.h))
    lab = c.render("STEREO·TV GUIDE", "barlow7", 28, GUIDE_NAVY, 0.12)
    badge = pygame.Rect(c.X(24), 0, lab.get_width() + c.L(28), lab.get_height() + c.L(2))
    badge.centery = bar.centery
    pygame.draw.rect(surf, GUIDE_ACCENT, badge)
    surf.blit(lab, lab.get_rect(center=badge.center))
    right = c.text(surf, _clock(), "barlow6", 28, (255, 255, 255), c.X(936), bar.centery, 0.06, anchor="midright")
    c.text(surf, _date_short("guide"), "barlow6", 28, (255, 255, 255), (badge.right + right.left) // 2, bar.centery, 0.06, anchor="center")


def _guide_now(app, surf) -> None:
    c, now = ctx(app.d), app.now
    surf.blit(_guide_bg(c), (0, 0))
    _guide_header(c, surf)
    rel = now.release or now.last_played
    playing = now.release is not None
    # album art: 332 box, 6px white border
    box = c.R(24, 76, 332, 332)
    pygame.draw.rect(surf, (255, 255, 255), box)
    inner = box.inflate(-2 * c.L(6), -2 * c.L(6))
    img = _cover(c, rel.cover_path if (rel and playing) else None, inner.w)
    if img:
        surf.blit(img, inner)
    else:
        stripes = c.static.get("guide_stripes")
        if stripes is None or stripes.get_size() != inner.size:
            stripes = pygame.Surface(inner.size); stripes.fill((26, 55, 176))
            step = c.L(36)
            for k in range(-inner.h, inner.w + inner.h, step):
                pygame.draw.polygon(stripes, GUIDE_CELL, [(k, inner.h), (k + step // 2, inner.h), (k + step // 2 + inner.h, 0), (k + inner.h, 0)])
            c.static["guide_stripes"] = stripes
        surf.blit(stripes, inner)
        c.text(surf, "STAND BY" if not playing else "ALBUM ART", "barlow7", 30, GUIDE_SUB, inner.centerx, inner.centery, 0.2, anchor="center")
    # right column
    x0, colw = c.X(384), c.L(552)
    y = c.Y(76)
    lab = c.render(f"CH {app.cur:02d}", "barlow7", 34, GUIDE_NAVY, 0.06)
    badge = pygame.Rect(x0, y, lab.get_width() + c.L(28), lab.get_height())
    pygame.draw.rect(surf, GUIDE_ACCENT, badge)
    surf.blit(lab, lab.get_rect(center=badge.center))
    c.text(surf, "NOW SPINNING" if playing else "OFF AIR", "barlow6", 30, GUIDE_ACCENT, badge.right + c.L(14), badge.centery, 0.18, anchor="midleft")
    title = (rel.title if (rel and playing) else "Nothing on").upper()
    size = c.fit(title, "barlow7", 104, 60, colw, 0.01)
    t = c.text(surf, c.ellipsize(title, "barlow7", size, colw, 0.01), "barlow7", size, (255, 255, 255), x0, badge.bottom + c.L(14) - c.L(size * 0.06), 0.01)
    artist = rel.artist if (rel and playing) else "Drop a needle to begin"
    c.text(surf, c.ellipsize(artist, "barlow5", 48, colw), "barlow5", 48, (255, 255, 255), x0, t.bottom - c.L(size * 0.08))
    # bottom group: track line + progress bar
    bottom = c.Y(408)
    prog = _progress(now) if playing else None
    track = _track_line(now) if playing else (f"Last played: {now.last_played.artist} — {now.last_played.title}" if now.last_played else "")
    bar_h = c.L(16)
    bar_y = bottom - bar_h
    if prog:
        pct, tstr = prog
        tr = c.text(surf, tstr, "barlow6", 28, (255, 255, 255), x0 + colw, bar_y + bar_h // 2, anchor="midright")
        bar = pygame.Rect(x0, bar_y, tr.left - c.L(16) - x0, bar_h)
        pygame.draw.rect(surf, GUIDE_DEEP, bar)
        pygame.draw.rect(surf, GUIDE_ACCENT, (bar.x, bar.y, int(bar.w * pct), bar.h))
        pygame.draw.rect(surf, (255, 255, 255), bar, c.L(2))
        ty = bar_y - c.L(10)
    else:
        ty = bottom
    if track:
        c.text(surf, c.ellipsize(track, "barlow6", 32, colw), "barlow6", 32, (255, 255, 255), x0, ty, anchor="bottomleft")
    # program grid
    _guide_grid(app, c, surf, rel if playing else None)


def _guide_grid(app, c: Ctx, surf, rel) -> None:
    x0, gap = 24, 3
    fr = (912 - 200 - 3 * gap) / 3
    cols = [x0, x0 + 200 + gap, x0 + 200 + gap + fr + gap, x0 + 200 + 2 * (fr + gap) + gap]
    y = 428
    t = time.localtime()
    base = t.tm_hour * 60 + (0 if t.tm_min < 30 else 30)
    for i in range(3):
        m = (base + 30 * i) % (24 * 60)
        h12 = (m // 60) % 12 or 12
        c.text(surf, f"{h12}:{m % 60:02d} {'AM' if m < 720 else 'PM'}", "barlow6", 26, GUIDE_ACCENT, c.X(cols[i + 1]), c.Y(y + 17), anchor="midleft")
    c.text(surf, "CHANNEL", "barlow6", 26, GUIDE_ACCENT, c.X(cols[0]), c.Y(y + 17), 0.1, anchor="midleft")
    names = {ch: getattr(app.channels.get(ch), "name", "") for ch in (1, 2, 3, 4, 8)}
    sim = _similar(app, rel) if rel else []
    upnext = _next_up(app.now) if rel else ""
    rows = [
        (1, names[1], [(2, f"{rel.title} — {rel.artist}" if rel else "Off air — drop the needle", True), (1, upnext or "Up next: your pick", False)]),
        (3, names[3], [(1, "About the artist", False), (2, "Album story · pressing notes", False)]),
        (4, "SIMILAR SOUNDS", [(3, "From your shelf: " + " · ".join(r.title for r, _ in sim[:3]) if sim else "From your shelf: related records", False)]),
        (2, names[2], [(3, "Visualizer — spectrum · VU meters · XY scope · waterfall", False)]),
        (8, names[8], [(3, "Random pick from your Discogs library", False)]),
    ]
    y += 34 + gap
    for num, name, cells in rows:
        chr_ = c.R(cols[0], y, 200, 44)
        pygame.draw.rect(surf, GUIDE_DEEP, chr_)
        n = c.text(surf, f"{num:02d}", "barlow7", 30, GUIDE_ACCENT, chr_.x + c.L(12), chr_.centery, anchor="midleft")
        nsz = c.fit(name, "barlow6", 22, 16, chr_.right - n.right - c.L(18), 0.06)
        c.text(surf, c.ellipsize(name, "barlow6", nsz, chr_.right - n.right - c.L(18), 0.06), "barlow6", nsz, (255, 255, 255), n.right + c.L(10), chr_.centery, 0.06, anchor="midleft")
        ci = 1
        for span, text, hot in cells:
            x_ = cols[ci]; w = span * fr + (span - 1) * gap
            cell = c.R(x_, y, w, 44)
            pygame.draw.rect(surf, GUIDE_ACCENT if hot else GUIDE_CELL, cell)
            key = "barlow7" if hot else "barlow6"
            c.text(surf, c.ellipsize(text, key, 26, cell.w - c.L(28)), key, 26, GUIDE_NAVY if hot else (255, 255, 255), cell.x + c.L(14), cell.centery, anchor="midleft")
            ci += span
        y += 44 + gap


def _guide_similar(app, surf) -> None:
    c, now = ctx(app.d), app.now
    surf.blit(_guide_bg(c), (0, 0))
    _guide_header(c, surf)
    rel = now.release or now.last_played
    # heading block 56..176
    lab = c.render(f"CH {app.cur:02d}", "barlow7", 40, GUIDE_NAVY, 0.06)
    sub = f"Because you're spinning {rel.title} by {rel.artist}" if rel else "Spin a record to see what else on your shelf fits"
    sub_img = c.render(c.ellipsize(sub, "barlow5", 30, c.L(912)), "barlow5", 30, GUIDE_SUB)
    head_h = lab.get_height() + c.L(4) + sub_img.get_height()
    top = c.Y(56) + (c.L(120) - head_h) // 2
    badge = pygame.Rect(c.X(24), top, lab.get_width() + c.L(28), lab.get_height())
    pygame.draw.rect(surf, GUIDE_ACCENT, badge)
    surf.blit(lab, lab.get_rect(center=badge.center))
    c.text(surf, "SIMILAR SOUNDS", "barlow7", 64, (255, 255, 255), badge.right + c.L(16), badge.centery, 0.04, anchor="midleft")
    surf.blit(sub_img, (c.X(24), badge.bottom + c.L(4)))
    # table
    gap = 3
    widths = [90, 912 - 90 - 300 - 100 - 150 - 4 * gap, 300, 100, 150]
    xs = [24]
    for w in widths[:-1]:
        xs.append(xs[-1] + w + gap)
    y = 176
    for x_, h in zip(xs, ("#", "ALBUM", "ARTIST", "YEAR", "STATUS")):
        c.text(surf, h, "barlow6", 26, GUIDE_ACCENT, c.X(x_), c.Y(y + 17), anchor="midleft")
    y += 34 + gap
    sims = _similar(app, rel) if rel else []
    for i in range(5):
        r = sims[i][0] if i < len(sims) else None
        hot = i == 0 and r is not None
        vals = [f"{i + 1:02d}", r.title if r else "", r.artist if r else "", str(r.year or "") if r else "", "ON SHELF" if r else ""]
        for j, (x_, w, v) in enumerate(zip(xs, widths, vals)):
            cell = c.R(x_, y, w, 60)
            bg = GUIDE_ACCENT if hot else (GUIDE_DEEP if j == 0 else GUIDE_CELL)
            pygame.draw.rect(surf, bg, cell)
            col = GUIDE_NAVY if hot else (GUIDE_ACCENT if j == 0 else (255, 255, 255))
            key = "barlow7" if hot else "barlow6"
            if v:
                c.text(surf, c.ellipsize(v, key, 32, cell.w - c.L(28)), key, 32, col, cell.x + c.L(14), cell.centery, anchor="midleft")
        y += 60 + gap
    # about box
    box = c.R(24, y + 16 - gap, 912, 720 - 26 - (y + 16 - gap))
    pygame.draw.rect(surf, GUIDE_DEEP, box)
    pygame.draw.rect(surf, (255, 255, 255), box, c.L(3))
    reason = _reason(sims[0][1]) if sims else "Nothing related on the shelf yet."
    body = c.render("ABOUT THIS PICK", "barlow7", 24, GUIDE_ACCENT, 0.18)
    words, lines, cur = reason.split(), [], ""
    fw = box.w - c.L(40)
    for wd in words:
        t_ = (cur + " " + wd).strip()
        if c.render(t_, "barlow5", 34, (255, 255, 255)).get_width() <= fw or not cur:
            cur = t_
        else:
            lines.append(cur); cur = wd
    lines.append(cur)
    lines = lines[:2]
    lh = int(c.font("barlow5", 34).get_height() * 1.0)
    total = body.get_height() + c.L(6) + lh * len(lines)
    ty = box.centery - total // 2
    surf.blit(body, (box.x + c.L(20), ty))
    ty += body.get_height() + c.L(6)
    for ln in lines:
        c.text(surf, ln, "barlow5", 34, (255, 255, 255), box.x + c.L(20), ty); ty += lh


def _guide_frame(app, surf, ch) -> pygame.Rect:
    """Ordinary channels: header bar on top; returns the content rect."""
    c = ctx(app.d)
    _guide_header(c, surf)
    return pygame.Rect(c.X(24), c.Y(56 + 14), c.L(912), c.L(720 - 56 - 14 - 14))


# ================================================================ FACEPLATE
def _face_static(c: Ctx, kind: str) -> pygame.Surface:
    """kind: 'now' (VU + display + dial + knobs), 'similar' (display + dial + knobs), 'frame' (window hole + dial)."""
    key = ("face", kind)
    s = c.static.get(key)
    if s is not None:
        return s
    d = c.d
    s = pygame.Surface((d.w, d.h), pygame.SRCALPHA)
    # wood: repeating vertical stripes #3a2213 6px, #45291a 5px, #33200f 4px
    wood = [((58, 34, 19), 6), ((69, 41, 26), 5), ((51, 32, 15), 4)]
    x = 0.0
    while x < d.w:
        for col, w in wood:
            ww = w * c.s
            pygame.draw.rect(s, col, (int(x), 0, max(1, int(round(ww))), d.h))
            x += ww
    # brushed metal panel 28,28 904x664, vertical gradient + hairlines, double inset border
    panel = c.R(28, 28, 904, 664)
    metal = pygame.Surface(panel.size)
    a, b = (220, 217, 210), (196, 193, 184)
    for yy in range(panel.h):
        t = yy / max(1, panel.h - 1)
        col = [int(a[i] + (b[i] - a[i]) * t) for i in range(3)]
        if yy % 2 == 0:
            col = [min(255, v + 6) for v in col]
        metal.fill(col, (0, yy, panel.w, 1))
    mask = pygame.Surface(panel.size, pygame.SRCALPHA)
    pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=c.L(6))
    metal = metal.convert_alpha(); metal.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    s.blit(metal, panel)
    pygame.draw.rect(s, (239, 236, 229), panel, max(1, c.L(2)), border_radius=c.L(6))
    pygame.draw.rect(s, (157, 154, 146), panel.inflate(-2 * c.L(2), -2 * c.L(2)), max(1, c.L(2)), border_radius=c.L(6))
    # wordmark row (y 50..90)
    c.text(s, "STEREO·TV", "jost6", 30, FACE_INK, c.X(54), c.Y(70), 0.28, anchor="midleft")
    c.text(s, "HI-FI CHANNEL RECEIVER", "jost5", 16, FACE_MUTED, c.X(906), c.Y(70), 0.24, anchor="midright")
    if kind == "now":
        for side, x0, label in ((0, 54, "LEFT · LEVEL"), (1, 676, "RIGHT · LEVEL")):
            _vu_face(c, s, x0, 164)
            c.text(s, label, "jost5", 14, FACE_MUTED, c.X(x0 + 115), c.Y(164 + 170 + 14), 0.24, anchor="midtop")
        _display_box(c, s, pygame.Rect(c.X(300), c.Y(106), c.L(360), c.L(320)))
    elif kind == "similar":
        _display_box(c, s, pygame.Rect(c.X(54), c.Y(106), c.L(852), c.L(320)))
    else:
        win = _face_window(c)
        _display_box(c, s, win)
        pygame.draw.rect(s, (0, 0, 0, 0), win.inflate(-2 * c.L(6), -2 * c.L(6)), border_radius=c.L(8))   # the hole
    dial_y = 442 if kind in ("now", "similar") else 554
    _dial_box(c, s, dial_y)
    if kind in ("now", "similar"):
        _knobs(c, s)
    c.static[key] = s
    return s


def _face_window(c: Ctx) -> pygame.Rect:
    return pygame.Rect(c.X(54), c.Y(106), c.L(852), c.L(432))


def _display_box(c: Ctx, s, r: pygame.Rect) -> None:
    pygame.draw.rect(s, (12, 9, 6), r, border_radius=c.L(12))
    pygame.draw.rect(s, (42, 39, 34), r, max(2, c.L(6)), border_radius=c.L(12))
    # inset shadow: a few darker rings inside the bezel
    for i in range(1, 5):
        pygame.draw.rect(s, (4, 3, 2, 60), r.inflate(-2 * (c.L(6) + i), -2 * (c.L(6) + i)), 1, border_radius=c.L(10))


def _vu_face(c: Ctx, s, x0, y0) -> None:
    """The 230x170 SVG meter face (static parts)."""
    face = c.R(x0, y0, 230, 170)
    pygame.draw.rect(s, (239, 227, 189), face, border_radius=c.L(10))
    pygame.draw.rect(s, FACE_INK, face, max(2, c.L(4)), border_radius=c.L(10))
    ticks = [((34.7, 77.7), (43.7, 85.8), 0), ((51.5, 62.6), (58.6, 72.3), 0), ((71.1, 51.3), (76, 62.3), 0),
             ((92.5, 44.4), (95, 56.1), 0), ((115, 42), (115, 54), 0), ((137.5, 44.4), (135, 56.1), 0),
             ((158.9, 51.3), (154, 62.3), 1), ((178.5, 62.6), (171.4, 72.3), 1), ((195.3, 77.7), (186.3, 85.8), 1)]
    for (ax, ay), (bx, by), red in ticks:
        pygame.draw.line(s, (179, 38, 30) if red else FACE_INK, (c.X(x0 + ax), c.Y(y0 + ay)), (c.X(x0 + bx), c.Y(y0 + by)), max(2, c.L(4 if red else 3)))
    c.text(s, "VU", "jost6", 24, FACE_INK, c.X(x0 + 115), c.Y(y0 + 112), 4 / 24, anchor="midbottom")
    pygame.draw.rect(s, FACE_INK, c.R(x0, y0 + 146, 230, 24), border_bottom_left_radius=c.L(10), border_bottom_right_radius=c.L(10))


def _vu_needle(c: Ctx, surf, x0, y0, level: float) -> None:
    """level 0..1 across the tick sweep (±48° about vertical, pivot at 115,150, length ~100)."""
    ang = math.radians(-48 + 96 * max(0.0, min(1.0, level)))
    px, py = x0 + 115, y0 + 150
    tx, ty = px + math.sin(ang) * 99, py - math.cos(ang) * 99
    clip = surf.get_clip()
    surf.set_clip(c.R(x0 + 2, y0 + 2, 226, 144))
    pygame.draw.line(surf, (17, 17, 17), (c.X(px), c.Y(py)), (c.X(tx), c.Y(ty)), max(2, c.L(3)))
    surf.set_clip(clip)
    pygame.draw.circle(surf, (17, 17, 17), (c.X(px), c.Y(py)), c.L(9))


def _dial_box(c: Ctx, s, y) -> None:
    box = c.R(54, y, 852, 96)
    pygame.draw.rect(s, (22, 17, 12), box, border_radius=c.L(8))
    pygame.draw.rect(s, (122, 118, 109), box, max(2, c.L(4)), border_radius=c.L(8))
    inner_x0, inner_w = 54 + 4 + 16, 852 - 8 - 32
    n = 16
    slot = inner_w / n
    # numbers
    for i in range(n):
        c.text(s, str(i + 1), "jost5", 24, (233, 214, 164), c.X(inner_x0 + slot * (i + 0.5)), c.Y(y + 4 + 10), anchor="midtop")
    # tick row: 1px lines every ~17px (scaled)
    ty = c.Y(y + 4 + 10 + 30 + 2)
    step = 17.16 * inner_w / 812
    xx = inner_x0
    while xx < inner_x0 + inner_w:
        pygame.draw.line(s, (233, 214, 164), (c.X(xx), ty), (c.X(xx), ty + c.L(12)), 1)
        xx += step


def _dial_dynamic(c: Ctx, surf, y, current: int) -> None:
    inner_x0, inner_w = 54 + 4 + 16, 852 - 8 - 32
    slot = inner_w / 16
    ly = c.Y(y + 4 + 10 + 30 + 2 + 12 + 4)
    placed = []
    order = [current] + [i for i in range(1, 17) if i != current]
    for i in order:
        img = c.render(DIAL_LABELS.get(i, ""), "jost5", 12, (255, 207, 122) if i == current else (183, 154, 87), 0.1)
        r = img.get_rect(midtop=(c.X(inner_x0 + slot * (i - 0.5)), ly))
        if all(not r.inflate(c.L(10), 0).colliderect(q) for q in placed):
            surf.blit(img, r); placed.append(r)
    px = c.X(inner_x0 + slot * (current - 0.5))
    top, bot = c.Y(y + 6), c.Y(y + 90)
    glow = pygame.Surface((c.L(12), bot - top), pygame.SRCALPHA)
    glow.fill((232, 69, 44, 70))
    surf.blit(glow, (px - glow.get_width() // 2, top))
    pygame.draw.line(surf, (232, 69, 44), (px, top), (px, bot), max(2, c.L(3)))


def _knob_img(c: Ctx, deg: float) -> pygame.Surface:
    key = ("knob", deg)
    img = c.static.get(key)
    if img is None:
        n = c.L(60)
        yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
        r = np.hypot(xx - n / 2 + 0.5, yy - n / 2 + 0.5)
        dist = np.hypot(xx - 0.35 * n, yy - 0.30 * n) / (0.72 * n)          # radial gradient from the highlight
        stops = [(0.0, (244, 241, 234)), (0.6, (143, 139, 130)), (1.0, (75, 72, 66))]
        rgb = np.zeros((n, n, 3), np.float32)
        for (p0, c0), (p1, c1) in zip(stops[:-1], stops[1:]):
            t = np.clip((dist - p0) / (p1 - p0), 0, 1)[..., None]
            seg = (dist >= p0)[..., None] & (dist <= p1 + (0.001 if p1 == 1.0 else 0))[..., None]
            rgb = np.where(seg, np.array(c0) + (np.array(c1) - np.array(c0)) * t, rgb)
        rgb = np.where((dist > 1.0)[..., None], np.array((75, 72, 66), np.float32), rgb)
        alpha = np.clip((n / 2 - r) * 2, 0, 1) * 255
        surf = pygame.Surface((n, n), pygame.SRCALPHA)
        pygame.surfarray.pixels3d(surf)[:] = rgb.transpose(1, 0, 2).astype(np.uint8)
        pygame.surfarray.pixels_alpha(surf)[:] = alpha.T.astype(np.uint8)
        # pointer bar 4x20 from 4px below the top, then rotate the whole knob
        pygame.draw.rect(surf, (26, 24, 21), (n // 2 - c.L(2), c.L(4), max(2, c.L(4)), c.L(20)), border_radius=c.L(2))
        img = c.static[key] = pygame.transform.rotozoom(surf, -deg, 1.0)
    return img


def _knobs(c: Ctx, s) -> None:
    y = 554
    x = 54
    for label, deg in (("VOLUME", 40), ("BASS", -10), ("TREBLE", 15), ("BALANCE", 0)):
        lab = c.render(label, "jost5", 13, FACE_INK, 0.22)
        colw = max(c.L(60), lab.get_width())
        block_h = c.L(60) + c.L(8) + lab.get_height()
        top = c.Y(y) + (c.L(96) - block_h) // 2
        cx = c.X(x) + colw // 2
        sh = pygame.Surface((c.L(64), c.L(64)), pygame.SRCALPHA)
        pygame.draw.circle(sh, (0, 0, 0, 90), (c.L(32), c.L(32)), c.L(31))
        s.blit(sh, sh.get_rect(center=(cx, top + c.L(30) + c.L(3))))
        k = _knob_img(c, deg)
        s.blit(k, k.get_rect(center=(cx, top + c.L(30))))
        s.blit(lab, lab.get_rect(midtop=(cx, top + c.L(60) + c.L(8))))
        x += colw / c.s + 34


def _lamps(c: Ctx, surf, now) -> None:
    labels = [("PHONO", now.release is not None), ("SIDE A", (now.side or "").upper() == "A"), ("SIDE B", (now.side or "").upper() == "B")]
    x_right = c.X(906)
    imgs = [c.render(l, "jost5", 13, FACE_INK, 0.22) for l, _ in labels]
    widths = [max(c.L(14), im.get_width()) for im in imgs]
    total = sum(widths) + c.L(26) * (len(widths) - 1)
    x = x_right - total
    y = c.Y(554)
    for (label, lit), im, w in zip(labels, imgs, widths):
        block_h = c.L(14) + c.L(8) + im.get_height()
        top = y + (c.L(96) - block_h) // 2
        cx = x + w // 2
        if lit:
            g = pygame.Surface((c.L(34), c.L(34)), pygame.SRCALPHA)
            pygame.draw.circle(g, (*AMBER, 60), (c.L(17), c.L(17)), c.L(16))
            pygame.draw.circle(g, (*AMBER, 90), (c.L(17), c.L(17)), c.L(11))
            surf.blit(g, g.get_rect(center=(cx, top + c.L(7))))
        pygame.draw.circle(surf, AMBER if lit else (107, 90, 58), (cx, top + c.L(7)), c.L(7))
        surf.blit(im, im.get_rect(midtop=(cx, top + c.L(14) + c.L(8))))
        x += w + c.L(26)


_vu_state = {"lvl": np.zeros(2), "t": 0.0}


def _vu_levels(app) -> np.ndarray:
    a = app.audio
    now_t = time.monotonic()
    dt = min(0.2, now_t - _vu_state["t"]) if _vu_state["t"] else 1 / 30
    _vu_state["t"] = now_t
    target = np.zeros(2)
    if a and a.alive:
        st = a.latest_stereo(0.12)
        if len(st):
            rms = np.sqrt((st * st).mean(axis=0)) + 1e-9
            target = np.clip((20 * np.log10(rms) + 10 + 20) / 23.0, 0.0, 1.0)
    lv = _vu_state["lvl"]
    k_up, k_dn = min(1.0, dt / 0.06), min(1.0, dt / 0.22)
    _vu_state["lvl"] = np.where(target > lv, lv + (target - lv) * k_up, lv + (target - lv) * k_dn)
    return _vu_state["lvl"]


def _face_now(app, surf) -> None:
    c, now = ctx(app.d), app.now
    surf.blit(_face_static(c, "now"), (0, 0))
    lv = _vu_levels(app)
    _vu_needle(c, surf, 54, 164, float(lv[0]))
    _vu_needle(c, surf, 676, 164, float(lv[1]))
    # display content: box 300,106 360x320, border 6, padding 18 22 -> inner 328..632 x 130..402
    rel = now.release
    x0, x1, top, bot = c.X(328), c.X(632), c.Y(130), c.Y(402)
    w = x1 - x0
    if rel:
        src = "PHONO" if rel.release_id > 0 else "AUX"
        line1 = " · ".join(p for p in (f"CH {app.cur:02d}", src, f"SIDE {now.side}" if now.side else "") if p)
        title = rel.title.upper()
        artist = rel.artist.upper()
        line3 = " · ".join(p for p in (f"{int(now.track):02d}" if (now.track or "").isdigit() else (now.track or ""), (now.track_title or "").upper()) if p) \
            or f"{rel.year or ''} {(rel.label or '').split(',')[0].upper()}".strip()
    else:
        line1, title, artist, line3 = f"CH {app.cur:02d} · STANDBY", "NO SIGNAL", "DROP THE NEEDLE", \
            (f"LAST · {now.last_played.title.upper()}" if now.last_played else "")
    c.glow_text(surf, c.ellipsize(line1, "vt", 26, w, 0.08), "vt", 26, AMBER, x0, top, 0.08, alpha=217)
    tsize = c.fit(title, "vt", 82, 56, w)
    tlines = [title]
    if c.render(title, "vt", tsize, AMBER).get_width() > w:
        words = title.split()
        best = None
        for sz in range(66, 38, -2):                 # split into two lines at the largest size that fits
            for k in range(1, len(words)):
                a_, b_ = " ".join(words[:k]), " ".join(words[k:])
                if max(c.render(a_, "vt", sz, AMBER).get_width(), c.render(b_, "vt", sz, AMBER).get_width()) <= w:
                    best = (sz, [a_, b_]); break
            if best:
                break
        tsize, tlines = best if best else (40, [c.ellipsize(title, "vt", 40, w)])
    prog = _progress(now) if rel else None
    # vertical rhythm of the flex column: distribute the leftover space into three gaps
    heights = [c.font("vt", 26).get_height(), int(c.font("vt", tsize).get_height() * 0.9) * len(tlines) + c.font("vt", 40).get_height(),
               c.font("vt", 34).get_height(), c.font("vt", 30).get_height() if prog else 0]
    free = (bot - top) - sum(heights)
    g = free // 3
    y = top + heights[0] + g
    for ln in tlines:
        c.glow_text(surf, c.ellipsize(ln, "vt", tsize, w), "vt", tsize, AMBER, x0, y)
        y += int(c.font("vt", tsize).get_height() * 0.9)
    c.glow_text(surf, c.ellipsize(artist, "vt", 40, w), "vt", 40, AMBER, x0, y)
    y += c.font("vt", 40).get_height() + g
    if line3:
        c.glow_text(surf, c.ellipsize(line3, "vt", 34, w), "vt", 34, AMBER, x0, y)
    if prog:
        pct, tstr = prog
        tr = c.glow_text(surf, tstr, "vt", 30, AMBER, x1, bot, anchor="bottomright")
        bw = tr.left - c.L(14) - x0
        seg, gap_ = c.L(8), c.L(4)
        by = tr.centery - c.L(8)
        xx = x0
        while xx + seg <= x0 + bw:
            lit = (xx - x0 + seg / 2) / bw <= pct
            col = AMBER if lit else (70, 50, 18)
            pygame.draw.rect(surf, col, (xx, by, seg, c.L(16)))
            xx += seg + gap_
    _dial_dynamic(c, surf, 442, app.cur)
    _lamps(c, surf, now)


def _face_similar(app, surf) -> None:
    c, now = ctx(app.d), app.now
    surf.blit(_face_static(c, "similar"), (0, 0))
    rel = now.release or now.last_played
    x0, x1, top = c.X(54 + 6 + 26), c.X(906 - 6 - 26), c.Y(106 + 6 + 16)
    w = x1 - x0
    h = c.glow_text(surf, f"CH {app.cur:02d} · SIMILAR SOUNDS", "vt", 38, AMBER, x0, top, 0.06)
    c.glow_text(surf, "FROM YOUR SHELF", "vt", 26, AMBER, x1, h.bottom, anchor="bottomright", alpha=217)
    uy = h.bottom + c.L(4)
    xx = x0
    while xx < x1:
        pygame.draw.rect(surf, AMBER, (xx, uy, c.L(2), c.L(2)))
        xx += c.L(6)
    y = uy + c.L(2) + c.L(6)
    sims = _similar(app, rel) if rel else []
    cols = [44, None, 230, 70]
    for i in range(5):
        row = pygame.Rect(x0, y, w, c.L(44))
        r = sims[i][0] if i < len(sims) else None
        hot = i == 0 and r is not None
        if hot:
            pygame.draw.rect(surf, AMBER, row)
        vals = [f"{i + 1:02d}", r.title.upper() if r else "", r.artist.upper() if r else "", str(r.year or "") if r else ""]
        pad = c.L(12)
        flex = w - 2 * pad - c.L(44) - c.L(230) - c.L(70) - 3 * c.L(16)
        xs = [x0 + pad, x0 + pad + c.L(44) + c.L(16)]
        xs += [xs[1] + flex + c.L(16), xs[1] + flex + c.L(16) + c.L(230) + c.L(16)]
        widths = [c.L(44), flex, c.L(230), c.L(70)]
        for j, (xv, wv, v) in enumerate(zip(xs, widths, vals)):
            if not v:
                continue
            v = c.ellipsize(v, "vt", 34, wv)
            if hot:
                c.text(surf, v, "vt", 34, (12, 9, 6), xv + (wv if j == 3 else 0), row.centery, anchor="midright" if j == 3 else "midleft")
            else:
                c.glow_text(surf, v, "vt", 34, AMBER, xv + (wv if j == 3 else 0), row.centery, anchor="midright" if j == 3 else "midleft")
        y += c.L(44) + c.L(3)
    _dial_dynamic(c, surf, 442, app.cur)
    _lamps(c, surf, now)


def _face_frame(app, surf, ch) -> pygame.Rect:
    c = ctx(app.d)
    surf.blit(_face_static(c, "frame"), (0, 0))
    _dial_dynamic(c, surf, 554, app.cur)
    win = _face_window(c)
    return win.inflate(-2 * c.L(6 + 16), -2 * c.L(6 + 12))


# ================================================================ SLEEVE POSTER
def _sleeve_bg(c: Ctx, with_record: bool) -> pygame.Surface:
    key = ("sleeve_bg", with_record)
    bg = c.static.get(key)
    if bg is None:
        d = c.d
        bg = pygame.Surface((d.w, d.h)); bg.fill(PAPER)
        if with_record:
            cx, cy, r = c.X(490 + 350), c.Y(130 + 350), c.L(350)
            pygame.draw.circle(bg, (17, 17, 17), (cx, cy), r)
            period = 5 * c.s
            rr = float(r)
            while rr > c.L(105):
                pygame.draw.circle(bg, (34, 34, 34), (cx, cy), int(rr), 1)
                rr -= period
            pygame.draw.circle(bg, POSTER_RED, (cx, cy), c.L(105))
            c.text(bg, "SIDE A", "archivo", 26, PAPER, cx, cy, 0.04, anchor="center")
        c.static[key] = bg
    return bg


def _masthead(c: Ctx, surf, chnum: int) -> int:
    y = c.Y(32)
    lab = c.render(f"CH {chnum:02d}", "mono7", 15, PAPER, 0.12)
    left = c.text(surf, "STEREO-TV", "mono7", 15, INK, c.X(40), y + c.L(2), 0.12)
    badge = pygame.Rect(0, y, lab.get_width() + c.L(24), lab.get_height() + c.L(4))
    badge.centerx = c.X(480)
    pygame.draw.rect(surf, POSTER_RED, badge)
    surf.blit(lab, lab.get_rect(center=badge.center))
    c.text(surf, _date_short("sleeve").upper(), "mono7", 15, INK, c.X(920), y + c.L(2), 0.12, anchor="topright")
    rule_y = badge.bottom + c.L(12)
    pygame.draw.rect(surf, INK, (c.X(40), rule_y, c.L(880), max(2, c.L(2))))
    return rule_y + c.L(2)


def _difference_title(c: Ctx, surf, text: str, size: int, x: int, y: int) -> None:
    """CSS mix-blend-mode:difference with #f1ede4 text: dark on paper, pale where it crosses the record."""
    key = ("difftitle", text, size, x, y)
    patch = c.static.get(key)
    if patch is None:
        glyphs = c.render(text, "archivo", size, (255, 255, 255), -0.04)
        r = glyphs.get_rect(topleft=(x, y)).clip(surf.get_rect())
        if r.w <= 0 or r.h <= 0:
            return
        bg = pygame.surfarray.array3d(surf.subsurface(r)).astype(np.int16)
        cov = pygame.surfarray.array_alpha(glyphs)[: r.w, : r.h].astype(np.float32)[..., None] / 255.0
        diff = np.abs(bg - np.array(PAPER, np.int16))
        out = (bg * (1 - cov) + diff * cov).astype(np.uint8)
        patch = pygame.surfarray.make_surface(out)
        c.static[key] = (patch, r.topleft)
    else:
        patch = patch
    img, pos = c.static[key]
    surf.blit(img, pos)


def _sleeve_now(app, surf) -> None:
    c, now = ctx(app.d), app.now
    rel = now.release
    shown = rel or now.last_played
    surf.blit(_sleeve_bg(c, True), (0, 0))
    if rel and now.side:
        # relabel the record for the playing side (cached per side)
        key = ("sidelabel", now.side)
        if key not in c.static:
            lab = pygame.Surface((c.L(200), c.L(60)), pygame.SRCALPHA)
            c.static[key] = lab
        cx, cy = c.X(840), c.Y(480)
        pygame.draw.circle(surf, POSTER_RED, (cx, cy), c.L(105))
        c.text(surf, f"SIDE {now.side}", "archivo", 26, PAPER, cx, cy, 0.04, anchor="center")
    pygame.draw.circle(surf, PAPER, (c.X(840), c.Y(480)), c.L(9))
    _masthead(c, surf, app.cur)
    c.text(surf, "NOW SPINNING" if rel else "OFF AIR", "mono7", 16, POSTER_RED, c.X(44), c.Y(112), 0.24)
    title = shown.title if shown else "Silence"
    tsize = 156
    while tsize > 100 and c.render(title, "archivo", tsize, (0, 0, 0), -0.04).get_width() > c.L(660):
        tsize -= 4
    if c.render(title, "archivo", tsize, (0, 0, 0), -0.04).get_width() <= c.L(660):
        _difference_title(c, surf, title, tsize, c.X(38), c.Y(140) - c.L(tsize * 0.08))
    else:
        words, best = title.split(), None
        for sz in range(96, 46, -4):                 # two lines; the second may run under the record
            for k in range(1, len(words)):
                a_, b_ = " ".join(words[:k]), " ".join(words[k:])
                if c.render(a_, "archivo", sz, (0, 0, 0), -0.04).get_width() <= c.L(650) and \
                        c.render(b_, "archivo", sz, (0, 0, 0), -0.04).get_width() <= c.L(900):
                    best = (sz, a_, b_); break
            if best:
                break
        sz, a_, b_ = best if best else (48, c.ellipsize(title, "archivo", 48, c.L(650), -0.04), "")
        lh = int(c.font("archivo", sz).get_height() * 0.9)
        y0 = c.Y(140) + (c.L(172) - 2 * lh) // 2 - c.L(sz * 0.06)
        _difference_title(c, surf, a_, sz, c.X(38), y0)
        if b_:
            _difference_title(c, surf, b_, sz, c.X(38), y0 + lh)
    artist = shown.artist if shown else "Drop the needle"
    asize = c.fit(artist, "archivo", 44, 26, c.L(620), -0.01)
    c.text(surf, artist, "archivo", asize, POSTER_RED, c.X(44), c.Y(312), -0.01)
    # spec grid 44,392 w470: LABEL / YEAR / SPEED
    if shown:
        fm = (shown.formats or "")
        speed = "45 RPM" if ('7"' in fm or "Single" in fm or "45 RPM" in fm) else "33⅓ RPM"
        if c.font("mono7", 20).metrics("⅓")[0] is None:
            speed = speed.replace("⅓", " 1/3")
        cells = [("LABEL", (shown.label or "").split(",")[0].strip()), ("YEAR", str(shown.year or "")), ("SPEED", speed if shown.release_id > 0 else "")]
        cw = (470 - 2 * 16) / 3
        for i, (k, v) in enumerate(cells):
            x = 44 + i * (cw + 16)
            pygame.draw.rect(surf, INK, c.R(x, 392, cw, 2))
            c.text(surf, k, "mono4", 12, (85, 85, 85), c.X(x), c.Y(402), 0.16)
            words, lines, cur = v.split(), [], ""
            for wd in words:
                t_ = (cur + " " + wd).strip()
                if c.render(t_, "mono7", 20, INK).get_width() <= c.L(cw) or not cur:
                    cur = t_
                else:
                    lines.append(cur); cur = wd
            if cur:
                lines.append(cur)
            yy = c.Y(402 + 18)
            for ln in lines[:2]:
                c.text(surf, c.ellipsize(ln, "mono7", 20, c.L(cw)), "mono7", 20, INK, c.X(x), yy); yy += c.font("mono7", 20).get_height()
    # cover art (on top of the title), 700,92 220x220, 3px ink border
    box = c.R(700, 92, 220, 220)
    img = _cover(c, shown.cover_path if shown else None, box.w - 2 * c.L(3))
    pygame.draw.rect(surf, INK, box)
    inner = box.inflate(-2 * c.L(3), -2 * c.L(3))
    if img:
        surf.blit(img, inner)
    else:
        pygame.draw.rect(surf, PAPER, inner)
        step = c.L(20)
        clip = surf.get_clip(); surf.set_clip(inner)
        for k in range(inner.left - inner.h, inner.right, step):
            pygame.draw.line(surf, (220, 214, 200), (k, inner.bottom), (k + inner.h, inner.top), c.L(10))
        surf.set_clip(clip)
        c.text(surf, "COVER ART", "mono7", 16, INK, inner.centerx, inner.centery, 0.24, anchor="center")
    # bottom band: 720-170, border-top 2, progress + track
    band = c.R(0, 550, 960, 170)
    pygame.draw.rect(surf, PAPER, band)
    pygame.draw.rect(surf, INK, (band.x, band.y, band.w, max(2, c.L(2))))
    prog = _progress(now) if rel else None
    bar = c.R(40, 570, 880, 8)
    pygame.draw.rect(surf, (212, 208, 200), bar)
    pct = prog[0] if prog else 0.0
    pygame.draw.rect(surf, INK, (bar.x, bar.y, int(bar.w * pct), bar.h))
    if prog:
        pygame.draw.circle(surf, POSTER_RED, (bar.x + int(bar.w * pct), bar.centery), c.L(10))
    base_y = c.Y(570 + 8 + 18 + 72 * 0.8)
    pos = f"{now.side or ''}{now.track or ''}" if rel else ""
    x = c.X(40)
    if pos:
        pr = c.text(surf, pos, "archivo", 72, POSTER_RED, x, base_y, anchor="bottomleft")
        x = pr.right + c.L(24)
    tr = c.text(surf, prog[1], "mono7", 26, INK, c.X(920), base_y - c.L(6), anchor="bottomright") if prog else pygame.Rect(c.X(920), 0, 0, 0)
    tname = (now.track_title or "") if rel else (f"Last: {now.last_played.title}" if now.last_played else "")
    if tname:
        size = c.fit(tname, "archivo", 68, 36, tr.left - c.L(24) - x, -0.03)
        c.text(surf, c.ellipsize(tname, "archivo", size, tr.left - c.L(24) - x, -0.03), "archivo", size, INK, x, base_y - c.L(4), -0.03, anchor="bottomleft")


def _sleeve_similar(app, surf) -> None:
    c, now = ctx(app.d), app.now
    rel = now.release or now.last_played
    surf.blit(_sleeve_bg(c, False), (0, 0))
    _masthead(c, surf, app.cur)
    c.text(surf, f"{app.cur:02d}", "archivo", 250, POSTER_RED, c.X(30), c.Y(74) - c.L(250 * 0.12), -0.06)
    c.text(surf, "Similar", "archivo", 48, INK, c.X(44), c.Y(330), -0.02)
    c.text(surf, "Sounds", "archivo", 48, INK, c.X(44), c.Y(330 + 48), -0.02)
    # description, 16px, width 300, line-height 1.5, with the album in bold
    if rel:
        parts = [("Because you're spinning ", "mono4"), (rel.title, "mono7"), (f" by {rel.artist}. Picked from your shelf by year and style.", "mono4")]
    else:
        parts = [("Spin a record and the shelf answers back.", "mono4")]
    words = []
    for text, key in parts:
        for i, wd in enumerate(text.split(" ")):
            if wd:
                words.append((wd, key))
    x0, maxw, y = c.X(44), c.L(300), c.Y(452)
    lh = int(c.font("mono4", 16).get_height() * 1.5 * 16 / 24)  # CSS line-height 1.5 on a 16px font
    x = x0
    space = c.render(" ", "mono4", 16, INK).get_width()
    for wd, key in words:
        img = c.render(wd, key, 16, INK)
        if x > x0 and x + img.get_width() > x0 + maxw:
            x, y = x0, y + lh
        surf.blit(img, (x, y)); x += img.get_width() + space
    # list: left 400, right 40, top 92, rows 96
    sims = _similar(app, rel) if rel else []
    lx, lw = 400, 520
    pygame.draw.rect(surf, INK, c.R(lx, 92, lw, 2))
    y = 94
    for i in range(5):
        r = sims[i][0] if i < len(sims) else None
        hot = i == 0 and r is not None
        row = c.R(lx, y, lw, 96)
        if hot:
            pygame.draw.rect(surf, POSTER_RED, row)
        elif i > 0:
            pygame.draw.rect(surf, INK, (row.x, row.y - c.L(2), row.w, max(2, c.L(2))))
        col = PAPER if hot else INK
        c.text(surf, f"{i + 1:02d}", "mono7", 18, col, c.X(lx + 14), c.Y(y + 12 + 8))
        if r:
            yr = c.text(surf, str(r.year or ""), "mono7", 18, col, c.X(lx + lw - 14), c.Y(y + 12 + 8), anchor="topright")
            tw = yr.left - c.L(8) - c.X(lx + 14 + 44 + 8)
            c.text(surf, c.ellipsize(r.title, "archivo", 28, tw), "archivo", 28, col, c.X(lx + 14 + 44 + 8), c.Y(y + 12))
            c.text(surf, c.ellipsize(r.artist, "mono4", 16, tw), "mono4", 16, col, c.X(lx + 14 + 44 + 8), c.Y(y + 12 + 31 + 6))
        y += 96
    pygame.draw.rect(surf, INK, c.R(lx, y - 2, lw, 2))
    n = len(sims)
    fr = c.text(surf, f"{n} ON SHELF", "mono7", 16, POSTER_RED, c.X(lx) + c.L(18), c.Y(720 - 40), 0.1, anchor="bottomleft")
    pygame.draw.circle(surf, POSTER_RED, (c.X(lx) + c.L(6), fr.centery), c.L(6))
    nxt = app.cur % len(app.channels) + 1
    nname = getattr(app.channels.get(nxt), "name", "").title()
    c.text(surf, f"Next: CH {nxt:02d} {nname} →".upper(), "mono7", 16, INK, c.X(lx + lw), c.Y(720 - 40), 0.1, anchor="bottomright")


def _sleeve_frame(app, surf, ch) -> pygame.Rect:
    c = ctx(app.d)
    top = _masthead(c, surf, app.cur)
    return pygame.Rect(c.X(40), top + c.L(16), c.L(880), c.Y(720 - 32) - top - c.L(16))


# ================================================================ dispatch
CUSTOM = {
    "guide": {1: _guide_now, 4: _guide_similar},
    "faceplate": {1: _face_now, 4: _face_similar},
    "sleeve": {1: _sleeve_now, 4: _sleeve_similar},
}
FRAMES = {"guide": _guide_frame, "faceplate": _face_frame, "sleeve": _sleeve_frame}


def draw(app, ch) -> None:
    d = app.d
    design = d.style.get("design")
    surf = d.surface
    fn = CUSTOM.get(design, {}).get(app.cur)
    if fn:
        fn(app, surf)
        return
    # ordinary channel inside the theme's frame: shrink the safe area, draw, then lay the frame over it
    saved = d.safe
    c = ctx(d)
    if design == "faceplate":
        win = _face_window(c)
        d.safe = win.inflate(-2 * c.L(6 + 16), -2 * c.L(6 + 12))
    elif design == "guide":
        d.safe = pygame.Rect(c.X(24), c.Y(56 + 12), c.L(912), c.L(720 - 56 - 12 - 16))
    else:
        d.safe = pygame.Rect(c.X(40), c.Y(84), c.L(880), c.L(720 - 84 - 32))
    try:
        ch.draw(surf)
    finally:
        d.safe = saved
    if design == "faceplate":
        _face_frame(app, surf, ch)
    elif design == "guide":
        _guide_header(c, surf)
    else:
        # masthead sits on a paper strip so channel content never shows through it
        pygame.draw.rect(surf, PAPER, (0, 0, d.w, c.Y(84) - c.L(6)))
        _masthead(c, surf, app.cur)


def header(chan, surface, right: str = "") -> pygame.Rect:
    """In-frame header for ordinary channels; returns the bar rect (content starts below it)."""
    d = chan.d
    c = ctx(d)
    design = d.style.get("design")
    s = d.safe
    name = "SIMILAR SOUNDS" if chan.number == 4 else chan.name
    if design == "guide":
        lab = c.render(f"CH {chan.number:02d}", "barlow7", 30, GUIDE_NAVY, 0.06)
        badge = pygame.Rect(s.left, s.top, lab.get_width() + c.L(24), lab.get_height())
        pygame.draw.rect(surface, GUIDE_ACCENT, badge)
        surface.blit(lab, lab.get_rect(center=badge.center))
        left = c.text(surface, name, "barlow7", 40, (255, 255, 255), badge.right + c.L(14), badge.centery, 0.04, anchor="midleft")
        room = s.right - left.right - c.L(20)
        if right and room > c.L(80):
            c.text(surface, c.ellipsize(right, "barlow5", 26, room), "barlow5", 26, GUIDE_SUB, s.right, badge.centery, anchor="midright")
        return pygame.Rect(s.left, s.top, s.width, badge.height + c.L(6))
    if design == "faceplate":
        h = c.glow_text(surface, f"CH {chan.number:02d} · {name}", "vt", 38, AMBER, s.left, s.top, 0.06)
        room = s.right - h.right - c.L(20)
        if right and room > c.L(80):
            c.glow_text(surface, c.ellipsize(right.upper(), "vt", 26, room), "vt", 26, AMBER, s.right, h.bottom, anchor="bottomright", alpha=217)
        uy = h.bottom + c.L(4)
        xx = s.left
        while xx < s.right:
            pygame.draw.rect(surface, AMBER, (xx, uy, c.L(2), c.L(2))); xx += c.L(6)
        return pygame.Rect(s.left, s.top, s.width, uy + c.L(8) - s.top)
    # sleeve
    t = c.text(surface, name.title(), "archivo", 34, INK, s.left, s.top, -0.02)
    room = s.right - t.right - c.L(20)
    if right and room > c.L(80):
        c.text(surface, c.ellipsize(right.upper(), "mono7", 14, room), "mono7", 14, POSTER_RED, s.right, t.bottom - c.L(6), 0.1, anchor="bottomright")
    return pygame.Rect(s.left, s.top, s.width, t.height + c.L(8))
