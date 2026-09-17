"""Line-in fingerprinting -> NowPlaying.

Loop (background thread):
  * watch the audio level; classify SILENT / PLAYING
  * on silence->audio transition wait `settle` seconds, then fingerprint a clip
  * while unmatched, retry every `interval` seconds
  * once matched, don't re-fingerprint until a silence gap (side flip / new record)
  * manual override (web page) pauses auto-ID until override_until

Recognizer: shazamio (first), AcoustID via pyacoustid+fpcalc (optional fallback).
Both results resolve to *Justin's* pressing by fuzzy-matching artist/album/track
against the local Discogs DB.
"""
from __future__ import annotations

import asyncio
import difflib
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from stereotv import config, discogs
from stereotv.audio import AudioStream
from stereotv.state import NowPlaying, Release

log = logging.getLogger("stereotv.identify")


@dataclass
class Recognized:
    artist: str
    track: str
    album: str = ""
    engine: str = ""
    offset: float | None = None      # seconds into the song at the START of the clip
    cover_url: str = ""
    year: int | None = None
    label: str = ""


def parse_duration(s: str | None) -> float | None:
    """'3:45' -> 225.0; '1:02:03' -> 3723.0; '' -> None."""
    if not s or ":" not in s:
        return None
    try:
        parts = [float(x) for x in s.strip().split(":")]
    except ValueError:
        return None
    secs = 0.0
    for x in parts:
        secs = secs * 60 + x
    return secs or None


# ---------------------------------------------------------------- normalization / matching

_PAREN = re.compile(r"\s*[\(\[].*?[\)\]]")
_NONWORD = re.compile(r"[^a-z0-9 ]+")


def norm(s: str) -> str:
    s = (s or "").lower()
    s = _PAREN.sub("", s)                      # drop "(Remastered 2009)", "[Live]"
    s = s.replace("&", " and ")
    s = _NONWORD.sub(" ", s)
    s = re.sub(r"\b(the|a|an)\b", " ", s)
    return " ".join(s.split())


