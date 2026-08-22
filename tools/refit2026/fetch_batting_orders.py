#!/usr/bin/env python3
"""
Fetch each game's BATTING ORDER (the lineup card) from MLB statsapi, so the
batter-side pooled features can identify the top-3 hitters pre-game for every
season.  boxscore_top3 covers 2024 fully and 2025 barely (202 games); the
2026 ledger has lineup_json at ~80%.  This fills the gap with one source.

Output: data/cache/batting_order/<game_pk>.json  {"away": [ids], "home": [ids]}
Resume-safe, polite, no key.  Run in the background.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "cache" / "batting_order"
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://statsapi.mlb.com/api/v1/game/{pk}/boxscore"


def main() -> int:
    pks = set()
    for f in ["data/backtests/backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv",
              "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv",
              "data/picks_2026.csv"]:
        pks |= set(int(x) for x in pd.read_csv(ROOT / f, low_memory=False, usecols=["game_pk"])["game_pk"].dropna())
    import sys as _sys
    todo = [pk for pk in sorted(pks) if not (OUT / f"{pk}.json").exists()]
    if "--reverse" in _sys.argv:          # second instance walks from the other end
        todo = todo[::-1]
    print(f"games {len(pks)}  to fetch {len(todo)}", flush=True)
    s = requests.Session(); ok = fail = 0; t0 = time.time()
    for i, pk in enumerate(todo, 1):
        if (OUT / f"{pk}.json").exists():      # another instance got it
            continue
        try:
            r = s.get(URL.format(pk=pk), timeout=20)
            if r.status_code == 200:
                j = r.json().get("teams", {})
                out = {side: [int(x) for x in (j.get(side, {}).get("battingOrder") or [])]
                       for side in ("away", "home")}
                (OUT / f"{pk}.json").write_text(json.dumps(out), encoding="utf-8"); ok += 1
            else:
                fail += 1
        except Exception:  # noqa: BLE001
            fail += 1
        if i % 250 == 0:
            el = time.time() - t0
            print(f"  {i}/{len(todo)} ok={ok} fail={fail} {el/60:.1f}min eta {el/i*(len(todo)-i)/60:.1f}min", flush=True)
        time.sleep(0.1)
    print(f"DONE ok={ok} fail={fail} in {(time.time()-t0)/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
