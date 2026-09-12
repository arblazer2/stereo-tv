"""Wikipedia summaries for artists and albums, cached in the `wiki` table.

Uses the MediaWiki search API to resolve a page title, then the REST summary
endpoint for the extract. Polite: one request every ~250 ms, real User-Agent.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time

import requests

log = logging.getLogger("stereotv.wiki")

SEARCH = "https://en.wikipedia.org/w/api.php"
SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
MIN_INTERVAL = 0.25
_last = 0.0


def _get(s: requests.Session, url: str, **params):
    global _last
    wait = _last + MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    r = s.get(url, params=params or None, timeout=15)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _clean_artist(name: str) -> str:
    return re.sub(r"\s+\(\d+\)$", "", name).strip()


_MUSIC = re.compile(r"\b(band|singer|musician|songwriter|composer|guitarist|rapper|drummer|pianist|vocalist|"
                    r"group|duo|trio|quartet|orchestra|album|record|music)\b", re.I)


def _norm(s: str) -> str:
    s = re.sub(r"\s*\(.*?\)\s*$", "", s or "").lower()      # drop "(musician)" / "(band)"
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    return " ".join(s.split())


_ARTIST_PAREN = re.compile(r"\((band|musician|singer|group|rapper|duo|trio|composer|songwriter|guitarist|drummer|"
                           r"american band|british band|english band|canadian band|rock band|country singer|[a-z ]*band|[a-z ]*singer|[a-z ]*musician)\)$", re.I)
_ALBUM_PAREN = re.compile(r"\(.*(album|ep|soundtrack|record|compilation|live).*\)$", re.I)
_NOT_ARTIST = re.compile(r"\b(album|song|single|ep|film|movie|university|college|city|town|state|county|river|"
                         r"novel|book|tv series|television)\b", re.I)
_IS_ARTIST = re.compile(r"\b(band|singer|musician|songwriter|composer|guitarist|rapper|drummer|pianist|vocalist|"
                        r"group|duo|trio|quartet|orchestra|dj|artist|conductor)\b", re.I)


def title_matches(title: str, want: str, kind: str = "artist") -> bool:
    """The page title must BE the thing we want, not merely mention it:
    'George Harrison' ok, 'Dhani Harrison' not; 'Aja (album)' ok for album 'Aja';
    'Yes (Yes album)' NOT ok for artist 'Yes'."""
    t, w = _norm(title), _norm(want)
    if not t or not w:
        return False
    if not (t == w or t.startswith(w + " ") or t.endswith(" " + w) and len(w) >= 8):
        return False
    paren = re.search(r"\(([^)]*)\)\s*$", title)
    if paren:
        p = "(" + paren.group(1) + ")"
        if kind == "artist" and not _ARTIST_PAREN.search(p):
            return False
        if kind == "album" and not _ALBUM_PAREN.search(p):
            return False
    return True


def desc_ok(desc: str, extract: str, kind: str) -> bool:
    """Wikipedia short description must agree with what we asked for."""
    head = (desc or extract[:120] or "")
    if kind == "artist":
        return bool(_IS_ARTIST.search(head)) and not _NOT_ARTIST.search(head)
    return bool(re.search(r"\b(album|ep|soundtrack|compilation|record)\b", head, re.I))


def _search(s: requests.Session, query: str, want: str, kind: str) -> list[str]:
    """Candidate page titles whose title matches `want`, best first."""
    data = _get(s, SEARCH, action="query", list="search", srsearch=query, srlimit=8, format="json")
    return [hit["title"] for hit in (data or {}).get("query", {}).get("search", []) if title_matches(hit["title"], want, kind)]


def _summary(s: requests.Session, title: str) -> tuple[str, str, str] | None:
    data = _get(s, SUMMARY + requests.utils.quote(title.replace(" ", "_"), safe=""))
    if not data or data.get("type") == "disambiguation":
        return None
    return (data.get("extract", ""), data.get("content_urls", {}).get("desktop", {}).get("page", ""),
            data.get("description", ""))


def lookup(con: sqlite3.Connection, s: requests.Session, key: str, queries: list[tuple[str, list[str]]]) -> dict | None:
    """Return cached row for key, fetching (and caching, incl. misses) if needed."""
    row = con.execute("SELECT * FROM wiki WHERE key=?", (key,)).fetchone()
    if row is not None:
        return dict(row) if row["title"] else None
    title, extract, url = "", "", ""
    try:
        seen: set[str] = set()
        kind = "album" if key.startswith("album:") else "artist"
        for q, want in queries:
            for t in _search(s, q, want, kind):
                if t in seen:
                    continue
                seen.add(t)
                res = _summary(s, t)
                if not res or not res[0]:
                    continue
                ext, u, desc = res
                # the description has to agree: a band for an artist, an album for an album
                if not desc_ok(desc, ext, kind):
                    continue
                title, extract, url = t, ext, u
                break
            if title:
                break
    except requests.RequestException as e:
        log.warning("wiki %s: %s", key, e)
        return None                      # transient: don't cache the miss
    con.execute("INSERT OR REPLACE INTO wiki VALUES (?,?,?,?,?)", (key, title, extract, url, time.time()))
    con.commit()
    return {"key": key, "title": title, "extract": extract, "url": url} if title else None


def artist_queries(artist: str) -> list[tuple[str, str]]:
    """(search query, required title) pairs."""
    a = _clean_artist(artist)
    return [(f"{a} band", a), (f"{a} musician", a), (a, a)]


def album_queries(artist: str, title: str) -> list[tuple[str, str]]:
    a = _clean_artist(artist)
    return [(f"{title} ({a} album)", title), (f"{title} {a} album", title), (f"{title} album", title)]


def session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = user_agent
    return s


def for_release(con: sqlite3.Connection, s: requests.Session, rel) -> tuple[dict | None, dict | None]:
    """(artist_summary, album_summary) for a Release, cached."""
    a = lookup(con, s, f"artist:{rel.artist}", artist_queries(rel.artist))
    akey = f"album:{rel.release_id}" if rel.release_id > 0 else f"album:{rel.artist}|{rel.title}"
    b = lookup(con, s, akey, album_queries(rel.artist, rel.title))
    return a, b


def prefetch(con: sqlite3.Connection, user_agent: str) -> int:
    s = session(user_agent)
    n = 0
    rows = con.execute("SELECT release_id, artist, title FROM releases ORDER BY artist").fetchall()
    for r in rows:
        if r["artist"].lower() not in ("various", "various artists"):
            if lookup(con, s, f"artist:{r['artist']}", artist_queries(r["artist"])):
                n += 1
        if lookup(con, s, f"album:{r['release_id']}", album_queries(r["artist"], r["title"])):
            n += 1
        if n and n % 50 == 0:
            log.info("wiki: %d summaries", n)
    return n
