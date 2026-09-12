"""Shared line-in stream.

One `arecord` subprocess reads the USB interface; we keep a rolling mono
float32 ring buffer that both the identifier (10 s clips) and the visualizer
(latest window for FFT) read from.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger("stereotv.audio")


class AudioStream:
    def __init__(self, device: str = "plughw:CARD=CODEC,DEV=0", rate: int = 44100,
                 channels: int = 2, buffer_seconds: float = 15.0, chunk_frames: int = 2048,
                 mixer_control: str = "", mixer_gain: str = ""):
        self.device = device
        self.mixer_control = mixer_control      # e.g. "Mic"; set on every (re)start so it survives reboots
        self.mixer_gain = mixer_gain            # e.g. "40%"
        self.rate = rate
        self.channels = channels
        self.chunk_frames = chunk_frames
        self.n = int(rate * buffer_seconds)
        self.buf = np.zeros(self.n, dtype=np.float32)
        self.pos = 0                      # write cursor
        self.total = 0                    # frames written since start
        self.level = 0.0                  # RMS of last chunk (0..1)
        self.peak = 0.0                   # peak abs sample of last chunk
        self.lock = threading.Lock()
        self.alive = False                # arecord currently delivering data
        self._stop = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="audio", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._proc:
            self._proc.kill()

    def _apply_mixer(self) -> None:
        if not (self.mixer_control and self.mixer_gain and shutil.which("amixer")):
            return
        m = re.search(r"CARD=([^,]+)", self.device)
        dev = ["-D", f"hw:CARD={m.group(1)}"] if m else []
        r = subprocess.run(["amixer", *dev, "sset", self.mixer_control, self.mixer_gain],
                           capture_output=True, text=True)
        if r.returncode == 0:
            log.info("mixer %s = %s", self.mixer_control, self.mixer_gain)
        else:
            log.warning("mixer set failed: %s", (r.stderr or r.stdout).strip()[:80])

    def _cmd(self) -> list[str]:
        return ["arecord", "-q", "-D", self.device, "-f", "S16_LE", "-r", str(self.rate),
                "-c", str(self.channels), "-t", "raw", "--buffer-size", str(self.chunk_frames * 4)]

    def _run(self) -> None:
        if self.device.startswith("file:"):
            self._run_file(self.device[5:])
            return
        use_sd = self.device.startswith("sd:") or (not shutil.which("arecord")) or self.device == "auto" and not shutil.which("arecord")
        if use_sd:
            self._run_sounddevice(self.device[3:] if self.device.startswith("sd:") else "")
            return
        if self.device == "auto":
            self.device = "default"
        backoff = 2.0
        last_err = None
        while not self._stop.is_set():
            try:
                self._apply_mixer()
                self._proc = subprocess.Popen(self._cmd(), stdout=subprocess.PIPE,
                                              stderr=subprocess.PIPE, bufsize=0)
                log.debug("arecord started on %s", self.device)
                self._pump(self._proc)
                if self.alive:
                    last_err = None
                err = (self._proc.stderr.read() or b"").decode(errors="replace").strip()
                if err and not self._stop.is_set():
                    msg = err.splitlines()[-1]
                    # first occurrence at WARNING, repeats at DEBUG (device unplugged -> retry every 30 s)
                    log.log(logging.DEBUG if msg == last_err else logging.WARNING,
                            "arecord exited: %s (retrying every %.0fs)", msg, backoff)
                    last_err = msg
            except Exception as e:  # noqa: BLE001
                log.warning("audio error: %s", e)
            self.alive = False
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    # ------------------------------------------------------------ portable backend: sounddevice (PortAudio)
    def _run_sounddevice(self, which: str) -> None:
        """Windows / macOS / any PortAudio host. `which` = device index, a name substring, or '' for default."""
        try:
            import sounddevice as sd  # noqa: WPS433
        except Exception as e:  # noqa: BLE001
            log.error("no arecord and sounddevice unavailable (%s); audio disabled", e)
            return
        dev = None
        if which:
            if which.isdigit():
                dev = int(which)
            else:
                for i, d in enumerate(sd.query_devices()):
                    if d["max_input_channels"] > 0 and which.lower() in d["name"].lower():
                        dev = i
                        break
        backoff = 2.0
        while not self._stop.is_set():
            try:
                info = sd.query_devices(dev, "input")
                ch = min(self.channels, int(info["max_input_channels"])) or 1

                def cb(indata, frames, t, status):
                    if not self.alive:
                        log.info("line-in active on %s", info["name"])
                    self.alive = True
                    mono = indata.mean(axis=1) if indata.ndim > 1 else indata[:, 0]
                    self.push(mono.astype(np.float32, copy=False))

                with sd.InputStream(device=dev, channels=ch, samplerate=self.rate, dtype="float32",
                                    blocksize=self.chunk_frames, callback=cb):
                    log.debug("sounddevice stream on %s (%d ch)", info["name"], ch)
                    while not self._stop.is_set():
                        time.sleep(0.25)
                    return
            except Exception as e:  # noqa: BLE001
                self.alive = False
                log.warning("sounddevice: %s (retrying in %.0fs)", str(e)[:90], backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    # ------------------------------------------------------------ dev source: loop a file
    def _run_file(self, path: str, gap_seconds: float = 4.0) -> None:
        """Feed a WAV (or anything ffmpeg can decode) in real time, looping with a
        silence gap so the identifier sees a "new side" each pass."""
        try:
            mono = load_audio_file(Path(path), self.rate)
        except Exception as e:  # noqa: BLE001
            log.error("file source %s: %s", path, e)
            return
        log.info("file source %s (%.0fs) looping", path, len(mono) / self.rate)
        gap = np.zeros(int(gap_seconds * self.rate), dtype=np.float32)
        chunk = self.chunk_frames
        while not self._stop.is_set():
            for data in (mono, gap):
                for i in range(0, len(data), chunk):
                    if self._stop.is_set():
                        return
                    self.alive = True
                    self.push(data[i:i + chunk])
                    time.sleep(chunk / self.rate)

    def _pump(self, proc: subprocess.Popen) -> None:
        nbytes = self.chunk_frames * self.channels * 2
        while not self._stop.is_set():
            data = proc.stdout.read(nbytes)
            if not data:
                return
            if not self.alive:
                log.info("line-in active on %s", self.device)
            self.alive = True
            pcm = np.frombuffer(data, dtype=np.int16)
            if self.channels > 1:
                pcm = pcm.reshape(-1, self.channels).mean(axis=1)
            self.push(pcm.astype(np.float32) / 32768.0)

    # ------------------------------------------------------------ buffer
    def push(self, mono: np.ndarray) -> None:
        """Append mono float32 samples (also used by tests / fake sources)."""
        k = len(mono)
        if k == 0:
            return
        with self.lock:
            end = self.pos + k
            if end <= self.n:
                self.buf[self.pos:end] = mono
            else:
                first = self.n - self.pos
                self.buf[self.pos:] = mono[:first]
                self.buf[: k - first] = mono[first:]
            self.pos = end % self.n
            self.total += k
            self.level = float(np.sqrt(np.mean(mono * mono)))
            self.peak = float(np.max(np.abs(mono)))

    def latest(self, seconds: float) -> np.ndarray:
        """Most recent `seconds` of mono audio, oldest first."""
        k = min(int(seconds * self.rate), self.n, self.total)
        with self.lock:
            start = (self.pos - k) % self.n
            if start + k <= self.n:
                return self.buf[start:start + k].copy()
            return np.concatenate((self.buf[start:], self.buf[: (start + k) % self.n]))

    def stats(self) -> dict:
        x = self.latest(1.0)
        rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        return {"device": self.device, "alive": self.alive, "rate": self.rate,
                "rms": round(rms, 4), "rms_db": round(20 * np.log10(rms + 1e-9), 1),
                "peak": round(float(np.max(np.abs(x))) if len(x) else 0.0, 3)}

    def rms(self, seconds: float = 1.0) -> float:
        x = self.latest(seconds)
        return float(np.sqrt(np.mean(x * x))) if len(x) else 0.0

    def write_wav(self, path: Path, seconds: float) -> Path:
        x = self.latest(seconds)
        pcm = np.clip(x * 32767.0, -32768, 32767).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(pcm.tobytes())
        return path


def load_audio_file(path: Path, rate: int) -> np.ndarray:
    """Decode to mono float32 at `rate`. WAV via stdlib; other formats via ffmpeg."""
    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as w:
            ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            raw = w.readframes(n)
        if sw == 2:
            pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif sw == 4:
            pcm = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        elif sw == 1:
            pcm = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
        else:
            raise ValueError(f"unsupported sample width {sw}")
        if ch > 1:
            pcm = pcm.reshape(-1, ch).mean(axis=1)
    else:
        if not shutil.which("ffmpeg"):
            raise RuntimeError("ffmpeg needed to decode non-WAV files")
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
                              "-ar", str(rate), "-"], capture_output=True, check=True).stdout
        return np.frombuffer(out, dtype=np.float32).copy()
    if sr != rate:
        x = np.arange(len(pcm)) * (rate / sr)
        pcm = np.interp(np.arange(0, x[-1], 1.0), x, pcm).astype(np.float32)
    return pcm


def list_devices() -> list[tuple[str, str]]:
    """[(device string, description)] — ALSA via arecord where available, else PortAudio via sounddevice."""
    devs: list[tuple[str, str]] = []
    if shutil.which("arecord"):
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True).stdout
        for m in re.finditer(r"card (\d+): (\S+) \[(.*?)\], device (\d+): (.*?) \[", out):
            devs.append((f"plughw:CARD={m.group(2)},DEV={m.group(4)}", f"{m.group(3)} ({m.group(5)})"))
    if not devs:
        try:
            import sounddevice as sd  # noqa: WPS433
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0:
                    devs.append((f"sd:{i}", f"{d['name']} ({d['max_input_channels']} ch)"))
        except Exception:  # noqa: BLE001
            pass
    return devs
