"""Music news via RSS (stdlib XML) and 'this day in music' via Wikipedia's on-this-day feed. Cached."""
from __future__ import annotations

import html
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import requests

log = logging.getLogger("stereotv.news")

from stereotv import config as _cfg
UA = _cfg.user_agent()
DEFAULT_FEEDS = [
    ("PITCHFORK", "https://pitchfork.com/feed/feed-news/rss"),
    ("ROLLING STONE", "https://www.rollingstone.com/music/music-news/feed/"),
    ("STEREOGUM", "https://www.stereogum.com/feed/"),
    ("NME", "https://www.nme.com/news/music/feed"),
]
MUSIC_WORDS = re.compile(r"\b(album|single|song|band|singer|musician|guitarist|drummer|bassist|pianist|composer|"
                         r"rapper|dj|record label|billboard|concert|tour|rock|jazz|blues|soul|country music|"
                         r"hip hop|punk|metal|grammy|beatles|elvis|dylan|motown|vinyl|lp|orchestra|symphony|opera|"
                         r"songwriter|producer|vocalist|saxophonist|violinist|trumpeter|music)\b", re.I)
_TAG = re.compile(r"<[^>]+>")
# for people: the Wikipedia short description must say they made music
PERSON_WORDS = re.compile(r"\b(singer|musician|composer|band|rapper|guitarist|drummer|pianist|songwriter|conductor|"
                          r"bassist|saxophonist|violinist|trumpeter|vocalist|cellist|organist|record producer|"
                          r"disc jockey|dj|bandleader|lyricist|music)\b", re.I)


@dataclass
class Item:
    source: str
    title: str
    summary: str = ""
    when: float = 0.0
    link: str = ""


