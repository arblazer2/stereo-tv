"""Shared NowPlaying state that every channel reads."""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("stereotv.state")


@dataclass
class Release:
    release_id: int
    artist: str
    title: str
    year: int | None = None
    label: str = ""
    catno: str = ""
    formats: str = ""
    genres: str = ""
    styles: str = ""
    cover_path: str | None = None

    @classmethod
    def from_row(cls, row) -> "Release":
        return cls(
            release_id=row["release_id"],
            artist=row["artist"],
            title=row["title"],
            year=row["year"] or None,
            label=row["label"] or "",
            catno=row["catno"] or "",
            formats=row["formats"] or "",
            genres=row["genres"] or "",
            styles=row["styles"] or "",
            cover_path=row["cover_path"],
        )

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class NowPlaying:
    release: Release | None = None
    side: str | None = None
    track: str | None = None
    track_title: str | None = None
    source: str = "none"          # none | manual | auto | hardcoded
    confidence: float = 0.0
    status: str = ""              # short human status from the identifier ("LISTENING", ...)
    updated_at: float = field(default_factory=time.monotonic)
    override_until: float = 0.0   # monotonic; auto-ID paused until then
    last_played: Release | None = None   # last *real* play (auto/manual/shazam), survives clear()
    last_played_at: float = 0.0          # wall clock
    version: int = 0              # bump so channels can detect changes cheaply
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, release: Release | None, source: str, confidence: float = 1.0,
            side: str | None = None, track: str | None = None,
            track_title: str | None = None, override_minutes: float = 0.0) -> None:
        with self._lock:
            self.release = release
            self.source = source
            self.confidence = confidence
            self.side = side
            self.track = track
            self.track_title = track_title
            self.updated_at = time.monotonic()
            if release is not None and source in ("auto", "manual", "shazam", "acoustid"):
                self.last_played = release
                self.last_played_at = time.time()
                self._persist()
            if override_minutes:
                self.override_until = time.monotonic() + override_minutes * 60
            self.version += 1

    def clear(self) -> None:
        """Nothing playing (long silence). Keeps last_played and any manual override timer."""
        with self._lock:
            if self.release is None:
                return
            self.release = None
            self.source = "none"
            self.confidence = 0.0
            self.side = self.track = self.track_title = None
            self.updated_at = time.monotonic()
            self.version += 1

    @property
    def playing(self) -> bool:
        return self.release is not None

    # ------------------------------------------------------------ last-played persistence
    persist_path: Path | None = None

    def _persist(self) -> None:
        if not self.persist_path or not self.last_played:
            return
        try:
            self.persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"release": self.last_played.as_dict(), "at": self.last_played_at}))
            tmp.replace(self.persist_path)
        except OSError as e:
            log.debug("persist: %s", e)

    def load_last_played(self, path: Path) -> None:
        self.persist_path = path
        try:
            j = json.loads(path.read_text())
            self.last_played = Release(**j["release"])
            self.last_played_at = float(j.get("at", 0))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def set_track(self, side: str | None, track: str | None, track_title: str | None) -> None:
        with self._lock:
            if (side, track, track_title) != (self.side, self.track, self.track_title):
                self.side, self.track, self.track_title = side, track, track_title
                self.version += 1

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status

    def clear_override(self) -> None:
        with self._lock:
            self.override_until = 0.0

    @property
    def overridden(self) -> bool:
        return time.monotonic() < self.override_until

    def as_dict(self) -> dict:
        return {
            "release": self.release.as_dict() if self.release else None,
            "side": self.side, "track": self.track, "track_title": self.track_title,
            "source": self.source, "confidence": self.confidence, "status": self.status,
            "override_remaining": max(0.0, self.override_until - time.monotonic()),
            "last_played": self.last_played.as_dict() if self.last_played else None,
            "last_played_at": self.last_played_at,
            "version": self.version,
        }
