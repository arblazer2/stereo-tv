"""Discogs collection sync -> SQLite + local cover-art cache.

Usage:
    python -m stereotv.discogs sync            # full sync (releases + missing covers)
    python -m stereotv.discogs sync --no-covers
    python -m stereotv.discogs covers          # only fetch missing covers
    python -m stereotv.discogs tracks          # tracklists + release notes (1 API call per release)
    python -m stereotv.discogs wiki            # prefetch Wikipedia summaries for artists + albums
    python -m stereotv.discogs musicbrainz     # original release dates (for "released today" anniversaries)
    python -m stereotv.discogs market          # collection value + per-release lowest price (weekly)
    python -m stereotv.discogs stats
    python -m stereotv.discogs show <release_id>
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import requests

from stereotv import config
from stereotv.state import Release

log = logging.getLogger("stereotv.discogs")

API = "https://api.discogs.com"
PER_PAGE = 100
MIN_INTERVAL = 1.1  # s between API calls -> ~55/min, under the 60/min cap

SCHEMA = """
CREATE TABLE IF NOT EXISTS releases (
    release_id   INTEGER PRIMARY KEY,
    instance_id  INTEGER,
    artist       TEXT NOT NULL,
    title        TEXT NOT NULL,
    year         INTEGER,
    label        TEXT,
    catno        TEXT,
    formats      TEXT,
    genres       TEXT,
    styles       TEXT,
    cover_url    TEXT,
    cover_path   TEXT,
    date_added   TEXT,
    folder_id    INTEGER,
    raw          TEXT,
    synced_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_releases_artist ON releases(artist);
CREATE INDEX IF NOT EXISTS idx_releases_label ON releases(label);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS wiki (
    key          TEXT PRIMARY KEY,      -- "artist:<name>" or "album:<release_id>"
    title        TEXT,                  -- resolved Wikipedia page title ('' = not found)
    extract      TEXT,
    url          TEXT,
    fetched_at   REAL
);
CREATE TABLE IF NOT EXISTS market (
    release_id   INTEGER PRIMARY KEY REFERENCES releases(release_id) ON DELETE CASCADE,
    lowest       REAL,
    prev_lowest  REAL,
    currency     TEXT,
    for_sale     INTEGER,
    fetched_at   REAL
);
CREATE TABLE IF NOT EXISTS value_history (
    day          TEXT PRIMARY KEY,      -- YYYY-MM-DD
    minimum      REAL, median REAL, maximum REAL, currency TEXT
);
CREATE TABLE IF NOT EXISTS tracks (
    release_id   INTEGER NOT NULL REFERENCES releases(release_id) ON DELETE CASCADE,
    position     TEXT,
    title        TEXT NOT NULL,
    duration     TEXT,
    seq          INTEGER,
    PRIMARY KEY (release_id, seq)
);
"""


def open_db(path: Path = config.DB_FILE) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")      # readers don't block writers: sync jobs + app coexist
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError:
        pass
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(releases)")}
    for col in ("notes", "enriched_at", "released", "credits"):
        if col not in cols:
            con.execute(f"ALTER TABLE releases ADD COLUMN {col} {'REAL' if col.endswith('_at') else 'TEXT'}")
            if col in ("released", "credits"):
                con.execute("UPDATE releases SET enriched_at=NULL")   # refetch to fill it
    con.commit()
    return con


# ---------------------------------------------------------------- API client

class Discogs:
    def __init__(self, token: str, user_agent: str):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Discogs token={token}",
            "User-Agent": user_agent,
            "Accept": "application/vnd.discogs.v2.plaintext+json",
        })
        self._last = 0.0

    def _throttle(self) -> None:
        wait = self._last + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str, **params: Any) -> dict:
        for attempt in range(5):
            self._throttle()
            r = self.s.get(url, params=params, timeout=30)
            if r.status_code == 429:
                delay = 5 * (attempt + 1)
                log.warning("rate limited; sleeping %ss", delay)
                time.sleep(delay)
                continue
            r.raise_for_status()
            remaining = r.headers.get("X-Discogs-Ratelimit-Remaining")
            if remaining is not None and int(remaining) < 5:
                log.info("ratelimit remaining=%s; pausing", remaining)
                time.sleep(20)
            return r.json()
        raise RuntimeError(f"gave up on {url}")

    def identity(self) -> dict:
        return self.get(f"{API}/oauth/identity")

    def collection(self, username: str, folder: int = 0) -> Iterator[dict]:
        page = 1
        while True:
            data = self.get(
                f"{API}/users/{username}/collection/folders/{folder}/releases",
                page=page, per_page=PER_PAGE, sort="added", sort_order="desc",
            )
            for item in data.get("releases", []):
                yield item
            pag = data.get("pagination", {})
            log.info("page %d/%d (%d items)", page, pag.get("pages", 1), pag.get("items", 0))
            if page >= pag.get("pages", 1):
                return
            page += 1

    def download(self, url: str, dest: Path) -> bool:
        self._throttle()
        r = self.s.get(url, timeout=60, stream=True)
        if r.status_code != 200:
            log.warning("cover %s -> HTTP %s", url, r.status_code)
            return False
        ctype = r.headers.get("Content-Type", "")
        if not ctype.startswith("image/"):
            log.warning("cover %s -> not an image (%s)", url, ctype)
            return False
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(65536):
                f.write(chunk)
        if tmp.stat().st_size < 100:
            log.warning("cover %s -> empty body", url)
            tmp.unlink()
            return False
        tmp.replace(dest)
        return True


# ---------------------------------------------------------------- mapping

_DISAMBIG = re.compile(r"\s+\(\d+\)$")


def _join(items: list, key: str | None = None, sep: str = ", ") -> str:
    vals = []
    for it in items or []:
        v = it.get(key) if key else it
        if v:
            v = _DISAMBIG.sub("", str(v))    # "MPL (2)" -> "MPL"
            if v not in vals:
                vals.append(v)
    return sep.join(vals)


def _artist_name(artists: list[dict]) -> str:
    """Join artists honoring Discogs 'join' strings, strip the (2) disambiguators."""
    out = ""
    for a in artists or []:
        name = a.get("name", "")
        # "Cars, The (2)" -> "Cars, The"; move trailing ", The" to the front
        if name.endswith(")") and "(" in name:
            head, _, tail = name.rpartition(" (")
            if tail[:-1].isdigit():
                name = head
        if name.endswith(", The"):
            name = "The " + name[:-5]
        out += name
        join = (a.get("join") or "").strip()
        if join:
            out += " " + join + " "
    return out.strip()


def _format_str(formats: list[dict]) -> str:
    parts = []
    for f in formats or []:
        bits = [f.get("name", "")]
        bits += [d for d in (f.get("descriptions") or []) if d]
        if f.get("text"):
            bits.append(f["text"])
        parts.append(" ".join(b for b in bits if b))
    return "; ".join(parts)


def map_item(item: dict) -> dict:
    bi = item["basic_information"]
    labels = bi.get("labels") or []
    return {
        "release_id": bi["id"],
        "instance_id": item.get("instance_id"),
        "artist": _artist_name(bi.get("artists", [])),
        "title": bi.get("title", ""),
        "year": bi.get("year") or None,
        "label": _join(labels, "name"),
        "catno": _join(labels, "catno"),
        "formats": _format_str(bi.get("formats", [])),
        "genres": _join(bi.get("genres", [])),
        "styles": _join(bi.get("styles", [])),
        "cover_url": bi.get("cover_image") or "",
        "date_added": item.get("date_added"),
        "folder_id": item.get("folder_id"),
        "raw": json.dumps(bi, separators=(",", ":")),
    }


# ---------------------------------------------------------------- sync

def sync_releases(api: Discogs, con: sqlite3.Connection, username: str) -> int:
    now = time.time()
    seen: set[int] = set()
    n = 0
    for item in api.collection(username):
        row = map_item(item)
        seen.add(row["release_id"])
        con.execute(
            """INSERT INTO releases (release_id, instance_id, artist, title, year, label,
                 catno, formats, genres, styles, cover_url, date_added, folder_id, raw, synced_at)
               VALUES (:release_id, :instance_id, :artist, :title, :year, :label, :catno,
                 :formats, :genres, :styles, :cover_url, :date_added, :folder_id, :raw, :synced_at)
               ON CONFLICT(release_id) DO UPDATE SET
                 instance_id=excluded.instance_id, artist=excluded.artist, title=excluded.title,
                 year=excluded.year, label=excluded.label, catno=excluded.catno,
                 formats=excluded.formats, genres=excluded.genres, styles=excluded.styles,
                 cover_url=excluded.cover_url, date_added=excluded.date_added,
                 folder_id=excluded.folder_id, raw=excluded.raw, synced_at=excluded.synced_at""",
            {**row, "synced_at": now},
        )
        n += 1
        if n % 100 == 0:
            con.commit()
    # remove releases no longer in the collection
    if seen:
        gone = [r["release_id"] for r in con.execute("SELECT release_id FROM releases WHERE synced_at < ?", (now,))]
        for rid in gone:
            log.info("removed from collection: %s", rid)
            con.execute("DELETE FROM releases WHERE release_id=?", (rid,))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('last_sync', ?)", (str(now),))
    con.commit()
    return n


def sync_covers(api: Discogs, con: sqlite3.Connection) -> int:
    config.COVERS_DIR.mkdir(parents=True, exist_ok=True)
    rows = con.execute(
        "SELECT release_id, cover_url, cover_path FROM releases WHERE cover_url != ''"
    ).fetchall()
    n = 0
    for r in rows:
        if r["cover_path"]:
            cp = Path(r["cover_path"])
            if cp.exists() and cp.stat().st_size > 0:
                continue
            if cp.exists():
                cp.unlink()  # empty/corrupt from an interrupted run
        url = r["cover_url"]
        if "spacer.gif" in url:
            continue
        ext = ".png" if url.lower().endswith(".png") else ".jpg"
        dest = config.COVERS_DIR / f"{r['release_id']}{ext}"
        if dest.exists() and dest.stat().st_size == 0:
            dest.unlink()
        if not dest.exists():
            log.info("cover %s", r["release_id"])
            try:
                if not api.download(url, dest):
                    continue
            except requests.RequestException as e:
                log.warning("cover %s failed: %s", r["release_id"], e)
                continue
        con.execute("UPDATE releases SET cover_path=? WHERE release_id=?", (str(dest), r["release_id"]))
        con.commit()
        n += 1
    return n


def sync_tracks(api: Discogs, con: sqlite3.Connection) -> int:
    """Fetch full release data (tracklist + notes) for releases not yet enriched. ~1 call/s."""
    rows = con.execute("SELECT release_id FROM releases WHERE enriched_at IS NULL").fetchall()
    n = 0
    for r in rows:
        rid = r["release_id"]
        try:
            data = api.get(f"{API}/releases/{rid}")
        except requests.RequestException as e:
            log.warning("release %s failed: %s", rid, e)
            continue
        tl = [t for t in data.get("tracklist", []) if t.get("type_", "track") == "track"]
        con.execute("DELETE FROM tracks WHERE release_id=?", (rid,))
        for i, t in enumerate(tl):
            con.execute("INSERT OR REPLACE INTO tracks VALUES (?,?,?,?,?)",
                        (rid, t.get("position", ""), t.get("title", ""), t.get("duration", ""), i))
        credits = [{"name": _DISAMBIG.sub("", a.get("name", "")), "role": a.get("role", ""), "tracks": a.get("tracks", "")}
                   for a in data.get("extraartists", []) if a.get("name")]
        con.execute("UPDATE releases SET notes=?, released=?, credits=?, enriched_at=? WHERE release_id=?",
                    ((data.get("notes") or "").strip(), (data.get("released") or "").strip(),
                     json.dumps(credits, separators=(",", ":")), time.time(), rid))
        con.commit()
        n += 1
        if n % 25 == 0:
            log.info("enriched: %d/%d", n, len(rows))
    return n


def _money(v) -> float | None:
    try:
        return float(v.get("value")) if isinstance(v, dict) else (float(v) if v is not None else None)
    except (TypeError, ValueError):
        return None


def sync_market(api: Discogs, con: sqlite3.Connection, username: str, max_age_days: float = 6.5) -> int:
    """Collection value (1 call) + per-release marketplace stats (1 call each, only if stale)."""
    try:
        v = api.get(f"{API}/users/{username}/collection/value")
        cur = "USD"
        vals = {}
        for k in ("minimum", "median", "maximum"):
            raw = str(v.get(k, "")).replace(",", "")
            m = re.search(r"([A-Z]{3}|[$€£])?\s*([\d.]+)", raw)
            if m:
                vals[k] = float(m.group(2))
                if m.group(1):
                    cur = {"$": "USD", "€": "EUR", "£": "GBP"}.get(m.group(1), m.group(1))
        if vals:
            con.execute("INSERT OR REPLACE INTO value_history VALUES (date('now','localtime'),?,?,?,?)",
                        (vals.get("minimum"), vals.get("median"), vals.get("maximum"), cur))
            con.commit()
            log.info("collection value: min %s median %s max %s %s", vals.get("minimum"), vals.get("median"), vals.get("maximum"), cur)
    except requests.RequestException as e:
        log.warning("collection value: %s", e)
    cutoff = time.time() - max_age_days * 86400
    rows = con.execute("SELECT r.release_id, m.lowest, m.fetched_at FROM releases r LEFT JOIN market m USING(release_id) "
                       "WHERE m.fetched_at IS NULL OR m.fetched_at < ?", (cutoff,)).fetchall()
    n = 0
    for r in rows:
        try:
            st = api.get(f"{API}/marketplace/stats/{r['release_id']}", curr_abbr="USD")
        except requests.RequestException as e:
            log.warning("market %s: %s", r["release_id"], e)
            continue
        low = _money(st.get("lowest_price"))
        con.execute("INSERT OR REPLACE INTO market VALUES (?,?,?,?,?,?)",
                    (r["release_id"], low, r["lowest"], (st.get("lowest_price") or {}).get("currency", "USD") if isinstance(st.get("lowest_price"), dict) else "USD",
                     int(st.get("num_for_sale") or 0), time.time()))
        con.commit()
        n += 1
        if n % 25 == 0:
            log.info("market: %d/%d", n, len(rows))
    return n


def search(con: sqlite3.Connection, q: str, limit: int = 40) -> list[dict]:
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        rows = con.execute(
            "SELECT release_id, artist, title, year, label, cover_path FROM releases"
            " WHERE artist LIKE ? OR title LIKE ? OR label LIKE ? OR catno LIKE ?"
            " ORDER BY artist, year LIMIT ?", (like, like, like, like, limit)).fetchall()
    else:
        rows = con.execute(
            "SELECT release_id, artist, title, year, label, cover_path FROM releases"
            " ORDER BY date_added DESC LIMIT ?", (limit,)).fetchall()
    return [{"release_id": r["release_id"], "artist": r["artist"], "title": r["title"],
             "year": r["year"], "label": r["label"], "cover": bool(r["cover_path"])} for r in rows]


# ---------------------------------------------------------------- queries used by channels

def get_release(con: sqlite3.Connection, release_id: int) -> Release | None:
    row = con.execute("SELECT * FROM releases WHERE release_id=?", (release_id,)).fetchone()
    return Release.from_row(row) if row else None


def random_release(con: sqlite3.Connection, with_cover: bool = True) -> Release | None:
    q = "SELECT * FROM releases"
    if with_cover:
        q += " WHERE cover_path IS NOT NULL"
    row = con.execute(q + " ORDER BY RANDOM() LIMIT 1").fetchone()
    return Release.from_row(row) if row else None


def count(con: sqlite3.Connection) -> tuple[int, int]:
    total = con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    covers = con.execute("SELECT COUNT(*) FROM releases WHERE cover_path IS NOT NULL").fetchone()[0]
    return total, covers


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stereotv.discogs")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sync"); p.add_argument("--no-covers", action="store_true")
    sub.add_parser("covers")
    sub.add_parser("tracks")
    sub.add_parser("wiki")
    sub.add_parser("musicbrainz")
    sub.add_parser("market")
    sub.add_parser("stats")
    sub.add_parser("whoami")
    p = sub.add_parser("show"); p.add_argument("release_id", type=int)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    cfg = config.load()
    config.ensure_dirs()
    con = open_db()

    if args.cmd == "stats":
        total, covers = count(con)
        tracked = con.execute("SELECT COUNT(DISTINCT release_id) FROM tracks").fetchone()[0]
        noted = con.execute("SELECT COUNT(*) FROM releases WHERE notes != ''").fetchone()[0]
        wikis = con.execute("SELECT COUNT(*) FROM wiki WHERE title != ''").fetchone()[0]
        last = con.execute("SELECT value FROM meta WHERE key='last_sync'").fetchone()
        print(f"releases: {total}  covers: {covers}  tracklists: {tracked}  notes: {noted}  wiki: {wikis}  db: {config.DB_FILE}")
        if last:
            print("last sync:", time.strftime("%Y-%m-%d %H:%M", time.localtime(float(last[0]))))
        return 0
    if args.cmd == "show":
        rel = get_release(con, args.release_id)
        print(rel if rel else "not found")
        return 0
    if args.cmd == "wiki":
        from stereotv import wiki
        log.info("fetched %d wikipedia summaries", wiki.prefetch(con, cfg["discogs"]["user_agent"]))
        return 0
    if args.cmd == "musicbrainz":
        from stereotv import musicbrainz
        log.info("musicbrainz: %d original release dates", musicbrainz.enrich(con))
        return 0

    api = Discogs(config.read_token(cfg), cfg["discogs"]["user_agent"])
    if args.cmd == "whoami":
        me = api.identity()
        print(f"authenticated as {me.get('username')} (id {me.get('id')})")
        return 0
    username = cfg["discogs"]["username"]
    if args.cmd == "sync":
        n = sync_releases(api, con, username)
        log.info("synced %d releases", n)
        if not args.no_covers:
            log.info("fetched %d covers", sync_covers(api, con))
    elif args.cmd == "covers":
        log.info("fetched %d covers", sync_covers(api, con))
    elif args.cmd == "tracks":
        log.info("fetched %d tracklists", sync_tracks(api, con))
    elif args.cmd == "market":
        log.info("fetched %d marketplace stats", sync_market(api, con, username))
    total, covers = count(con)
    log.info("db now has %d releases, %d with covers", total, covers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
