#!/usr/bin/env python3
"""
tools/merge_odds_books.py -- take N per-book odds CSVs and emit one
best-price CSV for `--import-odds`.

WHY THIS IS THE HIGHEST-VALUE REMAINING CHANGE
----------------------------------------------
Every bet in the ledger is DraftKings. There has never been any line
shopping. Measured on the 105 bets that clear the shipped STRONG gate
(average price -142):

    price improvement   P&L      gain     ROI
    none (today)        +14.15u    --    13.47%
    5 cents             +15.99u  +1.84u  15.23%
    10 cents            +17.97u  +3.83u  17.12%
    20 cents            +22.38u  +8.24u  21.32%

A 10-cent improvement is routine across three or four books on a
first-inning market. Unlike every model change tested during the
2026-07-27/28 investigation, this is a CERTAIN gain -- no out-of-sample
risk, no hindsight. It also compounds with Kelly, because a better price
raises the Kelly fraction as well as the payout.

BEST PRICE IS PER SIDE, NOT PER GAME.  We only ever bet one side, so the
NRFI and YRFI columns are optimised independently and may come from
different books. The merged row records which book won each side.

OPERATOR CAVEAT, and it is the whole ballgame: this only converts to real
money if you actually hold an account at the winning book. If you can
only bet DraftKings, treat the output as a diagnostic ("DK was 12 cents
off the best price on this game"), not as an instruction.

Usage:
    python tools/merge_odds_books.py data/odds/dk_2026-07-28.csv \\
                                     data/odds/fd_2026-07-28.csv \\
        --output data/odds/best_2026-07-28.csv

    python tools/merge_odds_books.py --self-test
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIELDS = ["date", "game_pk", "away_team", "home_team",
          "market_nrfi_odds", "market_yrfi_odds", "sportsbook",
          "start_time_utc", "best_nrfi_book", "best_yrfi_book",
          "nrfi_books_seen", "yrfi_books_seen",
          "nrfi_cents_gained", "yrfi_cents_gained"]


def parse_american(s):
    """'-115' / '+105' / '−115' (en-dash) -> float, or None."""
    if s is None:
        return None
    s = str(s).strip().replace("−", "-").replace("–", "-").replace(" ", "")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return None if v == 0 else v


def payout(odds: float) -> float:
    """Net profit per 1 staked -- the only correct way to compare prices.

    Comparing American odds numerically is WRONG across the +/- boundary:
    +100 pays more than -105, but 100 > -105 only by luck of sign, and
    -110 vs -105 inverts (the numerically smaller is better). Converting
    to payout makes 'higher is better' true everywhere.
    """
    return odds / 100.0 if odds > 0 else 100.0 / abs(odds)


def fmt_american(odds: float) -> str:
    v = int(round(odds))
    return f"+{v}" if v > 0 else str(v)


def cents_between(a: float, b: float) -> float:
    """Approximate 'cents' of price improvement from b to a."""
    return abs(a - b)


def row_key(r: dict) -> tuple:
    """Prefer game_pk; fall back to teams + start time (DH-aware)."""
    pk = (r.get("game_pk") or "").strip()
    d = (r.get("date") or "").strip()
    if pk:
        return (d, "pk", pk)
    return (d, "tm",
            (r.get("away_team") or "").upper().strip(),
            (r.get("home_team") or "").upper().strip(),
            (r.get("start_time_utc") or "").strip())


def merge(files: list[Path]) -> list[dict]:
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for path in files:
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                book = (r.get("sportsbook") or path.stem).strip() or path.stem
                r = dict(r)
                r["_book"] = book
                buckets[row_key(r)].append(r)

    out = []
    for key, rows in buckets.items():
        base = rows[0]
        merged = {
            "date": base.get("date", ""),
            "game_pk": base.get("game_pk", ""),
            "away_team": base.get("away_team", ""),
            "home_team": base.get("home_team", ""),
            "start_time_utc": base.get("start_time_utc", ""),
        }
        wins = []
        for side, col in (("nrfi", "market_nrfi_odds"), ("yrfi", "market_yrfi_odds")):
            priced = [(parse_american(r.get(col)), r["_book"]) for r in rows]
            priced = [(o, b) for o, b in priced if o is not None]
            if not priced:
                merged[col] = ""
                merged[f"best_{side}_book"] = ""
                merged[f"{side}_books_seen"] = "0"
                merged[f"{side}_cents_gained"] = ""
                continue
            best_o, best_b = max(priced, key=lambda t: payout(t[0]))
            worst_o, _ = min(priced, key=lambda t: payout(t[0]))
            merged[col] = fmt_american(best_o)
            merged[f"best_{side}_book"] = best_b
            merged[f"{side}_books_seen"] = str(len(priced))
            merged[f"{side}_cents_gained"] = (
                f"{cents_between(best_o, worst_o):.0f}" if len(priced) > 1 else "0")
            wins.append(best_b)
        merged["sportsbook"] = "/".join(dict.fromkeys(wins)) or base["_book"]
        out.append(merged)
    out.sort(key=lambda r: (r.get("date", ""), r.get("away_team", ""),
                            r.get("home_team", "")))
    return out


def self_test() -> int:
    """Exercises the comparison logic, especially the +/- boundary where
    naive numeric comparison of American odds silently picks the worse
    price."""
    print("=== payout ordering (the trap) ===")
    cases = [
        ("-110 vs -105", -110, -105, -105),
        ("+100 vs -105", 100, -105, 100),
        ("+120 vs +105", 120, 105, 120),
        ("-150 vs +100", -150, 100, 100),
        ("-115 vs -115", -115, -115, -115),
    ]
    ok = True
    for label, a, b, expected in cases:
        winner = a if payout(a) >= payout(b) else b
        good = winner == expected
        ok &= good
        naive = a if a >= b else b
        note = "" if naive == expected else f"   (naive numeric compare would pick {naive})"
        print(f"  [{'PASS' if good else 'FAIL'}] {label:<16} -> {fmt_american(winner)}{note}")

    print("\n=== end-to-end merge ===")
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    a = tmp / "bookA.csv"
    b = tmp / "bookB.csv"
    hdr = ["date", "game_pk", "away_team", "home_team",
           "market_nrfi_odds", "market_yrfi_odds", "sportsbook", "start_time_utc"]
    with open(a, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, hdr); w.writeheader()
        w.writerow(dict(date="2026-07-28", game_pk="1", away_team="NYY",
                        home_team="BOS", market_nrfi_odds="-130",
                        market_yrfi_odds="+110", sportsbook="BookA",
                        start_time_utc=""))
        w.writerow(dict(date="2026-07-28", game_pk="2", away_team="LAD",
                        home_team="SF", market_nrfi_odds="-105",
                        market_yrfi_odds="-115", sportsbook="BookA",
                        start_time_utc=""))
    with open(b, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, hdr); w.writeheader()
        w.writerow(dict(date="2026-07-28", game_pk="1", away_team="NYY",
                        home_team="BOS", market_nrfi_odds="-120",
                        market_yrfi_odds="+100", sportsbook="BookB",
                        start_time_utc=""))
        # game 2 missing from BookB entirely -> must still emit BookA's price
    rows = merge([a, b])
    exp = {
        "1": dict(nrfi="-120", nrfi_book="BookB", yrfi="+110", yrfi_book="BookA"),
        "2": dict(nrfi="-105", nrfi_book="BookA", yrfi="-115", yrfi_book="BookA"),
    }
    for r in rows:
        e = exp[r["game_pk"]]
        good = (r["market_nrfi_odds"] == e["nrfi"]
                and r["best_nrfi_book"] == e["nrfi_book"]
                and r["market_yrfi_odds"] == e["yrfi"]
                and r["best_yrfi_book"] == e["yrfi_book"])
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] game {r['game_pk']}: "
              f"NRFI {r['market_nrfi_odds']}({r['best_nrfi_book']}) "
              f"YRFI {r['market_yrfi_odds']}({r['best_yrfi_book']}) "
              f"books_seen {r['nrfi_books_seen']}/{r['yrfi_books_seen']}")
    print(f"  [{'PASS' if len(rows)==2 else 'FAIL'}] emitted {len(rows)} rows "
          f"(game present in only one book must survive)")
    ok &= len(rows) == 2
    print("\n" + ("ALL SELF-TESTS PASSED" if ok else "*** SELF-TEST FAILED ***"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.files:
        ap.error("give at least one per-book odds CSV, or --self-test")
    missing = [f for f in args.files if not f.exists()]
    if missing:
        sys.exit(f"missing: {', '.join(str(m) for m in missing)}")

    rows = merge(args.files)
    out = args.output or (ROOT / "data" / "odds" / "best_merged.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    multi = [r for r in rows if int(r.get("nrfi_books_seen") or 0) > 1
             or int(r.get("yrfi_books_seen") or 0) > 1]
    gains = [float(r["yrfi_cents_gained"]) for r in rows
             if r.get("yrfi_cents_gained") not in (None, "", "0")]
    print(f"merged {len(args.files)} book file(s) -> {len(rows)} games -> {out}")
    print(f"  games priced by >1 book: {len(multi)}")
    if gains:
        print(f"  YRFI price spread where multiple books quoted: "
              f"mean {sum(gains)/len(gains):.0f} cents, max {max(gains):.0f}")
    if len(args.files) == 1:
        print("  NOTE: only one book supplied -- this is a pass-through. Add a")
        print("        second source before expecting any gain.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