def _clean(s: str) -> str:
    s = html.unescape(_TAG.sub(" ", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def fetch_feeds(feeds: list[tuple[str, str]], per_feed: int = 6) -> list[Item]:
    s = requests.Session()
    s.headers["User-Agent"] = UA
    items: list[Item] = []
    for name, url in feeds:
        try:
            r = s.get(url, timeout=15)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            n = 0
            for it in root.iter("item"):
                title = _clean(it.findtext("title", ""))
                if not title:
                    continue
                desc = _clean(it.findtext("description", "") or it.findtext("{http://purl.org/rss/1.0/modules/content/}encoded", ""))
                when = 0.0
                try:
                    when = parsedate_to_datetime(it.findtext("pubDate", "")).timestamp()
                except (TypeError, ValueError):
                    pass
                items.append(Item(name, title, desc[:280], when, it.findtext("link", "") or ""))
                n += 1
                if n >= per_feed:
                    break
        except Exception as e:  # noqa: BLE001
            log.warning("feed %s: %s", name, e)
    items.sort(key=lambda i: i.when, reverse=True)
    return items


def collection_anniversaries(month: int, day: int, window_days: int = 0) -> list[Item]:
    """Records in the collection first released on this month/day (MusicBrainz original date,
    falling back to the Discogs pressing date). window_days>0 widens to ±N days."""
    import datetime as dt
    from stereotv import discogs
    out = []
    try:
        con = discogs.open_db()
        cols = {r[1] for r in con.execute("PRAGMA table_info(releases)")}
        date_expr = "COALESCE(NULLIF(first_release,''), released)" if "first_release" in cols else "released"
        rows = con.execute(f"SELECT artist, title, {date_expr} AS d, label FROM releases "
                           f"WHERE length({date_expr})=10 AND {date_expr} NOT LIKE '%-00'").fetchall()
        con.close()
    except Exception as e:  # noqa: BLE001
        log.warning("anniversaries: %s", e)
        return out
    today = dt.date(2000, month, day)          # leap-safe comparison year
    for r in rows:
        try:
            y, m, dd = (int(x) for x in r["d"].split("-"))
            delta = (dt.date(2000, m, dd) - today).days
        except ValueError:
            continue
        if delta > 182:
            delta -= 366
        elif delta < -182:
            delta += 366
        if abs(delta) > window_days:
            continue
        age = time.localtime().tm_year - y
        when = "today" if delta == 0 else ("tomorrow" if delta == 1 else "yesterday" if delta == -1
                                          else f"{abs(delta)} days {'ago' if delta < 0 else 'from now'}")
        src = "RELEASED TODAY · FROM YOUR SHELF" if delta == 0 else "RELEASED THIS WEEK · FROM YOUR SHELF"
        out.append(Item(src, f"{r['artist']} — {r['title']}",
                        f"{y} · {r['label'] or ''}".rstrip(" ·") + f" · {age} years ago {when}", float(y) - abs(delta) / 10))
    out.sort(key=lambda i: (0 if "TODAY" in i.source else 1, i.when))
    return out


def fetch_this_day(month: int, day: int, max_items: int = 22, people: bool = False) -> list[Item]:
    """Music-flavoured events for today from Wikipedia (+ births/deaths if people=True),
    plus releases from the local collection with this date."""
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Api-User-Agent": UA})
    out: list[Item] = []
    kinds = [("events", "ON THIS DAY")] + ([("births", "BORN TODAY"), ("deaths", "DIED TODAY")] if people else [])
    for kind, label in kinds:
        try:
            r = s.get(f"https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/{kind}/{month:02d}/{day:02d}", timeout=20)
            r.raise_for_status()
            for e in r.json().get(kind, []):
                text = _clean(e.get("text", ""))
                pages = e.get("pages", [])
                desc = " ".join(_clean(p.get("description", "")) for p in pages[:2])
                if kind == "events":
                    if not MUSIC_WORDS.search(text + " " + desc):
                        continue
                elif not (pages and PERSON_WORDS.search(_clean(pages[0].get("description", "")))):
                    continue
                year = e.get("year")
                if kind == "events":
                    out.append(Item(label, f"{year}: {text}", "", float(year or 0)))
                else:
                    who = re.sub(r"\s*\([^)]*\)$", "", pages[0].get("normalizedtitle", "")) if pages else text
                    d = _clean(pages[0].get("description", "")) if pages else ""
                    out.append(Item(label, f"{who} ({year})", d or text, float(year or 0)))
        except Exception as e:  # noqa: BLE001
            log.warning("onthisday %s: %s", kind, e)
    # births from 1900 on in year order (the vinyl-era names land early), then deaths
    # newest-first, then events; capped so a full cycle takes ~5 minutes
    # rock/vinyl-era births first, then the rest newest-first
    b = [i for i in out if i.source == "BORN TODAY" and i.when >= 1900]
    births = sorted((i for i in b if 1935 <= i.when <= 1975), key=lambda i: i.when) + \
             sorted((i for i in b if not 1935 <= i.when <= 1975), key=lambda i: -i.when)
    deaths = sorted((i for i in out if i.source == "DIED TODAY"), key=lambda i: -i.when)
    events = sorted((i for i in out if i.source == "ON THIS DAY"), key=lambda i: i.when)
    anniv = collection_anniversaries(month, day)
    if len(anniv) + len(events) < 3:
        anniv = collection_anniversaries(month, day, window_days=3)
    return (anniv[:12] + events[:10] + births[:8] + deaths[:4])[:max_items]


class Cache:
    def __init__(self, fetch_fn, refresh_s: float):
        self.fetch_fn = fetch_fn
        self.refresh_s = refresh_s
        self.items: list[Item] = []
        self.fetched_at = 0.0
        self.error = ""
        self.version = 0
        self._busy = False

    def get(self) -> list[Item]:
        if not self._busy and time.time() - self.fetched_at > self.refresh_s:
            self._busy = True
            threading.Thread(target=self._run, daemon=True).start()
        return self.items

    def _run(self) -> None:
        try:
            items = self.fetch_fn()
            if items or not self.items:
                self.items = items
                self.version += 1
            self.fetched_at = time.time()
            self.error = "" if items else "no items"
        except Exception as e:  # noqa: BLE001
            self.error = str(e)[:80]
        finally:
            self._busy = False
