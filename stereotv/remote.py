"""Wired touchscreen remote (ESP32 "CYD") over USB serial.

Protocol, newline-terminated, 115200 baud:
  remote -> pi:  CH <n>            change channel
                 KEY <name>        SPACE | RETURN | p | s | LEFT | RIGHT ...  (same as the keyboard)
                 SAVER [mode]      start the screensaver
                 PING
  pi -> remote:  S <json>          every second: {"ch":5,"name":"GUIDE","np":"Artist · Title","st":"LOCKED","sv":0}
                 OK / ERR <msg>
"""
from __future__ import annotations

import glob
import json
import logging
import threading
import time

import pygame

log = logging.getLogger("stereotv.remote")

KEYS = {"SPACE": pygame.K_SPACE, "RETURN": pygame.K_RETURN, "ENTER": pygame.K_RETURN, "LEFT": pygame.K_LEFT,
        "RIGHT": pygame.K_RIGHT, "UP": pygame.K_UP, "DOWN": pygame.K_DOWN, "ESC": pygame.K_ESCAPE}


def find_port(pattern: str = "auto") -> str | None:
    if pattern != "auto":
        return pattern
    for pat in ("/dev/serial/by-id/*CH340*", "/dev/serial/by-id/*CP210*", "/dev/serial/by-id/*", "/dev/ttyUSB*", "/dev/ttyACM*"):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[0]
    return None


class SerialRemote(threading.Thread):
    def __init__(self, app, port: str = "auto", baud: int = 115200):
        super().__init__(name="remote", daemon=True)
        self.app = app
        self.port_pattern = port
        self.baud = baud
        self._stop = threading.Event()
        self.connected = False

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ commands
    def handle(self, line: str) -> str:
        parts = line.strip().split()
        if not parts:
            return ""
        cmd, args = parts[0].upper(), parts[1:]
        app = self.app
        if cmd == "CH" and args and args[0].isdigit():
            app.channel_ctl.request(int(args[0]))
            return "OK"
        if cmd == "KEY" and args:
            name = args[0]
            key = KEYS.get(name.upper()) or (ord(name.lower()) if len(name) == 1 else None)
            if key is None:
                return f"ERR unknown key {name}"
            pygame.event.post(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode="", scancode=0))
            return "OK"
        if cmd == "SAVER":
            return "OK" if app.channel_ctl.saver(args[0] if args else None) else "ERR no saver"
        if cmd == "PING":
            return "OK"
        if cmd == "T":
            log.info("touch %s", " ".join(args))
            return ""
        return f"ERR unknown {cmd}"

    def status(self) -> str:
        app = self.app
        ch = app.channels.get(app.cur)
        rel = app.now.release
        return "S " + json.dumps({
            "ch": app.cur, "name": ch.name if ch else "",
            "np": f"{rel.artist} · {rel.title}" if rel else "",
            "st": app.now.status or ("" if rel else "NOTHING PLAYING"),
            "sv": 1 if getattr(app, "saving", False) else 0,
        }, separators=(",", ":"))

    # ------------------------------------------------------------ loop
    def run(self) -> None:
        try:
            import serial  # noqa: WPS433
        except ImportError:
            log.error("pyserial not installed (.venv/bin/pip install pyserial); remote disabled")
            return
        backoff = 2.0
        while not self._stop.is_set():
            port = find_port(self.port_pattern)
            if not port:
                time.sleep(5)
                continue
            try:
                with serial.Serial(port, self.baud, timeout=0.2) as ser:
                    log.info("remote connected on %s", port)
                    self.connected = True
                    backoff = 2.0
                    last = 0.0
                    buf = b""
                    while not self._stop.is_set():
                        data = ser.read(64)
                        if data:
                            buf += data
                            while b"\n" in buf:
                                line, buf = buf.split(b"\n", 1)
                                text = line.decode(errors="replace")
                                reply = self.handle(text)
                                log.debug("remote: %r -> %s", text, reply)
                                if reply:
                                    ser.write((reply + "\n").encode())
                        if time.monotonic() - last >= 1.0:
                            last = time.monotonic()
                            try:
                                ser.write((self.status() + "\n").encode())
                            except Exception as e:  # noqa: BLE001  (never let a status glitch kill the link)
                                log.warning("status: %s", e)
            except (OSError, serial.SerialException) as e:
                log.log(logging.DEBUG if self.connected is False else logging.WARNING, "remote %s: %s", port, e)
            self.connected = False
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
