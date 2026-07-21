"""Shoot-day weather via the National Weather Service (free, no key).

Runs during each refresh: if a shoot happens within the next 2 days, geocode
its location (US Census geocoder; falls back to the configured home coordinates) and store
the NWS forecast for that date in sync_state. Fail-quiet: any error leaves
the previous forecast alone; the dashboard simply shows no weather.
"""
import json
import ssl
import urllib.parse
import urllib.request

import certifi

from app import config as appconfig
from app import db

_CFG = appconfig.load()
FALLBACK = (_CFG["home_lat"], _CFG["home_lon"])
UA = {"User-Agent": _CFG["api_user_agent"]}
SSL_CTX = ssl.create_default_context(cafile=certifi.where())


def _get(url: str, timeout: int = 6) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return json.loads(r.read().decode())


def _geocode(location: str) -> tuple[float, float]:
    if not location:
        return FALLBACK
    try:
        q = urllib.parse.quote(location)
        d = _get("https://geocoding.geo.census.gov/geocoder/locations/"
                 f"onelineaddress?address={q}&benchmark=Public_AR_Current"
                 "&format=json")
        matches = d.get("result", {}).get("addressMatches", [])
        if matches:
            c = matches[0]["coordinates"]
            return (c["y"], c["x"])
    except Exception:
        pass
    return FALLBACK


def update(conn) -> str | None:
    row = conn.execute(
        "SELECT title, location, start_at FROM events WHERE is_shoot=1"
        " AND date(start_at) BETWEEN date('now') AND date('now','+2 day')"
        " ORDER BY start_at LIMIT 1").fetchone()
    if not row:
        return None
    shoot_date = row["start_at"][:10]
    prev = db.get_state(conn, "weather")
    try:
        lat, lon = _geocode(row["location"])
        points = _get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}")
        forecast = _get(points["properties"]["forecast"])
        period = next(
            (p for p in forecast["properties"]["periods"]
             if p["startTime"][:10] == shoot_date and p["isDaytime"]), None)
        if not period:
            return None
        summary = (f"{period['shortForecast']}, {period['temperature']}°"
                   f"{period['temperatureUnit']} · wind {period['windSpeed']}")
        db.set_state(conn, "weather", json.dumps({
            "date": shoot_date, "location": row["location"],
            "summary": summary}))
        return summary
    except Exception:
        # keep prior forecast if it's still for the right date
        if prev and json.loads(prev).get("date") == shoot_date:
            return json.loads(prev)["summary"]
        return None
