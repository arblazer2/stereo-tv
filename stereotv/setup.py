"""First-run setup wizard: python -m stereotv.setup

Asks for the Discogs username + token (and verifies them), your location (ZIP or
"City, ST", geocoded via Open-Meteo), units, the audio capture device, and the
weather provider. Writes ~/.config/stereo-tv/config.toml and the token files,
then offers to run the first collection sync.

Non-interactive (for scripts):
  python -m stereotv.setup --discogs-user NAME --discogs-token TOKEN --location "72501" \
      --units imperial --audio "plughw:CARD=CODEC,DEV=0" --channels 2 --yes
"""
from __future__ import annotations

import argparse
import getpass
import re
import subprocess
import sys
from pathlib import Path

import requests

from stereotv import config

GEO = "https://geocoding-api.open-meteo.com/v1/search"
US_STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
             "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
             "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
             "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
             "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
             "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina",
             "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
             "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas",
             "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
             "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia"}


def ask(prompt: str, default: str = "", secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        v = (getpass.getpass(f"{prompt}: ") if secret else input(f"{prompt}{suffix}: ")).strip()
        if v:
            return v
        if default:
            return default
        if secret:
            print("  (nothing entered)")


def verify_discogs(token: str, ua: str) -> str | None:
    r = requests.get("https://api.discogs.com/oauth/identity", timeout=15,
                     headers={"Authorization": f"Discogs token={token}", "User-Agent": ua})
    return r.json().get("username") if r.status_code == 200 else None


def geocode(query: str) -> list[dict]:
    q = query.strip()
    m = re.match(r"^\s*([^,]+),\s*([A-Za-z]{2})\s*$", q)      # "City, ST" -> search city, filter by admin1 code
    name = m.group(1) if m else q
    r = requests.get(GEO, params={"name": name, "count": 10, "language": "en", "format": "json"}, timeout=15)
    r.raise_for_status()
    hits = r.json().get("results", []) or []
    if m:
        st = m.group(2).upper()
        full = US_STATES.get(st, "").lower()
        filtered = [h for h in hits if (h.get("admin1") or "").lower() in (full, st.lower())
                    or (h.get("admin1_code") or "").upper().endswith("." + st)]
        hits = filtered or hits
    return hits


def list_capture_devices() -> list[tuple[str, str]]:
    """[(alsa device string, description)] from `arecord -l`."""
    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    devs = []
    for m in re.finditer(r"card (\d+): (\S+) \[(.*?)\], device (\d+): (.*?) \[", out):
        card_id, desc, devnum, devname = m.group(2), m.group(3), m.group(4), m.group(5)
        devs.append((f"plughw:CARD={card_id},DEV={devnum}", f"{desc} ({devname})"))
    return devs


def toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_config(values: dict) -> Path:
    config.ensure_dirs()
    lines = [
        "# Written by `python -m stereotv.setup`. See config.example.toml for every option.",
        "",
        "[app]",
        f"contact = {toml_str(values['contact'])}",
        "",
        "[location]",
        f"name = {toml_str(values['loc_name'])}",
        f"lat = {values['lat']}",
        f"lon = {values['lon']}",
        f"units = {toml_str(values['units'])}",
        "",
        "[discogs]",
        f"username = {toml_str(values['discogs_user'])}",
        "",
        "[audio]",
        f"device = {toml_str(values['audio'])}",
        f"channels = {values['channels']}",
    ]
    if values.get("mixer"):
        lines += [f"mixer_control = {toml_str(values['mixer'][0])}", f"mixer_gain = {toml_str(values['mixer'][1])}"]
    lines += ["", "[weather]", f"provider = {toml_str(values['weather'])}"]
    if values["weather"] == "homeassistant":
        lines += ["", "[ha]", f"url = {toml_str(values['ha_url'])}", f"weather_entity = {toml_str(values['ha_entity'])}"]
    lines += ["", "[display]", f"theme = {toml_str(values.get('theme', 'cable88'))}"]
    lines += ["", "[radar]", f"zoom = {values['zoom']}", ""]
    config.CONFIG_FILE.write_text("\n".join(lines))
    return config.CONFIG_FILE


def save_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value.strip() + "\n")
    path.chmod(0o600)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stereotv.setup")
    ap.add_argument("--discogs-user"); ap.add_argument("--discogs-token")
    ap.add_argument("--location", help='ZIP/postal code or "City, ST"')
    ap.add_argument("--units", choices=["imperial", "metric"])
    ap.add_argument("--audio", help="ALSA capture device, e.g. plughw:CARD=CODEC,DEV=0")
    ap.add_argument("--channels", type=int)
    ap.add_argument("--contact", help="URL or email for API User-Agents")
    ap.add_argument("--weather", choices=["open-meteo", "homeassistant"])
    ap.add_argument("--ha-url"); ap.add_argument("--ha-token"); ap.add_argument("--ha-entity", default="weather.home")
    ap.add_argument("--zoom", type=int, help="radar zoom: 7 state, 8 region, 9 county")
    ap.add_argument("--theme", choices=["cable88", "prevue", "teletext", "phosphor", "vaporwave", "amber", "weather95", "arcade"])
    ap.add_argument("--yes", action="store_true", help="no prompts; fail on anything missing")
    ap.add_argument("--no-sync", action="store_true")
    a = ap.parse_args(argv)
    interactive = not a.yes
    v: dict = {}

    print("stereo-tv setup\n===============")
    v["contact"] = a.contact or (ask("Contact for API user-agents (a URL or email; sites like Discogs/Wikipedia ask for one)",
                                     config.DEFAULTS["app"]["contact"]) if interactive else config.DEFAULTS["app"]["contact"])
    ua = f"stereo-tv/0.1 (+{v['contact']})"

    # ---- Discogs
    print("\nDiscogs: generate a personal access token at https://www.discogs.com/settings/developers")
    v["discogs_user"] = a.discogs_user or (ask("Discogs username") if interactive else "")
    token = a.discogs_token or (ask("Discogs token (hidden)", secret=True) if interactive else "")
    if not v["discogs_user"] or not token:
        print("Discogs username and token are required", file=sys.stderr); return 2
    who = verify_discogs(token, ua)
    if not who:
        print("  ✗ token rejected by Discogs", file=sys.stderr); return 2
    if who.lower() != v["discogs_user"].lower():
        print(f"  note: token belongs to {who}, using that")
        v["discogs_user"] = who
    print(f"  ✓ authenticated as {who}")
    save_secret(Path(config.DEFAULTS["discogs"]["token_file"]), token)

    # ---- Location
    print("\nLocation (for the weather channel and radar)")
    q = a.location or (ask('ZIP / postal code, or "City, ST"') if interactive else "")
    hit = None
    while True:
        if not q:
            break
        try:
            hits = geocode(q)
        except requests.RequestException as e:
            print(f"  geocoding failed: {e}"); hits = []
        if hits and (len(hits) == 1 or not interactive):
            hit = hits[0]
        elif hits:
            for i, h in enumerate(hits[:6], 1):
                print(f"  {i}. {h.get('name')}, {h.get('admin1', '')} {h.get('country_code', '')}  ({h['latitude']:.2f}, {h['longitude']:.2f})")
            pick = ask("Which one", "1")
            hit = hits[int(pick) - 1] if pick.isdigit() and 0 < int(pick) <= len(hits[:6]) else hits[0]
        if hit or not interactive:
            break
        print("  nothing found"); q = ask('Try again: ZIP or "City, ST"')
    if not hit:
        print("A location is required", file=sys.stderr); return 2
    v["lat"], v["lon"] = round(hit["latitude"], 4), round(hit["longitude"], 4)
    v["loc_name"] = hit.get("admin1") or hit.get("name") or q       # radar title: the state/region
    print(f"  ✓ {hit.get('name')}, {hit.get('admin1', '')} ({v['lat']}, {v['lon']})")
    v["units"] = a.units or (ask("Units (imperial/metric)", "imperial" if (hit.get("country_code") == "US") else "metric") if interactive else "imperial")
    v["zoom"] = a.zoom or 7

    # ---- Audio
    print("\nAudio capture device (the line-in from your stereo)")
    devs = list_capture_devices()
    if a.audio:
        v["audio"] = a.audio
    elif devs and interactive:
        for i, (d, desc) in enumerate(devs, 1):
            print(f"  {i}. {desc}  [{d}]")
        pick = ask("Which one (number, or type an ALSA device)", "1")
        v["audio"] = devs[int(pick) - 1][0] if pick.isdigit() and 0 < int(pick) <= len(devs) else pick
    else:
        v["audio"] = devs[0][0] if devs else "plughw:CARD=CODEC,DEV=0"
        if not devs:
            print("  no capture device found now; using the default (change [audio].device later)")
    v["channels"] = a.channels or (int(ask("Channels (2 for a line-in interface, 1 for a mic/dongle)", "2")) if interactive else 2)
    v["mixer"] = None
    if v["channels"] == 1:
        # a mic-input dongle fed with line level needs its gain pulled down; start sensible
        g = ask("Capture gain for the mic input (%; ~40 for line level into a mic jack)", "40") if interactive else "40"
        v["mixer"] = ("Mic", f"{g}%")

    # ---- Weather provider
    v["weather"] = a.weather or (ask("Weather source (open-meteo = free, no key; homeassistant)", "open-meteo") if interactive else "open-meteo")
    if v["weather"] == "homeassistant":
        v["ha_url"] = a.ha_url or ask("Home Assistant URL", "http://homeassistant.local:8123")
        v["ha_entity"] = a.ha_entity
        tok = a.ha_token or ask("HA long-lived access token (hidden)", secret=True)
        save_secret(Path(config.DEFAULTS["ha"]["token_file"]), tok)

    v["theme"] = a.theme or (ask("Look: cable88 (late-80s cable), prevue, teletext, phosphor, vaporwave, amber, weather95, arcade", "cable88") if interactive else "cable88")
    path = write_config(v)
    print(f"\n✓ wrote {path}")
    if not a.no_sync and (a.yes or ask("Sync your Discogs collection now? (y/n)", "y").lower().startswith("y")):
        from stereotv import discogs
        print("Syncing… (covers, tracklists and notes follow; the nightly timer keeps it fresh)")
        rc = discogs.main(["sync"])
        if rc == 0:
            discogs.main(["tracks"])
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
