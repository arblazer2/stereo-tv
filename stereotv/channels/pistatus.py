"""Channel 12: Pi Status — temperature, clocks, throttle flags, load, memory, Wi-Fi, uptime, app stats."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import threading
import time

import pygame

from stereotv import display as D
from stereotv.channels.base import Channel

log = logging.getLogger("stereotv.ch.pistatus")

REFRESH_S = 3.0
THROTTLE_BITS = {0: "UNDER-VOLTAGE NOW", 1: "ARM FREQ CAPPED NOW", 2: "THROTTLED NOW", 3: "SOFT TEMP LIMIT NOW",
                 16: "under-voltage occurred", 17: "freq cap occurred", 18: "throttling occurred", 19: "soft temp limit occurred"}


def _vc(args: str) -> str:
    try:
        return subprocess.run(["vcgencmd", *args.split()], capture_output=True, text=True, timeout=2).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _uptime_str(up: float) -> str:
    return f"{int(up // 86400)}d {int(up % 86400 // 3600)}h {int(up % 3600 // 60)}m"


def collect(audio=None, now=None, fps: float = 0.0) -> dict:
    import platform
    s: dict = {}
    s["pi"] = bool(shutil.which("vcgencmd"))
    s["host"] = f"{platform.system()} {platform.release()}".strip()
    try:
        import psutil  # noqa: WPS433
    except ImportError:
        psutil = None
    # ---- temperature / clocks / power: Pi firmware, else psutil sensors, else blank
    t = _vc("measure_temp")
    if "=" in t:
        s["temp"] = t.split("=")[1]
    else:
        raw = _read("/sys/class/thermal/thermal_zone0/temp")
        s["temp"] = f"{int(raw) / 1000:.1f}'C" if raw.isdigit() else ""
        if not s["temp"] and psutil and hasattr(psutil, "sensors_temperatures"):
            try:
                for name, entries in (psutil.sensors_temperatures() or {}).items():
                    if entries:
                        s["temp"] = f"{entries[0].current:.1f}'C"; break
            except Exception:  # noqa: BLE001
                pass
    c = _vc("measure_clock arm")
    s["arm_mhz"] = int(c.split("=")[1]) // 1_000_000 if "=" in c else (int(psutil.cpu_freq().current) if psutil and psutil.cpu_freq() else 0)
    v = _vc("measure_volts core")
    s["core_v"] = v.split("=")[1] if "=" in v else ""
    th = _vc("get_throttled")
    flags = int(th.split("=")[1], 16) if "=" in th else 0
    s["throttled"] = flags
    s["throttle_now"] = [n for b, n in THROTTLE_BITS.items() if b < 16 and flags & (1 << b)]
    s["throttle_past"] = [n for b, n in THROTTLE_BITS.items() if b >= 16 and flags & (1 << b)]
    # ---- load / memory / uptime: /proc on Linux, psutil elsewhere
    if hasattr(os, "getloadavg"):
        s["load"] = os.getloadavg()
        s["cpu_pct"] = None
    else:
        s["load"] = None
        s["cpu_pct"] = psutil.cpu_percent(interval=None) if psutil else None
    mem = dict(re.findall(r"(\w+):\s+(\d+)", _read("/proc/meminfo")))
    if mem:
        s["mem_used_mb"] = (int(mem.get("MemTotal", 0)) - int(mem.get("MemAvailable", 0))) // 1024
        s["mem_total_mb"] = int(mem.get("MemTotal", 0)) // 1024
    elif psutil:
        vm = psutil.virtual_memory()
        s["mem_used_mb"], s["mem_total_mb"] = (vm.total - vm.available) // 2**20, vm.total // 2**20
    else:
        s["mem_used_mb"] = s["mem_total_mb"] = 0
    du = shutil.disk_usage(os.path.abspath(os.sep))
    s["disk_used_gb"], s["disk_total_gb"] = du.used / 1e9, du.total / 1e9
    up_raw = _read("/proc/uptime").split()
    up = float(up_raw[0]) if up_raw else ((time.time() - psutil.boot_time()) if psutil else 0.0)
    s["uptime"] = _uptime_str(up)
    wl = _read("/proc/net/wireless").splitlines()
    s["wifi"] = ""
    for ln in wl[2:]:
        parts = ln.split()
        if len(parts) > 3:
            s["wifi"] = f"{parts[0].rstrip(':')} {float(parts[3]):.0f} dBm  (link {float(parts[2]):.0f}%)"
    try:
        import socket
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sk.connect(("8.8.8.8", 80))
        s["ip"] = sk.getsockname()[0]; sk.close()
    except OSError:
        s["ip"] = "?"
    s["fps"] = fps
    if audio is not None:
        a = audio.stats()
        s["audio"] = f"{a['rms_db']} dB" if a["alive"] else "NO LINE-IN"
        s["audio_dev"] = a["device"]
    if now is not None:
        s["id"] = f"{now.source.upper()} {now.status}".strip()
    s["at"] = time.time()
    return s


class PiStatusChannel(Channel):
    number = 12
    name = "SYSTEM STATUS"

    def __init__(self, display, now, audio=None, fps_fn=None):
        super().__init__(display, now)
        self.audio = audio
        self.fps_fn = fps_fn or (lambda: 0.0)
        self.s: dict = {}
        self._busy = False
        self._last = 0.0

    def update(self, dt: float) -> None:
        if not self._busy and time.monotonic() - self._last > REFRESH_S:
            self._busy = True
            self._last = time.monotonic()
            threading.Thread(target=self._collect, daemon=True).start()

    def _collect(self) -> None:
        try:
            self.s = collect(self.audio, self.now, self.fps_fn())
        except Exception as e:  # noqa: BLE001
            log.warning("status: %s", e)
        self._busy = False

    def draw(self, surface: pygame.Surface) -> None:
        d = self.d
        surface.fill(D.DARK)
        bar = self.header(surface, f"stereo-tv @ {self.s.get('ip', '…')}", color=D.GREEN)
        safe = d.safe
        s = self.s
        if not s:
            d.text("READING…", "md", D.GREEN, (safe.left, bar.bottom + 20))
            return
        green, dim = D.GREEN, (90, 140, 90)
        x1, x2 = safe.left, safe.left + 170
        y = bar.bottom + 14
        rows = [("HOST", s.get("host", ""))] if not s.get("pi") else []
        if s["temp"]:
            rows.append(("CPU TEMP", s["temp"].replace("'C", "°C")))
        if s.get("pi"):
            rows += [("ARM CLOCK", f"{s['arm_mhz']} MHz" + ("" if s["arm_mhz"] >= 1200 else "  ← throttled")),
                     ("CORE VOLT", s["core_v"])]
        elif s["arm_mhz"]:
            rows.append(("CPU CLOCK", f"{s['arm_mhz']} MHz"))
        if s.get("load") is not None:
            rows.append(("LOAD", "  ".join(f"{v:.2f}" for v in s["load"])))
        elif s.get("cpu_pct") is not None:
            rows.append(("CPU", f"{s['cpu_pct']:.0f}%"))
        rows += [
            ("MEMORY", f"{s['mem_used_mb']} / {s['mem_total_mb']} MB"),
            ("DISK", f"{s['disk_used_gb']:.1f} / {s['disk_total_gb']:.1f} GB"),
            ("NETWORK" if not s["wifi"] else "WI-FI", s["wifi"] or s.get("ip", "")),
            ("UPTIME", s["uptime"]),
            ("DISPLAY", f"{d.w}×{d.h} @ {s['fps']:.0f} fps"),
            ("LINE-IN", s.get("audio", "")),
            ("IDENTIFY", s.get("id", "")),
        ]
        for k, v in rows:
            d.text(k, "mono", dim, (x1, y), shadow=False)
            d.text(d.fit_text(str(v), "mono", safe.right - x2), "mono", green, (x2, y), shadow=False)
            y += 28
        # throttle flags (Pi firmware only)
        y += 4
        if not s.get("pi"):
            pass
        elif s["throttle_now"]:
            for n in s["throttle_now"]:
                d.text("⚠ " + n, "sm", D.RED, (x1, y), shadow=False); y += 26
        elif s["throttle_past"]:
            d.text("since boot: " + ", ".join(s["throttle_past"]), "sm", D.AMBER, (x1, y), shadow=False); y += 26
        else:
            d.text(f"POWER OK  (throttled=0x{s['throttled']:x})", "sm", green, (x1, y), shadow=False); y += 26
        # blinking cursor, terminal style
        if int(time.time() * 2) % 2:
            pygame.draw.rect(surface, green, (x1, safe.bottom - 22, 14, 20))
