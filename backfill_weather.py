#!/usr/bin/env python3
"""
backfill_weather.py -- add wx_temp_c, wx_wind_kmh, wx_humidity, wx_is_dome
to picks_2026.csv.

The 2024/2025 backtest CSVs already have these (added during backtest
generation).  picks_2026.csv was created by the live predictor before
weather was a model feature, so the columns are missing.  This script
fills them in so the calibrator and the retro rebet see the same
weather inputs the production predictor will emit going forward.

For domed parks (DOMED_PARKS in backtest.py), all wx_* values are blank
EXCEPT wx_is_dome which is set to 1.  The trainer + predictor coerce
blanks to neutral defaults (20 C / 10 km/h / 60% humidity), and is_dome
acts as the model's "ignore weather" switch.

For outdoor parks, fetches the open-meteo cached archive via
backtest.game_weather() at the date of the game.

Idempotent: safe to re-run -- only fills cells that are missing.
"""

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from backtest import game_weather, DOMED_PARKS, PARK_COORDS
from mlb_first_inning_predictor import _fetch_open_meteo_forecast

PICKS_PATH = ROOT / "data" / "picks_2026.csv"
WX_COLS = ["wx_temp_c", "wx_wind_kmh", "wx_humidity", "wx_is_dome"]

# Cache the forecast endpoint per (park, season) so we don't hammer it.
# The forecast endpoint returns 92 past_days + 2 forecast_days in one call.
_forecast_cache: dict = {}


def _date_table_for(home_abbr: str, season: int) -> dict[str, dict]:
    """Single forecast fetch -> dict[date_iso] -> wx record for a given park."""
    key = (home_abbr, season)
    if key in _forecast_cache:
        return _forecast_cache[key]
    if home_abbr not in PARK_COORDS:
        _forecast_cache[key] = {}
        return {}
    lat, lon = PARK_COORDS[home_abbr]
    import urllib.request
    import json as _json
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,wind_speed_10m,relative_humidity_2m"
        "&past_days=92&forecast_days=2"
        "&timezone=America/New_York"
    )
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [forecast {home_abbr}] {e}")
        _forecast_cache[key] = {}
        return {}
    hourly = payload.get("hourly", {})
    times  = hourly.get("time", [])
    temps  = hourly.get("temperature_2m", [])
    winds  = hourly.get("wind_speed_10m", [])
    hums   = hourly.get("relative_humidity_2m", [])
    out: dict[str, dict] = {}
    for i, ts in enumerate(times):
        if not ts or "T" not in ts:
            continue
        d, hh = ts.split("T", 1)
        h = hh[:2]
        rec = out.get(d, {})
        if h == "19" or "hour" not in rec:
            rec["hour"]     = h
            rec["temp_c"]   = temps[i] if i < len(temps) else None
            rec["wind_kmh"] = winds[i] if i < len(winds) else None
            rec["humidity"] = hums[i]  if i < len(hums)  else None
            out[d] = rec
    _forecast_cache[key] = out
    return out


def main():
    with open(PICKS_PATH, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header = list(reader.fieldnames or [])
        rows = list(reader)

    if not rows:
        sys.exit("picks_2026.csv is empty.")

    added = []
    for c in WX_COLS:
        if c not in header:
            header.append(c)
            added.append(c)
    if added:
        print(f"  Adding columns: {', '.join(added)}")

    n_dome = n_outdoor = n_filled = n_skipped = 0
    last_date = None
    for r in rows:
        home = (r.get("home_team") or "").strip()
        date_iso = (r.get("date") or "").strip()
        season  = int((r.get("season") or "2026").strip() or 2026)
        if not home or not date_iso:
            n_skipped += 1
            continue

        if date_iso != last_date:
            print(f"  ... {date_iso}")
            last_date = date_iso

        if home in DOMED_PARKS:
            r["wx_temp_c"]   = ""
            r["wx_wind_kmh"] = ""
            r["wx_humidity"] = ""
            r["wx_is_dome"]  = "1"
            n_dome += 1
            n_filled += 1
            continue

        wx = game_weather(home, date_iso, season, use_cache=True) or {}
        # Archive miss for current-year dates -- fall back to forecast cache
        if wx.get("temp_c") is None or wx.get("wind_kmh") is None or wx.get("humidity") is None:
            tab = _date_table_for(home, season)
            rec = tab.get(date_iso, {})
            if wx.get("temp_c")   is None: wx["temp_c"]   = rec.get("temp_c")
            if wx.get("wind_kmh") is None: wx["wind_kmh"] = rec.get("wind_kmh")
            if wx.get("humidity") is None: wx["humidity"] = rec.get("humidity")
        r["wx_temp_c"]   = "" if wx.get("temp_c")   is None else f"{float(wx['temp_c']):.2f}"
        r["wx_wind_kmh"] = "" if wx.get("wind_kmh") is None else f"{float(wx['wind_kmh']):.2f}"
        r["wx_humidity"] = "" if wx.get("humidity") is None else f"{float(wx['humidity']):.1f}"
        r["wx_is_dome"]  = "0"
        n_outdoor += 1
        n_filled  += 1

    with open(PICKS_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n  Total rows: {len(rows)}")
    print(f"  Filled: {n_filled}  (dome={n_dome}, outdoor={n_outdoor})")
    print(f"  Skipped: {n_skipped}")


if __name__ == "__main__":
    main()
