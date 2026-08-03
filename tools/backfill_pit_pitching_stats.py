#!/usr/bin/env python3
"""
tools/backfill_pit_pitching_stats.py -- kill the ERA/FIP/WHIP leakage in the
historical backtest CSVs.

THE LEAK
--------
`home_era`, `home_fip`, `home_whip`, `home_k9`, `home_bb9`, `home_hr9` (and
the away_ twins) in data/backtests/*_truepit*.csv are SEASON-FINAL values.
Measured 2026-08-03 on the 2025 file, pitchers with >=5 starts:

    home_fip   191 of 191 (100%) have ZERO within-season variation
    home_era   191 of 191 (100%)
    home_obp   174 of 191  (91%)
    home_xera    2 of 186   (1%)   <- already point-in-time, the template

So an April game is predicted using the pitcher's September numbers. That
is future data, it is worth roughly AUC +0.011 of fake signal (about a
third of the model's entire edge), and it inflates `home_fip`'s learned
weight ~10x. Any weight fitted on this is partly fitted to the leak and
cannot hold at serve time.

THE FIX
-------
data/cache/pitcher_gamelog_v2/<pid>_<season>.json holds per-start lines
(ip, er, k, bb, hr, h) for 2021-2026, and data/pitcher_id_cache.json maps
game_pk -> [away_pid, home_pid]. So for every row we can rebuild each
stat from ONLY the starts that had already happened:

    ERA  = 9*ER/IP        WHIP = (BB+H)/IP
    K9   = 9*K/IP         BB9  = 9*BB/IP        HR9 = 9*HR/IP
    FIP  = (13*HR + 3*BB - 2*K)/IP + cFIP

cFIP is DERIVED from the data rather than hardcoded: it is defined as
whatever makes league FIP equal league ERA, so we compute it per season
from the same logs. (No HBP in the cache, so the BB term is walks only;
that shifts the constant, not the ranking, and the constant is absorbed.)

FALLBACK LADDER, applied per pitcher per game, and COUNTED so the mix is
visible rather than assumed:

    1. season-to-date, if the pitcher already has >= MIN_IP innings
    2. the PRIOR season's final line (complete before this season began,
       so it cannot leak forward)
    3. that season's league average

Tier 1 is the honest answer. Tier 2 is what the original xera fix used
for everything, and is a strict lower bound on the feature's information.
Tier 3 is a last resort for a true debut.

OUTPUT
------
Writes a sibling `*_ptfix.csv` per input. Originals are NEVER modified,
so a before/after comparison stays possible.

USAGE
-----
    python tools/backfill_pit_pitching_stats.py
    python tools/backfill_pit_pitching_stats.py --min-ip 20
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / "data" / "cache" / "pitcher_gamelog_v2"
IDMAP = ROOT / "data" / "pitcher_id_cache.json"

# The six columns that carry the leak, per side.
STATS = ("era", "fip", "whip", "k9", "bb9", "hr9")


def ids_for(row: dict, idmap: dict) -> tuple[int | None, int | None] | None:
    """(away_pid, home_pid) for a backtest row.

    THE ROW'S OWN COLUMNS WIN. The 2026 files carry `away_pitcher_id` /
    `home_pitcher_id` directly and the 2024/2025 files do not, so an
    idmap-only lookup resolved 2026 at 62% and 0% and sent almost every
    2026 slot to the league-average fallback. The game_pk map stays as
    the fallback for the older files, which have no id columns at all.
    """
    out: list[int | None] = []
    for col in ("away_pitcher_id", "home_pitcher_id"):
        v = str(row.get(col, "") or "").strip()
        if not v:
            out.append(None)
            continue
        try:
            out.append(int(float(v)))
        except ValueError:
            out.append(None)
    if out[0] is not None or out[1] is not None:
        return out[0], out[1]
    ids = idmap.get(str(row.get("game_pk", "")).strip())
    if ids:
        return int(ids[0]), int(ids[1])
    return None


def load_log(pid: int, season: int) -> list[dict]:
    p = LOGS / f"{pid}_{season}.json"
    if not p.exists():
        return []
    try:
        rows = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            ip = float(r.get("ip") or 0)
        except Exception:
            continue
        if ip <= 0:
            continue
        out.append({
            "date": str(r.get("date") or ""),
            "ip": ip,
            "er": float(r.get("er") or 0),
            "k": float(r.get("k") or 0),
            "bb": float(r.get("bb") or 0),
            "hr": float(r.get("hr") or 0),
            "h": float(r.get("h") or 0),
        })
    out.sort(key=lambda x: x["date"])
    return out


def rates(acc: dict, cfip: float) -> dict | None:
    """Turn a cumulative line into the six rate stats."""
    ip = acc["ip"]
    if ip <= 0:
        return None
    return {
        "era": 9.0 * acc["er"] / ip,
        "whip": (acc["bb"] + acc["h"]) / ip,
        "k9": 9.0 * acc["k"] / ip,
        "bb9": 9.0 * acc["bb"] / ip,
        "hr9": 9.0 * acc["hr"] / ip,
        "fip": (13.0 * acc["hr"] + 3.0 * acc["bb"] - 2.0 * acc["k"]) / ip + cfip,
    }


def season_totals(log: list[dict]) -> dict:
    a = defaultdict(float)
    for g in log:
        for k in ("ip", "er", "k", "bb", "hr", "h"):
            a[k] += g[k]
    return dict(a)


def derive_cfip(season: int, pids: set[int]) -> tuple[float, dict]:
    """cFIP is whatever makes league FIP == league ERA, computed from the
    same logs the stats come from. Also returns the league line so tier 3
    has something honest to fall back to."""
    a = defaultdict(float)
    for pid in pids:
        for g in load_log(pid, season):
            for k in ("ip", "er", "k", "bb", "hr", "h"):
                a[k] += g[k]
    ip = a["ip"]
    if ip <= 0:
        return 3.10, {}
    lg_era = 9.0 * a["er"] / ip
    raw_fip = (13.0 * a["hr"] + 3.0 * a["bb"] - 2.0 * a["k"]) / ip
    cfip = lg_era - raw_fip
    league = rates(dict(a), cfip) or {}
    return cfip, league


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-ip", type=float, default=20.0,
                    help="innings of season-to-date needed before tier 1 is used")
    args = ap.parse_args()

    idmap = json.loads(IDMAP.read_text(encoding="utf-8"))
    files = sorted(glob.glob(str(ROOT / "data" / "backtests" / "backtest_202*_truepit*.csv")))
    files = [f for f in files if "_ptfix" not in f]
    if not files:
        print("no backtest CSVs found", file=sys.stderr)
        return 1

    for path in files:
        base = os.path.basename(path)
        season = int(base[9:13])
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
        if not rows:
            continue

        # every pitcher who appears, so cFIP is computed on the right pool
        pids: set[int] = set()
        for r in rows:
            ids = ids_for(r, idmap)
            if ids:
                pids.update(int(x) for x in ids if x is not None)
        cfip, league = derive_cfip(season, pids)

        logs: dict[int, list[dict]] = {p: load_log(p, season) for p in pids}
        prior: dict[int, dict | None] = {}
        for p in pids:
            pl = load_log(p, season - 1)
            prior[p] = rates(season_totals(pl), cfip) if pl else None

        tier = defaultdict(int)
        for r in rows:
            ids = ids_for(r, idmap)
            date = str(r.get("date", "")).strip()
            for slot, pid in zip(("away", "home"), ids or (None, None)):
                vals = None
                if pid is not None:
                    pid = int(pid)
                    acc = defaultdict(float)
                    for g in logs.get(pid, []):
                        if g["date"] >= date:      # STRICTLY earlier only
                            break
                        for k in ("ip", "er", "k", "bb", "hr", "h"):
                            acc[k] += g[k]
                    if acc["ip"] >= args.min_ip:
                        vals = rates(dict(acc), cfip); tier["season-to-date"] += 1
                    elif prior.get(pid):
                        vals = prior[pid]; tier["prior season"] += 1
                if vals is None:
                    vals = league; tier["league average"] += 1
                for s in STATS:
                    if f"{slot}_{s}" in r and s in vals:
                        r[f"{slot}_{s}"] = f"{vals[s]:.4f}"

        out = path.replace(".csv", "_ptfix.csv")
        with open(out, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

        tot = sum(tier.values())
        print(f"{base}  ->  {os.path.basename(out)}")
        print(f"   season {season}   cFIP {cfip:+.3f}   {len(rows)} rows, {tot} pitcher-slots")
        for k in ("season-to-date", "prior season", "league average"):
            print(f"     {k:16s} {tier[k]:5d}  ({tier[k]/tot*100:5.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
