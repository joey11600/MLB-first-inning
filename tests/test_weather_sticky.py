"""A weather-fetch failure must not silently become a NEW model input.

THE INCIDENT (2026-08-23).  One Railway cycle lost its open-meteo fetch for
CLE@COL and `fetch_game_weather` substituted the neutral defaults -- 20 C /
10 km/h / 60% -- which are written into the ledger as ordinary numbers, so a
failed fetch is indistinguishable from a real mild day.  It happened on the
last cycle before first pitch, `_pick_is_locked` froze it, and the dashboard
served 60.0% for four hours against the committed ledger's 65.2%.  Rebuilt
from the row's own features: real weather -> 0.3483, defaults -> 0.4006,
Supabase served 0.4003.

THE RULE.  Defaults are the LAST resort, not the first:
    fresh cache -> live fetch -> last good reading (any age) -> defaults,
every reading carries a `source`, and the run reports its provenance.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mlb_first_inning_predictor as P  # noqa: E402

pytestmark = pytest.mark.money

PARK = "COL"          # outdoor; the incident's park
DATE = "2026-08-23"
REAL = {"temp_c": 30.6, "wind_kmh": 18.8, "humidity": 26.0}
DEFAULTS = (P.WX_TEMP_DEFAULT, P.WX_WIND_DEFAULT, P.WX_HUMIDITY_DEFAULT)


@pytest.fixture()
def wx(tmp_path, monkeypatch):
    """Isolate the cache on disk and control both upstreams.

    `calls` counts live fetches so the TTL test can prove a saved request.
    """
    monkeypatch.setattr(P, "_WX_CACHE_PATH", tmp_path / "weather_live.json")
    monkeypatch.setattr(P, "_wx_cache", None)
    monkeypatch.setattr(P, "_wx_cache_dirty", False)
    monkeypatch.setattr(P, "_wx_seeded", set())
    monkeypatch.setattr(P, "_wx_run_sources", {})
    # the ledger seed reads the real picks CSV; point it at an empty dir
    monkeypatch.setattr(P, "__file__", str(tmp_path / "predictor.py"))

    state = {"live": None, "calls": 0}

    def fake_forecast(lat, lon, date_iso):
        state["calls"] += 1
        return state["live"]

    monkeypatch.setattr(P, "_fetch_open_meteo_forecast", fake_forecast)

    # backtest.game_weather is imported INSIDE fetch_game_weather; stub the
    # module so the archive branch always misses and the coords exist.
    import types
    fake_bt = types.ModuleType("backtest")
    fake_bt.PARK_COORDS = {PARK: (39.7, -104.9)}
    fake_bt.game_weather = lambda *a, **k: {}
    monkeypatch.setitem(sys.modules, "backtest", fake_bt)
    return state


def test_a_live_reading_is_used_and_cached(wx):
    wx["live"] = dict(REAL)
    got = P.fetch_game_weather(PARK, DATE, 2026)
    assert got["source"] == "live"
    assert (got["temp_c"], got["wind_kmh"], got["humidity"]) == (30.6, 18.8, 26.0)
    assert P._wx_cache_get(PARK, DATE)["t"] == 30.6


def test_a_failed_fetch_reuses_the_last_good_reading_not_the_defaults(wx):
    """THE REGRESSION GUARD. This is the 2026-08-23 incident, in one test."""
    wx["live"] = dict(REAL)
    first = P.fetch_game_weather(PARK, DATE, 2026)
    P._wx_run_sources.clear()

    wx["live"] = None                       # the fetch that failed
    P._wx_cache_load()[f"{PARK}|{DATE}"]["at"] = "2020-01-01T00:00:00Z"  # force past TTL
    second = P.fetch_game_weather(PARK, DATE, 2026)

    assert second["source"] == "stale"
    assert (second["temp_c"], second["wind_kmh"], second["humidity"]) == \
           (first["temp_c"], first["wind_kmh"], first["humidity"])
    assert (second["temp_c"], second["wind_kmh"], second["humidity"]) != DEFAULTS


def test_defaults_only_when_nothing_was_ever_captured(wx):
    wx["live"] = None
    got = P.fetch_game_weather(PARK, DATE, 2026)
    assert got["source"] == "default"
    assert (got["temp_c"], got["wind_kmh"], got["humidity"]) == DEFAULTS


def test_a_fresh_reading_is_reused_within_the_ttl(wx):
    """The cadence fix: 12 cycles an hour must not be 12 requests an hour."""
    wx["live"] = dict(REAL)
    P.fetch_game_weather(PARK, DATE, 2026)
    P.fetch_game_weather(PARK, DATE, 2026)
    P.fetch_game_weather(PARK, DATE, 2026)
    assert wx["calls"] == 1
    assert P._wx_run_sources[PARK] == "cache"


def test_a_dome_is_untouched(wx):
    park = sorted(P.DOMED_PARKS)[0]
    got = P.fetch_game_weather(park, DATE, 2026)
    assert got["is_dome"] == 1.0 and got["source"] == "dome"
    assert wx["calls"] == 0


def test_the_ledger_seed_warms_a_cold_container_but_ignores_fallback_rows(tmp_path, monkeypatch, wx):
    """A redeploy wipes data/cache/ (gitignored), which is the state the
    incident happened in.  The committed ledger survives, so it seeds the
    cache -- except for rows that were THEMSELVES scored on the defaults."""
    data = tmp_path / "data"
    data.mkdir()
    hdr = "date,home_team,wx_temp_c,wx_wind_kmh,wx_humidity\n"
    (data / "picks_2026.csv").write_text(
        hdr
        + f"{DATE},COL,30.6,18.8,26.0\n"          # real -> seeds
        + f"{DATE},PIT,20.0,10.0,60.0\n"          # default signature -> ignored
        + f"{DATE},TOR,,,\n",                     # dome -> ignored
        encoding="utf-8")
    monkeypatch.setattr(P, "__file__", str(tmp_path / "predictor.py"))
    monkeypatch.setattr(P, "_wx_seeded", set())

    P._wx_seed_from_ledger(DATE)
    assert P._wx_cache_get("COL", DATE)["t"] == 30.6
    assert P._wx_cache_get("PIT", DATE) is None
    assert P._wx_cache_get("TOR", DATE) is None


def test_a_seeded_reading_does_not_suppress_the_live_fetch(tmp_path, monkeypatch, wx):
    """A seed is a PARACHUTE, not a fresh reading.  If seeding counted as
    fresh, every redeploy would serve up-to-TTL-minutes-old ledger weather
    instead of asking -- trading one stale-input bug for a smaller one."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "picks_2026.csv").write_text(
        "date,home_team,wx_temp_c,wx_wind_kmh,wx_humidity\n"
        f"{DATE},COL,30.6,18.8,26.0\n", encoding="utf-8")
    monkeypatch.setattr(P, "__file__", str(tmp_path / "predictor.py"))
    wx["live"] = {"temp_c": 27.5, "wind_kmh": 9.0, "humidity": 31.0}

    got = P.fetch_game_weather(PARK, DATE, 2026)
    assert got["source"] == "live" and got["temp_c"] == 27.5
    assert wx["calls"] == 1

    # ...and the seed is still there to catch a failure
    wx["live"] = None
    P._wx_cache_load()[f"{PARK}|{DATE}"]["at"] = ""      # unknown age again
    assert P.fetch_game_weather(PARK, DATE, 2026)["source"] == "stale"


def test_the_request_asks_only_for_the_days_it_needs(monkeypatch):
    """`past_days=92` on every game on every 5-minute cycle is what drew the
    refusals in the first place.  Today's slate needs one past day."""
    seen = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"hourly": {"time": [], "temperature_2m": [],
                                          "wind_speed_10m": [],
                                          "relative_humidity_2m": []}}).encode()

    import urllib.request

    def fake_urlopen(url, timeout=30):
        seen["url"] = url
        return FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo
    today = _dt.now(ZoneInfo("America/New_York")).date().isoformat()
    P._fetch_open_meteo_forecast(39.7, -104.9, today)
    assert "past_days=1" in seen["url"], seen["url"]
    assert "past_days=92" not in seen["url"]


def test_the_run_summary_counts_every_source(wx):
    wx["live"] = dict(REAL)
    P.fetch_game_weather(PARK, DATE, 2026)
    wx["live"] = None
    P.fetch_game_weather("PIT", DATE, 2026)         # no coords -> default
    P.fetch_game_weather(sorted(P.DOMED_PARKS)[0], DATE, 2026)
    c = P.weather_run_sources()
    assert c.get("live") == 1 and c.get("default") == 1 and c.get("dome") == 1
