"""Weather for the 'Local on the 8s' screensaver / channel.

Providers:
  open-meteo     (default) free, no key: current conditions + daily forecast for [location]
  homeassistant  your HA weather entity + weather.get_forecasts (token in ~/.config/stereo-tv/ha_token)
Cached; refreshed every 10 minutes.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from stereotv import config

log = logging.getLogger("stereotv.weather")

REFRESH_S = 600


@dataclass
class Weather:
    condition: str = ""
    temp: float | None = None
    unit: str = "°F"
    humidity: float | None = None
    wind_speed: float | None = None
    wind_bearing: float | None = None
    wind_unit: str = "mph"
    pressure: float | None = None
    daily: list[dict] = field(default_factory=list)   # [{datetime, condition, temperature, templow, precipitation_probability}]
    fetched_at: float = 0.0
    error: str = ""


def _token(cfg: dict) -> str | None:
    p = Path(cfg.get("token_file") or (config.CONFIG_DIR / "ha_token")).expanduser()
    if p.exists():
        return p.read_text().strip() or None
    return None


# WMO weather codes (Open-Meteo) -> the condition names the renderer knows (HA vocabulary)
_WMO = {0: "sunny", 1: "sunny", 2: "partlycloudy", 3: "cloudy", 45: "fog", 48: "fog",
        51: "rainy", 53: "rainy", 55: "rainy", 56: "snowy-rainy", 57: "snowy-rainy",
        61: "rainy", 63: "rainy", 65: "pouring", 66: "snowy-rainy", 67: "snowy-rainy",
        71: "snowy", 73: "snowy", 75: "snowy", 77: "snowy", 80: "rainy", 81: "rainy", 82: "pouring",
        85: "snowy", 86: "snowy", 95: "lightning-rainy", 96: "lightning-rainy", 99: "lightning-rainy"}


def fetch_open_meteo(loc: dict) -> Weather:
    w = Weather()
    lat, lon = float(loc.get("lat") or 0), float(loc.get("lon") or 0)
    if not lat and not lon:
        w.error = "no location (run python -m stereotv.setup)"
        return w
    imperial = (loc.get("units") or "imperial") == "imperial"
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 6,
        "current": "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m,wind_direction_10m,surface_pressure,is_day",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "temperature_unit": "fahrenheit" if imperial else "celsius",
        "wind_speed_unit": "mph" if imperial else "kmh",
    }
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=15)
        r.raise_for_status()
        j = r.json()
        c = j.get("current", {})
        code = int(c.get("weather_code", 0))
        w.condition = _WMO.get(code, "cloudy")
        if w.condition == "sunny" and not c.get("is_day", 1):
            w.condition = "clear-night"
        w.temp = c.get("temperature_2m")
        w.unit = "°F" if imperial else "°C"
        w.humidity = c.get("relative_humidity_2m")
        w.wind_speed = c.get("wind_speed_10m")
        w.wind_bearing = c.get("wind_direction_10m")
        w.wind_unit = "mph" if imperial else "km/h"
        hpa = c.get("surface_pressure")
        w.pressure = round(hpa * 0.02953, 2) if (hpa and imperial) else hpa
        d = j.get("daily", {})
        for i, day in enumerate(d.get("time", [])):
            w.daily.append({"datetime": day, "condition": _WMO.get(int(d["weather_code"][i]), "cloudy"),
                            "temperature": d["temperature_2m_max"][i], "templow": d["temperature_2m_min"][i],
                            "precipitation_probability": (d.get("precipitation_probability_max") or [None] * 9)[i]})
        w.daily = w.daily[:5]
        w.fetched_at = time.time()
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        w.error = str(e)[:80]
        log.warning("open-meteo: %s", w.error)
    return w


def fetch(cfg: dict) -> Weather:
    """cfg = the whole config. Dispatches on weather.provider."""
    provider = (cfg.get("weather", {}).get("provider") or "open-meteo").lower()
    if provider in ("homeassistant", "ha"):
        return fetch_ha(cfg.get("ha", {}))
    return fetch_open_meteo(cfg.get("location", {}))


def fetch_ha(cfg: dict) -> Weather:
    """cfg = config['ha']: url, weather_entity, token_file."""
    w = Weather()
    if not cfg.get("url"):
        w.error = "ha.url not set"
        return w
    tok = _token(cfg)
    if not tok:
        w.error = "no HA token"
        return w
    base = cfg["url"].rstrip("/")
    ent = cfg.get("weather_entity", "weather.home")
    h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    try:
        r = requests.get(f"{base}/api/states/{ent}", headers=h, timeout=10)
        r.raise_for_status()
        st = r.json()
        a = st.get("attributes", {})
        w.condition = st.get("state", "")
        w.temp = a.get("temperature")
        w.unit = a.get("temperature_unit", "°F")
        w.humidity = a.get("humidity")
        w.wind_speed = a.get("wind_speed")
        w.wind_bearing = a.get("wind_bearing")
        w.wind_unit = a.get("wind_speed_unit", "mph")
        w.pressure = a.get("pressure")
        r = requests.post(f"{base}/api/services/weather/get_forecasts?return_response", headers=h,
                          json={"entity_id": ent, "type": "daily"}, timeout=10)
        r.raise_for_status()
        resp = r.json().get("service_response", {})
        w.daily = (resp.get(ent) or {}).get("forecast", [])[:5]
        w.fetched_at = time.time()
    except requests.RequestException as e:
        w.error = str(e)[:80]
        log.warning("weather: %s", w.error)
    return w


class WeatherCache:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.data = Weather()
        self._lock = threading.Lock()
        self._busy = False

    def get(self) -> Weather:
        if not self._busy and time.time() - self.data.fetched_at > REFRESH_S:
            self._busy = True
            threading.Thread(target=self._refresh, daemon=True).start()
        return self.data

    def _refresh(self) -> None:
        w = fetch(self.cfg)
        if w.fetched_at or not self.data.fetched_at:
            self.data = w
        self._busy = False


def compass(bearing: float | None) -> str:
    if bearing is None:
        return ""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((bearing + 22.5) // 45) % 8]
