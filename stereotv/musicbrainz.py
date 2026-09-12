"""Original release dates from MusicBrainz release-groups (the album's first release,
not this pressing). Free, no key, 1 request/s with a real User-Agent."""
from __future__ import annotations

import logging
import re
import sqlite3
import time

import requests

log = logging.getLogger("stereotv.musicbrainz")

API = "https://musicbrainz.org/ws/2/release-group/"
from stereotv import config as _cfg
UA = _cfg.user_agent()
_last = 0.0


def _q(s: str) -> str:
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", s)


def lookup(session: requests.Session, artist: str, title: str) -> tuple[str, int] | None:
    """Return (first-release-date, score) or None."""
    global _last
    wait = _last + 1.05 - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    a = re.sub(r"\s+\(\d+\)$", "", artist)
    q = f'release:"{_q(title)}" AND artist:"{_q(a)}"'
    r = session.get(API, params={"query": q, "fmt": "json", "limit": 5}, timeout=45)
    if r.status_code == 503:
        time.sleep(5)
        return None
    r.raise_for_status()
    best = None
    for rg in r.json().get("release-groups", []):
        score, date = int(rg.get("score", 0)), rg.get("first-release-date") or ""
        if score < 85 or not date:
            continue
        # prefer albums over singles/others, then the most complete date, then score
        rank = (rg.get("primary-type") == "Album", len(date), score)
        if best is None or rank > best[0]:
            best = (rank, date, score)
    return (best[1], best[2]) if best else None


def enrich(con: sqlite3.Connection, only_missing: bool = True) -> int:
    cols = {r[1] for r in con.execute("PRAGMA table_info(releases)")}
    if "first_release" not in cols:
        con.execute("ALTER TABLE releases ADD COLUMN first_release TEXT")
        con.commit()
    where = "WHERE first_release IS NULL" if only_missing else ""
    rows = con.execute(f"SELECT release_id, artist, title FROM releases {where}").fetchall()
    s = requests.Session()
    s.headers["User-Agent"] = UA
    n = 0
    for i, r in enumerate(rows):
        if r["artist"].lower() in ("various", "various artists"):
            con.execute("UPDATE releases SET first_release='' WHERE release_id=?", (r["release_id"],))
            continue
        try:
            res = lookup(s, r["artist"], r["title"])
        except requests.RequestException as e:
            log.warning("mb %s: %s", r["release_id"], e)
            continue
        con.execute("UPDATE releases SET first_release=? WHERE release_id=?", (res[0] if res else "", r["release_id"]))
        con.commit()                      # short transactions: don't starve the app / other jobs
        if res:
            n += 1
        if i % 25 == 0:
            log.info("musicbrainz: %d/%d (%d dated)", i, len(rows), n)
    con.commit()
    return n
