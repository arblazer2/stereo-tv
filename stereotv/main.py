"""stereo-tv main loop: channel registry, dial input, snow transitions."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

import pygame

from stereotv import config, dial, discogs
from stereotv import display as D
from stereotv.audio import AudioStream
from stereotv.screensaver import Screensaver
from stereotv.channels import (CollectionChannel, GuideChannel, LinerNotesChannel, MusicNewsChannel,
                               NowPlayingChannel, PersonnelChannel, PiStatusChannel, ThisDayChannel, ValueChannel,
                               RadarChannel, RandomSpinChannel, StaticChannel, StatsChannel, TestPatternChannel,
                               TracklistChannel, VisualizerChannel, WeatherChannel)
from stereotv.state import NowPlaying, Release

log = logging.getLogger("stereotv")

NUM_CHANNELS = 16
SNOW_TIME = 0.15        # s of static on channel change
OSD_TIME = 2.0          # s the channel number stays on screen

FALLBACK = Release(
    release_id=0,
    artist="Steely Dan",
    title="Aja",
    year=1977,
    label="ABC Records",
    catno="AB-1006",
    formats="Vinyl LP Album",
    genres="Jazz, Rock",
    styles="Jazz-Rock, Pop Rock",
)


class ChannelControl:
    """Thread-safe remote channel request (web API, later HA); consumed by the main loop."""

    def __init__(self, app: "App"):
        self.app = app
        self.pending: int | None = None

    def get(self) -> int:
        return self.app.cur

    def fps(self) -> float:
        return self.app.d.clock.get_fps()

    def theme(self, name: str | None = None) -> str:
        if name:
            self.app.theme_request = name
        return self.app.d.theme

    def request(self, n: int) -> None:
        self.pending = n

    def saver(self, mode: str | None) -> bool:
        """Force the screensaver on (optionally a specific mode) — testing / party trick."""
        app = self.app
        if not app.saver:
            return False
        if mode:
            for i, sv in enumerate(app.saver.savers):
                if sv.name == mode:
                    app.saver.i = i - 1
                    break
            else:
                return False
        app.saver_request = True
        app.manual_saver = True
        return True


class App:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        dc = cfg["display"]
        self.d = D.Display(dc["width"], dc["height"], dc["fps"], dc["fullscreen"], dc["scanlines"],
                           sdl_debug=bool(dc.get("sdl_debug", False)),
                           drift=bool(dc.get("drift", False)), drift_px=int(dc.get("drift_px", 2)),
                           drift_seconds=float(dc.get("drift_seconds", 180)), theme=dc.get("theme", "cable88"),
                           scaling=dc.get("scaling", "gpu"), font_scale=float(dc.get("font_scale", 1.0)))
        self.now = NowPlaying()
        self.now.load_last_played(config.DATA_DIR / "last_played.json")
        self.state_path = config.DATA_DIR / "state.json"
        self.state = self._load_state()          # {"channel": n, "theme": name} — survives restarts/reboots
        if self.state.get("theme") and self.state["theme"] != self.d.theme:
            self.d.apply_theme(self.state["theme"])      # display was built from config; saved theme wins
        if self.now.last_played:
            log.info("last played: %s - %s", self.now.last_played.artist, self.now.last_played.title)
        self.dial = dial.make(cfg["dial"]["mode"])
        self.channels: dict[int, object] = {n: StaticChannel(self.d, self.now, n) for n in range(1, NUM_CHANNELS + 1)}
        self.channels[1] = NowPlayingChannel(self.d, self.now)
        self.cur = int(self.state.get("channel") or 1)
        if not 1 <= self.cur <= NUM_CHANNELS:
            self.cur = 1
        self.snow_left = 0.0
        self.osd_left = OSD_TIME
        self.running = True
        self._load_hardcoded()
        self._start_services()
        if self.audio:
            self.channels[2] = VisualizerChannel(self.d, self.now, self.audio,
                                                 float(cfg.get("visualizer", {}).get("gain_db", 0.0)))
        self.channels[3] = LinerNotesChannel(self.d, self.now, cfg["discogs"]["user_agent"])
        self.channels[4] = CollectionChannel(self.d, self.now)
        self.channels[5] = GuideChannel(self.d, self.now)
        self.channels[6] = TracklistChannel(self.d, self.now)
        self.channels[7] = StatsChannel(self.d, self.now)
        self.channels[8] = RandomSpinChannel(self.d, self.now, float(cfg["identify"].get("override_minutes", 30)))
        self.channels[16] = TestPatternChannel(self.d, self.now)
        if cfg.get("radar", {}).get("enabled", True):
            self.channels[10] = RadarChannel(self.d, self.now, cfg)
        self.channels[11] = WeatherChannel(self.d, self.now, cfg, radar=self.channels.get(10))
        self.channels[12] = PiStatusChannel(self.d, self.now, self.audio, lambda: self.d.clock.get_fps())
        self.channels[13] = MusicNewsChannel(self.d, self.now, cfg.get("news", {}).get("feeds"))
        self.channels[14] = ThisDayChannel(self.d, self.now)
        self.channels[15] = ValueChannel(self.d, self.now)
        self.channels[9] = PersonnelChannel(self.d, self.now)
        self.channels[self.cur].enter()
        sc = cfg.get("screensaver", {})
        self.saver = Screensaver(self.d, self.now, cfg) if sc.get("enabled", True) else None
        self.idle_s = float(sc.get("idle_seconds", 90))
        self.silence_rms = float(cfg["identify"].get("silence_rms", 0.004))
        self.wake_rms = self.silence_rms * float(sc.get("wake_ratio", 1.5))   # hysteresis over the floor
        self.wake_hold = float(sc.get("wake_hold_seconds", 1.0))             # must stay loud this long
        self.silent_since = time.monotonic()
        self.loud_since: float | None = None
        self.saving = False
        self.saver_request = False
        self.theme_request: str | None = None
        self.theme_osd_left = 0.0
        self.key_wake_s = float(sc.get("key_wake_seconds", 300))   # a key press keeps it awake this long
        self.awake_until = 0.0
        self.manual_saver = False      # started by key/API: audio doesn't wake it, only a key does
        if getattr(self, "remote", None):
            self.remote.start()

    def _start_services(self) -> None:
        cfg = self.cfg
        self.audio = None
        self.identifier = None
        self.web = None
        self.channel_ctl = None
        if cfg["audio"].get("enabled", True):
            self.audio = AudioStream(cfg["audio"]["device"], int(cfg["audio"].get("rate", 44100)),
                                     int(cfg["audio"].get("channels", 2)),
                                     mixer_control=cfg["audio"].get("mixer_control", ""),
                                     mixer_gain=cfg["audio"].get("mixer_gain", ""),
                                     input_gain_db=float(cfg["audio"].get("input_gain_db", 0.0)))
            self.audio.start()
        if self.audio and cfg["identify"].get("enabled", True):
            from stereotv.identify import Identifier
            self.identifier = Identifier(self.audio, self.now, cfg)
            self.identifier.start()
        if cfg["web"].get("enabled", True):
            from stereotv.web import WebServer
            try:
                self.channel_ctl = ChannelControl(self)
                self.web = WebServer(self.now, cfg["web"].get("host", "0.0.0.0"),
                                     int(cfg["web"].get("port", 8080)),
                                     float(cfg["identify"].get("override_minutes", 30)),
                                     channel_ctl=self.channel_ctl, audio=self.audio)
                self.web.start()
            except OSError as e:
                log.error("web server failed: %s", e)
        if self.channel_ctl is None:
            self.channel_ctl = ChannelControl(self)
        self.remote = None
        rc = cfg.get("remote", {})
        if rc.get("enabled", True):
            from stereotv.remote import SerialRemote
            self.remote = SerialRemote(self, rc.get("port", "auto"), int(rc.get("baud", 115200)))
            # started at the end of __init__ (see below): it reads app state from its own thread

    def shutdown(self) -> None:
        for svc in (self.identifier, self.audio, self.web, getattr(self, "remote", None)):
            if svc:
                try:
                    svc.stop()
                except Exception:  # noqa: BLE001
                    pass

    def _load_hardcoded(self) -> None:
        """Start with nothing playing unless config pins a release (dev/demo only)."""
        rid = int(self.cfg["now_playing"].get("release_id", 0))
        if rid == 0:
            log.info("nothing playing at start; waiting for the identifier")
            return
        rel = None
        if rid > 0:
            try:
                con = discogs.open_db()
                rel = discogs.get_release(con, rid)
                con.close()
            except Exception as e:  # noqa: BLE001
                log.warning("collection db unavailable: %s", e)
        if rel is None:
            rel = FALLBACK
        self.now.set(rel, source="hardcoded")
        log.info("now playing (hardcoded): %s - %s (%s)", rel.artist, rel.title, rel.year)

    # ------------------------------------------------------------
    def _load_state(self) -> dict:
        try:
            import json
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self) -> None:
        try:
            import json
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"channel": self.cur, "theme": self.d.theme}))
        except OSError as e:
            log.debug("state save: %s", e)

    def set_theme(self, name: str) -> None:
        self.d.apply_theme(name)
        self._save_state()
        # channels cache rendered surfaces; bump NowPlaying's version so they rebuild
        self.now.version += 1
        for ch in self.channels.values():
            for attr in ("_card_key", "_page_key"):
                if hasattr(ch, attr):
                    setattr(ch, attr, None)
            if hasattr(ch, "_cache") and isinstance(getattr(ch, "_cache"), dict):
                ch._cache.clear()
            if hasattr(ch, "_tick_surf"):
                ch._tick_surf = None
                ch._loaded = 0.0
        self.snow_left = SNOW_TIME
        self.theme_osd_left = 2.5

    def draw_theme_osd(self, surface: pygame.Surface) -> None:
        from stereotv import themes
        d = self.d
        label = themes.get(d.theme).get("label", d.theme.upper())
        f = d.fonts["md"]
        w = f.size(label)[0] + 48
        box = pygame.Rect(0, 0, w, 48)
        box.center = (d.w // 2, d.h // 2)
        pygame.draw.rect(surface, D.BLACK, box)
        pygame.draw.rect(surface, D.YELLOW, box, 2)
        d.text(label, "md", D.YELLOW, box.center, anchor="center", shadow=False)

    def wake(self, manual: bool = False) -> None:
        """Leave the screensaver. manual=True (key/dial/API) also holds it off for a while."""
        now = time.monotonic()
        self.silent_since = now
        if manual:
            self.awake_until = now + self.key_wake_s
        elif self.manual_saver:
            return                      # audio doesn't clear a manually started screensaver
        self.manual_saver = False
        if self.saving:
            self.saving = False
            self.saver.leave()
            self.snow_left = SNOW_TIME
            self.osd_left = OSD_TIME

    def check_idle(self) -> None:
        """Enter the screensaver after idle_s of silence; leave as soon as audio returns."""
        if not self.saver:
            return
        if not self.audio or not self.audio.alive:
            if self.saver_request:
                self.saver_request = False
                self.saving = True
                self.saver.enter()
            return
        now = time.monotonic()
        if self.saver_request:
            # start it, or switch modes in place if it's already showing
            self.saver_request = False
            self.saving = True
            self.silent_since = now
            self.saver.enter()
            return
        if self.manual_saver and self.saving:
            return                      # stays until a key press
        rms = self.audio.rms(0.5)
        if rms > self.wake_rms:
            # sound must be clearly above the noise floor AND sustained; a blip doesn't count
            if self.loud_since is None:
                self.loud_since = now
            if now - self.loud_since >= self.wake_hold:
                self.silent_since = now
                self.wake()
        else:
            self.loud_since = None
            if not self.saving and now >= self.awake_until and now - self.silent_since >= self.idle_s:
                self.saving = True
                self.saver.enter()

    def change_channel(self, n: int) -> None:
        self.wake(manual=True)
        n = (n - 1) % NUM_CHANNELS + 1
        if n == self.cur:
            return
        self.channels[self.cur].leave()
        self.cur = n
        self.channels[self.cur].enter()
        self.snow_left = SNOW_TIME
        self.osd_left = OSD_TIME
        log.info("channel %d (%s)", n, self.channels[n].name)
        self._save_state()

    def handle_events(self) -> None:
        events = pygame.event.get()
        for e in events:
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == pygame.KEYDOWN:
                ch = self.channels[self.cur]
                if not self.saving and hasattr(ch, "handle_key") and ch.handle_key(e.key):
                    continue
                if e.key in (pygame.K_ESCAPE, pygame.K_q):
                    self.running = False
                elif e.key == pygame.K_s:
                    self.d.scanlines_on = not self.d.scanlines_on
                elif e.key == pygame.K_t:
                    from stereotv import themes
                    self.set_theme(themes.next_name(self.d.theme))
                elif e.key == pygame.K_SPACE:
                    # Space: start the screensaver; again while showing = next saver
                    self.manual_saver = True
                    self.saver_request = True
        if any(e.type == pygame.KEYDOWN and e.key != pygame.K_SPACE for e in events):
            self.wake(manual=True)
        delta, absolute = self.dial.poll(events)
        if self.theme_request:
            name, self.theme_request = self.theme_request, None
            self.set_theme(name)
        if self.channel_ctl and self.channel_ctl.pending is not None:
            absolute, self.channel_ctl.pending = self.channel_ctl.pending, None
        if absolute is not None:
            self.change_channel(absolute)
        elif delta:
            self.change_channel(self.cur + delta)

    def draw_osd(self, surface: pygame.Surface) -> None:
        d = self.d
        box = pygame.Rect(0, 0, 76, 52)
        box.topright = (d.safe.right, d.safe.top)
        pygame.draw.rect(surface, D.BLACK, box)
        pygame.draw.rect(surface, D.GREEN, box, 2)
        d.text(f"{self.cur:02d}", "mono_lg", D.GREEN, box.center, anchor="center", shadow=False)

    def run(self) -> int:
        dt = 1.0 / self.d.fps
        frames = 0
        while self.running:
            self.handle_events()
            self.check_idle()
            ch = self.channels[self.cur]
            ch.update(dt)
            if self.snow_left > 0:
                self.snow_left -= dt
                self.d.snow()
            elif self.saving:
                self.saver.update(dt)
                self.saver.draw(self.d.surface)
            else:
                ch.draw(self.d.surface)
            if self.osd_left > 0:
                self.osd_left -= dt
                self.draw_osd(self.d.surface)
            if self.theme_osd_left > 0:
                self.theme_osd_left -= dt
                if self.snow_left <= 0:
                    self.draw_theme_osd(self.d.surface)
            dt = self.d.flip()
            frames += 1
            if frames % (self.d.fps * 30) == 0:
                log.debug("fps %.1f", self.d.clock.get_fps())
        self.shutdown()
        self.d.quit()
        return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stereotv")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--windowed", action="store_true", help="run in a window (dev)")
    ap.add_argument("--frames", type=int, default=0, help="exit after N frames (smoke test)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    cfg = config.load()
    config.ensure_dirs()
    if args.windowed:
        cfg["display"]["fullscreen"] = False
    app = App(cfg)
    signal.signal(signal.SIGTERM, lambda *_: setattr(app, "running", False))
    if args.frames:
        # smoke test: cycle every channel then exit
        for i in range(args.frames):
            if i and i % 10 == 0:
                app.change_channel(app.cur + 1)
            app.handle_events()
            app.channels[app.cur].update(1 / 30)
            app.d.snow() if app.snow_left > 0 else app.channels[app.cur].draw(app.d.surface)
            app.snow_left -= 1 / 30
            app.draw_osd(app.d.surface)
            app.d.flip()
        pygame.image.save(app.d.surface, os.environ.get("STEREOTV_SHOT", "/tmp/stereotv.png"))
        app.shutdown()
        app.d.quit()
        return 0
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
