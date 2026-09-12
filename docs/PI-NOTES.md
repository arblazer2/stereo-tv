# Pi / hardware notes

Lessons learned getting this running on a Raspberry Pi 3 with Raspberry Pi OS Lite (64-bit, KMS driver).
`deploy/install.sh` handles the package side; the rest is here in case something looks wrong.

## Display (kmsdrm, no X)

- SDL's kmsdrm backend needs `libegl1 libegl-mesa0 libgles2 libgl1-mesa-dri` even for plain 2D. Lite doesn't
  ship them. Without `libgl1-mesa-dri` (the vc4/v3d driver) the app runs but the screen stays black.
- SDL must use the `opengles2` renderer; `display.py` sets `SDL_RENDER_DRIVER=opengles2`. The default `opengl`
  renderer silently presents nothing because there's no desktop libGL on a Pi.
- kmsdrm refuses to start if no connector is "connected". If your display has no EDID (some CRTs, some
  adapters), append `video=HDMI-A-1:640x480@60D` to `/boot/firmware/cmdline.txt` — the `D` forces the output on.
  A VGA CRT monitor usually lists 640x480@85; use `@85D` for a steadier picture.
- Set `display.sdl_debug = true` in config.toml to get SDL's verbose log in the journal.
- CRT? Set `display.scanlines = false` (the tube has real ones) and `display.drift = true` (nudges the whole
  picture a few px every few minutes against burn-in).
- The service runs as your user with `SupplementaryGroups=video render input tty audio dialout`.

## Audio

- `audio.py` runs one `arecord` process into a ring buffer shared by the identifier and the visualizer. It needs
  `alsa-utils`. `arecord -l` lists capture devices; the wizard picks one.
- A USB-C headset dongle's mic input works as a mono line-in: set `[audio] channels = 1`,
  `mixer_control = "Mic"`, `mixer_gain = "40%"` (applied at every start). Check levels with `GET /api/audio`:
  aim for peaks around 0.4–0.8, RMS around -20 dB.
- Tune `identify.silence_rms` so the idle floor reads as silence (line-in floor is far below a room mic's).

## Identifier

- shazamio 0.8 needs `audioop-lts` on Python 3.13 and ships aarch64 wheels, so no Rust build on the Pi.
- After a match the identifier arms a track clock from Discogs durations and Shazam's offset, advances to the
  next track, and verifies. Consecutive tracks decide between sibling pressings (studio album vs compilation).

## Data

- SQLite in WAL mode with a 30 s busy timeout so nightly enrichment and the running app coexist.
- Nightly timer: `sync → tracks → wiki → musicbrainz → market`. MusicBrainz is 1 req/s and sometimes slow;
  missing rows are retried the next night.

## Restarting without sudo

`kill -9 $(systemctl show -p MainPID --value stereo-tv)` — the unit has `Restart=always`, so systemd relaunches
it with the new code. `scripts/deploy.sh` does exactly this after an rsync.

## ESP32 CYD remote

See `remote/cyd/README.md`. The two gotchas: the display must be on HSPI (`#define USE_HSPI_PORT`) so the
touch controller can have VSPI, and don't rely on the touch IRQ pin. You can flash it from the Pi with
`esptool` (installed into the venv) over the same USB cable.
