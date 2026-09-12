# stereo-tv

A retro "cable TV" display for an analog stereo. A Raspberry Pi drives a CRT or
small monitor with fake cable channels whose content follows whatever record is
playing, identified automatically from a line-in feed.

![channels](docs/channels.png)

**What it does**

- Listens to your stereo (USB line-in), fingerprints the audio with Shazam, and
  resolves the match to *your* pressing in your Discogs collection — side and
  track included.
- 16 channels: Now Playing, Visualizer, Liner Notes (Wikipedia + Discogs notes),
  In Your Collection, Prevue-style Guide, Tracklist, Collection Stats, Random
  Spin, Personnel, Local Radar, Weather, System Status, Music News, This Day in
  Music, Collection Value, Test Pattern.
- Screensavers when the music stops: cover wall, flying covers, "Local on the 8s"
  weather, bouncing cover.
- A phone page to search the collection and override what's showing.
- Optional wired touchscreen remote (ESP32 "CYD"), see `remote/cyd/`.

Designed for 640×480 at 30 fps on a Pi 3: big fonts, 4:3, safe margins, no desktop. On a 16:9 monitor the
canvas is GPU-scaled with side bars, or set an 854×480 canvas in the wizard to fill the screen.

## Hardware

- Raspberry Pi 3 or newer, Raspberry Pi OS Lite (64-bit, Bookworm or Trixie).
- A display: any HDMI monitor/TV. A VGA CRT via an HDMI→VGA adapter looks great.
- A USB audio input fed from your amp's **tape out / rec out** (line level):
  Behringer UCA202 or any class-compliant interface. A USB-C headset dongle's
  mic input works in a pinch (mono).
- Optional: an ESP32-2432S028 "Cheap Yellow Display" as a wired remote.

## Install

On the Pi, as the user that will run it:

```bash
git clone https://github.com/arblazer2/stereo-tv.git ~/stereo-tv
cd ~/stereo-tv
deploy/install.sh              # apt deps, venv, groups, systemd units (asks for sudo)
.venv/bin/python -m stereotv.setup    # Discogs token, location, audio device, weather; first sync
sudo systemctl start stereo-tv
journalctl -u stereo-tv -f
```

The wizard asks for:

- your Discogs username and a **personal access token**
  (Discogs → Settings → Developers → Generate new token), verified on the spot;
- a ZIP / postal code or "City, ST" — geocoded for the radar and weather;
- which audio capture device to use (it lists what's plugged in);
- a weather source: Open-Meteo (free, no key) or Home Assistant.

The first sync pulls your collection, cover art, tracklists and release notes
(a few minutes per few hundred records; Discogs allows 60 requests/minute).
A nightly timer keeps everything fresh and adds original release dates
(MusicBrainz), Wikipedia summaries and marketplace values.

If the screen stays black on a Pi with the KMS driver, add
`video=HDMI-A-1:640x480@60D` to `/boot/firmware/cmdline.txt` — see `docs/PI-NOTES.md`.

## Using it

- Keyboard: **←/→ or 1–9, 0** change channel · **Space** screensaver (again = next) ·
  **T** cycle themes (Cable '88, Prevue, Teletext, Phosphor, Vaporwave, Amber, Weather '95, Arcade, Modern Dark, Modern Light) · **S** scanlines · **Q** quit (systemd restarts it). On channel 8: **Enter** re-spin, **P** play it.
- Phone: `http://<pi>:8080/` — search, tap a record to override auto-ID for 30 min.
- API: `GET /api/now`, `GET /api/audio` (input level, for gain tuning),
  `POST /api/channel?channel=N`, `POST /api/screensaver?mode=wall`.
- Config lives in `~/.config/stereo-tv/config.toml`; `config.example.toml` documents every key.

## Layout

```
stereotv/main.py         loop, channel registry, dial/keyboard, screensaver
stereotv/display.py      pygame/kmsdrm init, fonts, CRT effects, burn-in drift
stereotv/audio.py        arecord → ring buffer shared by identify + visualizer
stereotv/identify.py     silence/audio state machine → Shazam → collection match → track clock
stereotv/discogs.py      collection sync, covers, tracklists, notes, market values (SQLite)
stereotv/wiki.py         Wikipedia summaries      stereotv/musicbrainz.py  original release dates
stereotv/weather.py      Open-Meteo / HA          stereotv/radar.py        RainViewer + OSM tiles
stereotv/web.py          phone page + API         stereotv/remote.py       USB serial remote
stereotv/channels/       one file per channel     stereotv/screensaver.py  the savers
remote/cyd/              ESP32 touchscreen remote firmware
deploy/                  systemd units + installer
```

## Credits / data sources

Discogs API · Shazam (via shazamio) · Wikipedia REST API · MusicBrainz ·
Open-Meteo · RainViewer · OpenStreetMap tiles · RSS feeds from Pitchfork,
Rolling Stone, Stereogum and NME. Please keep a real contact in `[app] contact`
— several of these ask for one in the User-Agent.

MIT license.
