#!/usr/bin/env python3
"""tools/heal_2026_09_04_min_cws_prob.py

ONE-SHOT restore of the PUBLISHED probability on 2026-09-04 MIN@CWS
(game_pk 824554), STRONG YRFI 3u at -138, WIN +2.174u, from the T8.35 stamp.

WHAT WENT WRONG (AUDIT T8.44, CHANGELOG [2026-09-05g])
=====================================================
The bet was sized at the lock (22:43Z) on YRFI 62.24% -- `sizing_prob`,
the probability the Kelly sizer actually used -- and quarter-Kelly on
0.6224 @ -138 is exactly the 3u the ledger carries.  The row's PUBLISHED
probability is 59.07%, and the rule at 59.07% is 1u, which is why
`tools/pl_calc.py` / `tools/stake_drift.py` flag it.

The 59.07% is a post-game re-score.  During the T8.42 runner outage the
git ledger was two days stale; Railway rebuilds its ledger from git on
every redeploy and every push redeploys it; with no local row for the
game, each fresh container scored MIN@CWS as a brand-new pick -- mid-game
at 8:20 PM ET, then the GitHub recovery run post-game at 11 PM ET -- and
the mirror wrote those numbers over the pre-game record.  The bet columns
survived because the mirror preserves blanks.  The mechanism is closed by
T8.44 (`log_picks` no longer scores a started game as a new row).

WHAT THIS CHANGES
=================
    yrfi_prob   0.5907 -> 0.6224   (= sizing_prob, the lock-time value)
    nrfi_prob   0.4093 -> 0.3776   (= 1 - sizing_prob)

Nothing else.  `nrfi_prob_raw`, the feature columns and the shadow columns
are post-game values too, but no pre-game copy of them survives (Railway's
container has been replaced; no SSH key is registered), so they are left
as they are and this file says so.  bet_placed / units_risked / odds /
edge_on_pick / P&L are untouched -- they were right all along.

The heal REFUSES to run unless the row still looks exactly as diagnosed
(bet Y, YRFI, 3u, sizing_prob 0.6224, published 0.5907) and unless the
shipped sizer reproduces the 3u from the stamp -- if anything has moved,
that is a different decision.

Both stores are written: the CSV atomically via tracker._write_rows, and
Supabase through the ordinary mirror (the probability columns are not
preserve-on-blank, so the mirror carries them).  Every changed cell is
journaled to data/diagnostics/heals/min_cws_prob_<utc>.csv.

USAGE
=====
  python tools/heal_2026_09_04_min_cws_prob.py            # report only
  python tools/heal_2026_09_04_min_cws_prob.py --apply    # write
Then: python tools/pl_calc.py --window season   (expect no STAKE DRIFT row)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tracker  # noqa: E402

JOURNAL_DIR = Path(tracker.__file__).resolve().parent / "data" / "diagnostics" / "heals"
DATE, GAME_PK, SEASON = "2026-09-04", "824554", 2026
EXPECT = {"bet_placed": "Y", "pick_side": "YRFI", "sizing_prob": 0.6224,
          "units_risked": 3.0, "yrfi_prob": 0.5907, "market_yrfi_odds": -138.0}


def _f(v) -> float | None:
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def run(apply: bool) -> int:
    path = tracker._csv_path(SEASON)
    rows = tracker._read_rows(path)
    hits = [i for i, r in enumerate(rows)
            if r.get("date") == DATE and str(r.get("game_pk")) == GAME_PK]
    if len(hits) != 1:
        print(f"REFUSED: expected exactly one row for {DATE}/{GAME_PK}, found {len(hits)}")
        return 2
    row = rows[hits[0]]
    tag = f"{DATE} {row.get('away_team')}@{row.get('home_team')} pk {GAME_PK}"

    problems = []
    if (row.get("bet_placed") or "").strip().upper() != EXPECT["bet_placed"]:
        problems.append(f"bet_placed={row.get('bet_placed')!r}")
    if (row.get("pick_side") or "").strip().upper() != EXPECT["pick_side"]:
        problems.append(f"pick_side={row.get('pick_side')!r}")
    for col in ("sizing_prob", "units_risked", "yrfi_prob", "market_yrfi_odds"):
        got = _f(row.get(col))
        if got is None or abs(got - EXPECT[col]) > 1e-9:
            problems.append(f"{col}={row.get(col)!r} (expected {EXPECT[col]})")
    stamp = _f(row.get("sizing_prob"))
    rule = (tracker.kelly_stake_units(stamp, row.get("market_yrfi_odds", ""), season=SEASON)
            if stamp is not None else None)
    if rule is None or abs(rule - EXPECT["units_risked"]) > 1e-9:
        problems.append(f"kelly_stake_units(stamp) = {rule} (expected {EXPECT['units_risked']})")
    if problems:
        print(f"REFUSED: {tag} is not the row that was diagnosed:")
        for p in problems:
            print("   ", p)
        return 2

    new_yrfi = tracker._fmt(stamp, 4)
    new_nrfi = tracker._fmt(1.0 - stamp, 4)
    before = {"nrfi_prob": row.get("nrfi_prob", ""), "yrfi_prob": row.get("yrfi_prob", "")}
    print(f"{tag}: sizing_prob {stamp} -> rule stake {rule:.2f}u == ledger "
          f"{_f(row.get('units_risked')):.2f}u  (stake correct)")
    print(f"   yrfi_prob {before['yrfi_prob']} -> {new_yrfi}")
    print(f"   nrfi_prob {before['nrfi_prob']} -> {new_nrfi}")
    print("   raw probability, features, shadow columns: left as they are (no pre-game copy survives)")
    if not apply:
        print("[dry-run] nothing written.  Re-run with --apply.")
        return 0

    now = tracker._now_utc()
    row["yrfi_prob"] = new_yrfi
    row["nrfi_prob"] = new_nrfi
    tracker._write_rows(path, rows)
    tracker._mirror_picks_to_supabase(SEASON, [row])
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    jpath = JOURNAL_DIR / f"min_cws_prob_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.csv"
    with open(jpath, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=("date", "game_pk", "field", "before", "after", "source", "healed_at"))
        w.writeheader()
        for fld in ("yrfi_prob", "nrfi_prob"):
            w.writerow({"date": DATE, "game_pk": GAME_PK, "field": fld,
                        "before": before[fld], "after": row[fld],
                        "source": "sizing_prob (T8.35 stamp)", "healed_at": now})
    print(f"wrote {path.name}, mirrored 1 row to Supabase, journal: {jpath}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true", help="write (default: report only)")
    return run(ap.parse_args().apply)


if __name__ == "__main__":
    sys.exit(main())
