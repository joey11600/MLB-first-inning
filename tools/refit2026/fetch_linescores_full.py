#!/usr/bin/env python3
"""
Fetch FULL per-inning linescores from MLB statsapi for every game in the three
datasets, so multi-inning targets (F3, F5, full game) can be built.

Why: data/cache/linescore/ stores only the first-inning extract
({away_runs, home_runs}) the grader needs.  The structural hypothesis under
test -- that our inputs carry real information that a ONE-inning target is
too noisy to surface -- needs runs by inning.

Output: data/cache/linescore_full/<game_pk>.json  (gitignored dir)
  {"innings": [{"num": 1, "away": r, "home": r}, ...], "state": "..."}

Resume-safe (skips existing files), polite (sleep between calls), no key.
Run in the background:  python tools/refit2026/fetch_linescores_full.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "cache" / "linescore_full"
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://statsapi.mlb.com/api/v1/game/{pk}/linescore"
SLEEP = 0.12


def game_pks() -> list[int]:
    pks = set()
    for f, c in [("data/backtests/backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", "game_pk"),
                 ("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", "game_pk"),
                 ("data/picks_2026.csv", "game_pk")]:
        s = pd.read_csv(ROOT / f, low_memory=False, usecols=[c])[c].dropna()
        pks |= set(int(x) for x in s)
    return sorted(pks)


def main() -> int:
    pks = game_pks()
    todo = [pk for pk in pks if not (OUT / f"{pk}.json").exists()]
    print(f"games {len(pks)}  already cached {len(pks)-len(todo)}  to fetch {len(todo)}",
          flush=True)
    sess = requests.Session()
    ok = fail = 0
    t0 = time.time()
    for i, pk in enumerate(todo, 1):
        try:
            r = sess.get(URL.format(pk=pk), timeout=20)
            if r.status_code != 200:
                fail += 1
                continue
            j = r.json()
            inns = [{"num": x.get("num"),
                     "away": (x.get("away") or {}).get("runs"),
                     "home": (x.get("home") or {}).get("runs")}
                    for x in j.get("innings", [])]
            (OUT / f"{pk}.json").write_text(json.dumps(
                {"innings": inns, "state": j.get("currentInningOrdinal")}), encoding="utf-8")
            ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1
        if i % 200 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(todo)}  ok={ok} fail={fail}  {el/60:.1f} min  "
                  f"eta {el/i*(len(todo)-i)/60:.1f} min", flush=True)
        time.sleep(SLEEP)
    print(f"DONE ok={ok} fail={fail} in {(time.time()-t0)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
