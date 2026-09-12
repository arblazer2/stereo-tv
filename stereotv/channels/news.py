"""Channels 13 (Music News) and 14 (This Day in Music) — card pages over cached feeds."""
from __future__ import annotations

import time

import pygame

from stereotv import display as D
from stereotv import news as N
from stereotv.channels.base import Channel

PAGE_S = 14.0


def _ago(ts: float) -> str:
    if not ts:
        return ""
    m = int((time.time() - ts) // 60)
    if m < 60:
        return f"{m} MIN AGO"
    if m < 60 * 36:
        return f"{m // 60} HR AGO"
    return f"{m // 1440} DAYS AGO"


class _CardChannel(Channel):
    """Shared: one item per page, wrapped title + summary, source strip, page dots."""
    accent_name = "YELLOW"
    bg_name = "NAVY"
    empty_text = "NOTHING TO SHOW"

    @property
    def accent(self):
        return getattr(D, self.accent_name)

    @property
    def bg(self):
        return getattr(D, self.bg_name)

    def __init__(self, display, now):
        super().__init__(display, now)
        self._i = 0
        self._t = 0.0
        self._ver = -1
        self._card: pygame.Surface | None = None
        self._card_key = None

    def cache(self) -> N.Cache:
        raise NotImplementedError

    def enter(self) -> None:
        self.cache().get()

    def update(self, dt: float) -> None:
        items = self.cache().get()
        if self.cache().version != self._ver:
            self._ver = self.cache().version
            self._i, self._t = 0, 0.0
        self._t += dt
        if items and self._t >= PAGE_S:
            self._t = 0.0
            self._i = (self._i + 1) % len(items)

    def meta_line(self, item: N.Item) -> str:
        return _ago(item.when)

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(self.bg)
        bar = self.header(surface, self.now_line())
        safe = d.safe
        items = self.cache().items
        if not items:
            if self.cache().fetched_at and not self.cache().error.startswith(("HTTP", "Conn")):
                msg = self.empty_text
            else:
                msg = "LOADING…" if not self.cache().error else f"NO FEED: {self.cache().error}"
            d.text(d.fit_text(msg, "md", safe.width), "md", D.GREY, (safe.left, bar.bottom + 20))
            return
        i = self._i % len(items)
        key = (self._ver, i)
        if self._card_key != key:
            self._card = self._render_card(items[i], safe.width, safe.bottom - bar.bottom - 30)
            self._card_key = key
        surface.blit(self._card, (safe.left, bar.bottom + 12))
        n = len(items)
        d.text(f"{i + 1}/{n}", "sm", D.CYAN, (safe.right, safe.bottom - 24), anchor="topright", shadow=False)
        # progress bar for the page timer (2 px tall, no 1-px lines)
        w = int(safe.width * min(1.0, self._t / PAGE_S))
        pygame.draw.rect(surface, self.accent, (safe.left, safe.bottom - 4, w, 3))

    def _render_card(self, item: N.Item, w: int, h: int) -> pygame.Surface:
        d = self.d
        s = pygame.Surface((w, h))
        s.fill(self.bg)
        # source strip
        strip = pygame.Rect(0, 0, w, 30)
        pygame.draw.rect(s, self.accent, strip)
        meta = self.meta_line(item)
        metaw = d.fonts["sm"].size(meta)[0] + 24 if meta else 0
        d.text(d.fit_text(item.source, "sm", w - 16 - metaw), "sm", D.BLACK, (8, strip.centery),
               anchor="midleft", shadow=False, target=s)
        if meta:
            d.text(meta, "sm", D.BLACK, (w - 8, strip.centery), anchor="midright", shadow=False, target=s)
        y = 40
        for ln in d.wrap(item.title, "md", w - 8)[:4]:
            d.text(ln, "md", D.WHITE, (0, y), shadow=False, target=s)
            y += 32
        y += 8
        if item.summary and y < h - 30:
            for ln in d.wrap(item.summary, "sm", w - 8):
                if y > h - 40:
                    break
                d.text(ln, "sm", D.GREY, (0, y), shadow=False, target=s)
                y += 26
        return s


class MusicNewsChannel(_CardChannel):
    number = 13
    name = "MUSIC NEWS"
    accent_name = "YELLOW"

    def __init__(self, display, now, feeds=None):
        super().__init__(display, now)
        feeds = [tuple(f) for f in (feeds or N.DEFAULT_FEEDS)]
        self._cache = N.Cache(lambda: N.fetch_feeds(feeds), 1800)

    def cache(self) -> N.Cache:
        return self._cache


class ThisDayChannel(_CardChannel):
    number = 14
    name = "THIS DAY IN MUSIC"
    accent_name = "AMBER"
    bg_name = "DARK"
    empty_text = "QUIET DAY IN MUSIC HISTORY"

    def __init__(self, display, now):
        super().__init__(display, now)
        self._day = None
        self._cache = N.Cache(self._fetch, 6 * 3600)

    def _fetch(self):
        t = time.localtime()
        return N.fetch_this_day(t.tm_mon, t.tm_mday)

    def cache(self) -> N.Cache:
        today = time.strftime("%m-%d")
        if self._day != today:                      # new day: force refetch
            self._day = today
            self._cache.fetched_at = 0.0
        return self._cache

    def meta_line(self, item: N.Item) -> str:
        # anniversaries already say "today"/"this week" in the label; date only on Wikipedia events
        if item.source == "ON THIS DAY":
            return time.strftime("%B %d").upper().replace(" 0", " ")
        return ""