def ratio(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_collection(con: sqlite3.Connection, rec: Recognized, current: Release | None = None,
                     stickiness: float = 0.05, prev_title: str | None = None,
                     ) -> tuple[Release | None, float, dict | None]:
    """Return (release, confidence, track_row) for the best local match.

    prev_title: the previously recognized track. A candidate pressing where that track
    sits immediately before this one gets a strong bonus — consecutive tracks pin down
    which of several pressings (studio album vs compilation) is actually spinning.
    """
    rows = con.execute("SELECT * FROM releases").fetchall()
    cands = []
    for r in rows:
        a = ratio(rec.artist, r["artist"])
        # "Various" compilations: artist field won't match; let a strong track hit carry it
        if a < 0.75 and norm(r["artist"]) not in ("various", "various artists"):
            continue
        cands.append((a, r))
    if not cands:
        return None, 0.0, None

    best, best_score, best_track = None, 0.0, None
    for a, r in cands:
        album = ratio(rec.album, r["title"]) if rec.album else 0.0
        track_row, t = None, 0.0
        tracks = con.execute("SELECT * FROM tracks WHERE release_id=? ORDER BY seq", (r["release_id"],)).fetchall()
        for tr in tracks:
            tt = ratio(rec.track, tr["title"])
            if tt > t:
                t, track_row = tt, tr
        # weights: artist is a gate, track proves the pressing, album helps
        score = 0.35 * a + 0.45 * t + 0.20 * album
        if current and r["release_id"] == current.release_id:
            score += stickiness              # hysteresis: stick with what we have
        if prev_title and track_row is not None and t >= 0.75:
            prev = next((x for x in tracks if x["seq"] == track_row["seq"] - 1), None)
            if prev is not None and ratio(prev_title, prev["title"]) >= 0.75:
                score += 0.5                 # consecutive tracks: this is the record (beats album+sticky)
        if score > best_score:
            best, best_score, best_track = r, score, track_row
    if best is None:
        return None, 0.0, None
    rel = Release.from_row(best)
    track = dict(best_track) if best_track and ratio(rec.track, best_track["title"]) >= 0.75 else None
    return rel, round(best_score, 2), track


# ---------------------------------------------------------------- recognizers

class ShazamRecognizer:
    name = "shazam"

    def __init__(self):
        from shazamio import Shazam  # noqa: WPS433
        self._shazam = Shazam()

    def recognize(self, wav: Path) -> Recognized | None:
        out = asyncio.run(self._shazam.recognize(str(wav)))
        track = out.get("track")
        if not track or not out.get("matches"):
            return None
        album, year, label = "", None, ""
        for sec in track.get("sections", []):
            for m in sec.get("metadata", []) or []:
                k, v = m.get("title", "").lower(), m.get("text", "")
                if k == "album":
                    album = v
                elif k == "released" and v[:4].isdigit():
                    year = int(v[:4])
                elif k == "label":
                    label = v
        cover = (track.get("images") or {}).get("coverarthq") or (track.get("images") or {}).get("coverart") or ""
        offset = None
        try:
            offset = float(out["matches"][0].get("offset"))
        except (KeyError, IndexError, TypeError, ValueError):
            pass
        return Recognized(artist=track.get("subtitle", ""), track=track.get("title", ""),
                          album=album, engine=self.name, offset=offset, cover_url=cover,
                          year=year, label=label)


class AcoustIDRecognizer:
    name = "acoustid"

    def __init__(self, api_key: str):
        import acoustid  # noqa: WPS433
        self._acoustid = acoustid
        self._key = api_key

    def recognize(self, wav: Path) -> Recognized | None:
        for score, rid, title, artist in self._acoustid.match(self._key, str(wav)):
            if score >= 0.5 and title:
                return Recognized(artist=artist or "", track=title, engine=self.name)
        return None


# ---------------------------------------------------------------- the loop

class Identifier(threading.Thread):
    def __init__(self, audio: AudioStream, now: NowPlaying, cfg: dict):
        super().__init__(name="identify", daemon=True)
        self.audio = audio
        self.now = now
        ic = cfg["identify"]
        self.clip = float(ic.get("clip_seconds", 10))
        self.interval = float(ic.get("interval", 60))
        self.settle = float(ic.get("settle_seconds", 4))
        self.silence_rms = float(ic.get("silence_rms", 0.004))
        self.silence_gap = float(ic.get("silence_gap_seconds", 2.5))
        self.min_conf = float(ic.get("min_confidence", 0.55))
        self.recheck = float(ic.get("recheck_seconds", 150))   # locked, no durations: re-ID this often
        self.clear_after = float(ic.get("clear_after_seconds", 300))  # silence this long -> nothing playing
        self.retry_interval = float(ic.get("retry_seconds", 20))      # quick retries right after audio starts...
        self.retry_fast = int(ic.get("retry_attempts", 4))             # ...this many times, then `interval`
        self.flip_window = float(ic.get("flip_window_seconds", 240))  # a gap shorter than this = side flip
        self.last_rel: Release | None = None                           # last release we positively matched
        self.last_side: str | None = None
        self.flip_candidate = False                                    # audio resumed within flip_window
        self.track_min = float(ic.get("acoustid_min_seconds", 60))    # shorter captures aren't worth a lookup
        self.advance_on_resume = False                                 # track ID'd at its end: step on when audio resumes
        self.verify_delay = float(ic.get("verify_seconds", 20))  # after a clocked advance, confirm
        self.track_end_at: float | None = None
        self.track_ref: tuple[int, int] | None = None            # (release_id, seq) the clock is on
        self.verify_at: float | None = None
        self.prev_rec: Recognized | None = None
        self.acoustid_key = ic.get("acoustid_api_key", "")
        self.recognizers = []
        self._stop = threading.Event()
        self.tmp = config.DATA_DIR / "clip.wav"

    def _init_recognizers(self) -> None:
        try:
            self.recognizers.append(ShazamRecognizer())
        except Exception as e:  # noqa: BLE001
            log.warning("shazamio unavailable: %s", e)
        self.acoustid = None
        if self.acoustid_key:
            try:
                # AcoustID only matches complete tracks, so it runs at track end on the whole-track capture
                self.acoustid = AcoustIDRecognizer(self.acoustid_key)
                log.info("acoustid enabled for end-of-track identification")
            except Exception as e:  # noqa: BLE001
                log.warning("pyacoustid unavailable: %s", e)
        if not self.recognizers:
            log.error("no recognizer available; auto-ID disabled")

    def stop(self) -> None:
        self._stop.set()

    # --------------------------------------------------------
    def run(self) -> None:
        self._init_recognizers()
        if not self.recognizers:
            self.now.set_status("NO RECOGNIZER")
            return
        con = discogs.open_db()
        playing = False
        silent_since = time.monotonic()
        audio_since = 0.0
        last_try = 0.0
        matched = False
        attempts = 0
        while not self._stop.is_set():
            time.sleep(0.5)
            if not self.audio.alive:
                self.now.set_status("NO LINE-IN")
                continue
            loud = self.audio.rms(0.5) > self.silence_rms
            t = time.monotonic()
            if loud:
                if not playing:
                    # silence -> audio: new side/record; forget the old match
                    if t - silent_since >= self.silence_gap:
                        matched = False
                        self.track_end_at = self.track_ref = self.verify_at = None
                        # a short gap after a known record is most likely its next side
                        self.flip_candidate = bool(self.last_rel) and (t - silent_since) <= self.flip_window
                        attempts = 0
                    playing, audio_since = True, t
                    self.audio.track_begin()
                    if self.advance_on_resume:
                        self.advance_on_resume = False
                        self._advance(con, t)
                        matched = True
                    log.info("audio started after %.1fs gap (flip candidate=%s)", t - silent_since, self.flip_candidate)
            else:
                if playing:
                    playing, silent_since = False, t
                    log.info("audio stopped after %.0fs (matched=%s conf=%.2f captured=%.0fs)", t - audio_since, matched,
                             self.now.confidence, self.audio.track_seconds())
                    if self.acoustid and (not matched or self.now.confidence < 0.5) and self.audio.track_seconds() >= self.track_min:
                        try:
                            if self._identify_track_end(con):
                                matched = True
                        except Exception as e:  # noqa: BLE001
                            log.warning("acoustid track-end: %s", e)
                    else:
                        self.audio.track_end()
                if self.now.playing and t - silent_since >= self.clear_after:
                    log.info("silent for %.0fs: clearing now playing", t - silent_since)
                    self.now.clear()
                    matched = False
                    self.track_end_at = self.track_ref = self.verify_at = None
                self.now.set_status("SILENCE")
                continue

            if self.now.overridden:
                self.now.set_status("MANUAL")
                continue
            if matched:
                if self.track_end_at is not None and t >= self.track_end_at:
                    self._advance(con, t)
                elif self.verify_at is not None and t >= self.verify_at:
                    self.verify_at = None
                    last_try = t
                    self.now.set_status("VERIFYING")
                    try:
                        matched = self._identify(con)
                    except Exception as e:  # noqa: BLE001
                        log.warning("verify failed: %s", e)
                elif self.track_end_at is None and t - last_try >= self.recheck:
                    last_try = t
                    self.now.set_status("RECHECK")
                    try:
                        matched = self._identify(con)
                    except Exception as e:  # noqa: BLE001
                        log.warning("recheck failed: %s", e)
                else:
                    self.now.set_status("LOCKED")
                continue
            gap = self.retry_interval if attempts < self.retry_fast else self.interval
            if t - audio_since < self.settle or t - last_try < gap:
                self.now.set_status("LISTENING")
                continue

            last_try = t
            attempts += 1
            self.now.set_status("IDENTIFYING")
            try:
                matched = self._identify(con)
            except Exception as e:  # noqa: BLE001
                log.warning("identify failed: %s", e)
                self.now.set_status("ID ERROR")
            if not matched and self.flip_candidate:
                # Shazam doesn't know this track; assume the next side of the record that just ended
                self.flip_candidate = False
                if self._presume_next_side(con, t):
                    matched = True

    def _identify(self, con: sqlite3.Connection) -> bool:
        wav = self.audio.write_wav(self.tmp, self.clip)
        rec = None
        for r in self.recognizers:
            try:
                rec = r.recognize(wav)
            except Exception as e:  # noqa: BLE001
                log.warning("%s error: %s", r.name, e)
                continue
            if rec:
                break
        if not rec:
            log.info("no match")
            self.now.set_status("NO MATCH")
            return False
        log.info("%s heard: %s - %s [%s]", rec.engine, rec.artist, rec.track, rec.album)
        # a record already identified this session is far more likely than a sibling pressing
        # (compilation vs studio album share tracks); a hardcoded/random pick carries no such weight
        sticky = 0.2 if self.now.source == "auto" else 0.0
        prev_title = self.prev_rec.track if self.prev_rec and self.prev_rec.track != rec.track else None
        rel, conf, track = match_collection(con, rec, self.now.release, stickiness=sticky, prev_title=prev_title)
        self.prev_rec = rec
        if rel is None or conf < self.min_conf:
            log.info("not in collection (best conf %.2f); showing %s result", conf, rec.engine)
            self._show_external(rec)
            self.now.set_status("NOT IN COLLECTION")
            return True                       # lock on it like any other match
        side, pos = _split_position(track["position"]) if track else (None, None)
        if self.now.release and self.now.release.release_id == rel.release_id and self.now.source == "auto":
            self.now.set_track(side, pos, track["title"] if track else rec.track)
            self.now.confidence = conf
        else:
            self.now.set(rel, source="auto", confidence=conf, side=side, track=pos,
                         track_title=track["title"] if track else rec.track)
        log.info("matched %s - %s (conf %.2f) %s", rel.artist, rel.title, conf,
                 f"side {side} trk {pos}" if side else "")
        self._start_clock(rel.release_id, track, rec.offset)
        self.last_rel, self.last_side = rel, side or self.last_side
        self.flip_candidate = False
        self.now.set_status("MATCHED")
        return True

    def _show_external(self, rec: Recognized) -> None:
        """CD / Zune / radio: not in the Discogs collection, show what the recognizer heard."""
        cover = None
        if rec.cover_url:
            cover = _cache_external_cover(rec.cover_url)
        import zlib
        rel = Release(release_id=-(zlib.crc32(f"{rec.artist}|{rec.album or rec.track}".encode()) % 10**9 + 1),
                      artist=rec.artist, title=rec.album or rec.track, year=rec.year,
                      label=rec.label, formats="not in collection", cover_path=cover)
        same = self.now.release and self.now.release.release_id == rel.release_id and self.now.source == rec.engine
        if same:
            self.now.set_track(None, None, rec.track)
        else:
            self.now.set(rel, source=rec.engine, confidence=0.0, track_title=rec.track)
        self.prev_rec = rec
        self.track_end_at = self.track_ref = self.verify_at = None

    # ------------------------------------------------------------ end-of-track AcoustID
    def _identify_track_end(self, con: sqlite3.Connection) -> bool:
        import wave
        samples = self.audio.track_end()
        secs = len(samples) / self.audio.track_rate
        path = config.DATA_DIR / "track.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(self.audio.track_rate)
            w.writeframes(samples.tobytes())
        self.now.set_status("ACOUSTID")
        rec = self.acoustid.recognize(path)
        if not rec:
            log.info("acoustid: no match for the %.0fs track", secs)
            return False
        log.info("acoustid heard: %s - %s (%.0fs track)", rec.artist, rec.track, secs)
        sticky = 0.2 if self.now.source == "auto" else 0.0
        prev_title = self.prev_rec.track if self.prev_rec and self.prev_rec.track != rec.track else None
        rel, conf, track = match_collection(con, rec, self.now.release, stickiness=sticky, prev_title=prev_title)
        self.prev_rec = rec
        if rel is None or conf < self.min_conf:
            if not self.now.playing:
                self._show_external(rec)
            log.info("acoustid: not in collection (best conf %.2f)", conf)
            return False
        side, pos = _split_position(track["position"]) if track else (None, None)
        self.now.set(rel, source="auto", confidence=conf, side=side, track=pos, track_title=track["title"] if track else rec.track)
        self.last_rel, self.last_side = rel, side or self.last_side
        self.track_end_at = self.verify_at = None
        self.track_ref = (rel.release_id, int(track["seq"])) if track else None
        self.advance_on_resume = self.track_ref is not None       # that track just finished: step to the next on resume
        log.info("acoustid matched %s - %s (conf %.2f) %s", rel.artist, rel.title, conf, f"side {side} trk {pos}" if side else "")
        self.now.set_status("MATCHED")
        return True

    # ------------------------------------------------------------ side-flip presumption
    def _presume_next_side(self, con: sqlite3.Connection, t: float) -> bool:
        """After a short silence following a known record, assume its next side, track 1.
        The track clock runs from there and every track boundary verifies with Shazam."""
        rel, side = self.last_rel, self.last_side
        if not rel or rel.release_id <= 0 or not side:
            return False
        tracks = con.execute("SELECT * FROM tracks WHERE release_id=? ORDER BY seq", (rel.release_id,)).fetchall()
        sides: list[str] = []
        for tr in tracks:
            sd = _split_position(tr["position"])[0]
            if sd and sd not in sides:
                sides.append(sd)
        if side.upper() not in sides or sides.index(side.upper()) + 1 >= len(sides):
            return False                                  # last side already: could be anything next
        nxt = sides[sides.index(side.upper()) + 1]
        first = next(tr for tr in tracks if _split_position(tr["position"])[0] == nxt)
        s2, pos = _split_position(first["position"])
        self.now.set(rel, source="auto", confidence=0.3, side=s2, track=pos, track_title=first["title"])
        self._start_clock(rel.release_id, dict(first), offset=-self.clip)   # clip just started: position 0
        self.verify_at = t + self.verify_delay
        self.last_side = s2
        log.info("presumed side %s of %s - %s (unrecognised first track)", s2, rel.artist, rel.title)
        self.now.set_status("PRESUMED")
        return True

    # ------------------------------------------------------------ track clock
    def _start_clock(self, release_id: int, track: dict | None, offset: float | None) -> None:
        """Arm the track-end timer from the matched track's duration and Shazam's offset."""
        self.track_end_at = self.track_ref = self.verify_at = None
        if not track:
            return
        dur = parse_duration(track.get("duration"))
        if dur is None:
            return
        # offset = song position at clip start; clip ends now
        pos = (offset or 0.0) + self.clip
        remaining = max(3.0, dur - pos)
        self.track_end_at = time.monotonic() + remaining
        self.track_ref = (release_id, int(track["seq"]))
        log.info("track clock: %s ends in %.0fs (dur %.0fs, at %.0fs)", track.get("position"), remaining, dur, pos)

    def _advance(self, con: sqlite3.Connection, t: float) -> None:
        """Track should have ended: step to the next track on the same side, then verify."""
        self.track_end_at = None
        if not self.track_ref:
            return
        rid, seq = self.track_ref
        cur = con.execute("SELECT * FROM tracks WHERE release_id=? AND seq=?", (rid, seq)).fetchone()
        nxt = con.execute("SELECT * FROM tracks WHERE release_id=? AND seq=?", (rid, seq + 1)).fetchone()
        cur_side = _split_position(cur["position"])[0] if cur else None
        nxt_side = _split_position(nxt["position"])[0] if nxt else None
        if nxt is None or nxt_side != cur_side:
            # end of side: wait for the flip (silence gap) but verify in case we missed it
            log.info("track clock: end of side %s", cur_side)
            self.track_ref = None
            self.verify_at = t + self.verify_delay
            return
        side, pos = _split_position(nxt["position"])
        self.last_side = side or self.last_side
        self.now.set_track(side, pos, nxt["title"])
        log.info("track clock: advanced to %s %s", nxt["position"], nxt["title"])
        dur = parse_duration(nxt["duration"])
        self.track_ref = (rid, seq + 1)
        self.track_end_at = (t + dur) if dur else None
        self.verify_at = t + self.verify_delay


def _cache_external_cover(url: str) -> str | None:
    import hashlib

    import requests
    d = config.COVERS_DIR / "external"
    d.mkdir(parents=True, exist_ok=True)
    p = d / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".jpg")
    if p.exists():
        return str(p)
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "stereo-tv/0.1"})
        if r.ok and r.headers.get("Content-Type", "").startswith("image/") and len(r.content) > 100:
            p.write_bytes(r.content)
            return str(p)
    except requests.RequestException as e:
        log.debug("external cover: %s", e)
    return None


def _split_position(pos: str) -> tuple[str | None, str | None]:
    """'A3' -> ('A', '3'); 'B' -> ('B', None); '7' -> (None, '7')."""
    pos = (pos or "").strip()
    m = re.match(r"^([A-Za-z]+)[-.]?(\d*)$", pos)
    if m:
        return m.group(1).upper(), (m.group(2) or None)
    return None, pos or None
