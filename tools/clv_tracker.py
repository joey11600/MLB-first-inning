#!/usr/bin/env python3
"""tools/clv_tracker.py -- are we capturing good YRFI prices?

The system bets at the FIRST scraped DK price and locks it, and closing-
line capture is intentionally off (T4.28), so true bet-vs-close CLV does
not exist here (clv_pct is always 0, open==bet). The meaningful proxy is
EDGE AT BET: our model's YRFI prob vs the book's de-vigged YRFI price.

    edge = model_yrfi - market_devig_yrfi
         = yrfi_prob - imp(YRFI)/(imp(NRFI)+imp(YRFI))

This answers two things that actually move money:
  1. Is the YRFI edge REAL? -> bucket bets by edge; do higher-edge bets
     win more (does realized P&L track the edge we thought we had)?
  2. Are we OVERPAYING on some bets? -> negative-edge bets, and a price-
     quality breakdown (plus-money vs juice), are the leak.

Read-only. Usage: python tools/clv_tracker.py [--side YRFI|NRFI] [--since YYYY-MM-DD]
"""
from __future__ import annotations
import argparse, csv, sys
from pathlib import Path
try:
    import numpy as np
except ImportError:
    sys.exit("Install numpy")
ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "data" / "picks_2026.csv"
SEED, B = 20260625, 10000


def imp(a):
    s = (a or "").strip()
    try: n = int(float(s))
    except (ValueError, TypeError): return None
    return (abs(n) / (abs(n) + 100)) if n < 0 else (100 / (n + 100))


def boot_ci(pls):
    if len(pls) < 8: return (float("nan"), float("nan"))
    rng = np.random.default_rng(SEED)
    arr = np.asarray(pls)
    m = rng.choice(arr, size=(B, len(arr)), replace=True).mean(axis=1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def load(side, since):
    rows = []
    with open(PICKS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if (r.get("pick_side") or "") != side: continue
            if (r.get("pick_strength") or "").upper() != "STRONG": continue
            if (r.get("bet_placed") or "").upper() != "Y": continue
            if (r.get("graded_result") or "").upper() not in ("WIN", "LOSS"): continue
            if (r.get("date") or "") < since: continue
            iN, iY = imp(r.get("market_nrfi_odds")), imp(r.get("market_yrfi_odds"))
            if not iN or not iY: continue
            mine = imp(r.get("market_yrfi_odds")) if side == "YRFI" else imp(r.get("market_nrfi_odds"))
            try: model = float(r["yrfi_prob"]) if side == "YRFI" else float(r["nrfi_prob"])
            except (ValueError, KeyError): continue
            try: booked = float(r.get("profit_loss_units") or "nan")
            except ValueError: continue
            if booked != booked: continue
            devig = (iY / (iN + iY)) if side == "YRFI" else (iN / (iN + iY))
            try: price = int(float((r.get("market_" + side.lower() + "_odds") or "").strip()))
            except (ValueError, TypeError): price = None
            won = r["graded_result"].upper() == "WIN"
            # Flat-1u settlement, the basis edge_reality_check.py and
            # market_signal_check.py both use.  payout == (1-imp)/imp
            # exactly, so this needs no second parse of the price string.
            pl_flat = ((1.0 - mine) / mine) if won else -1.0
            rows.append({"edge": model - devig, "won": won,
                         "pl_flat": pl_flat, "booked": booked,
                         "price": price, "vig_implied": mine})
    return rows


def show(rows, label, ci=False):
    """ROI and its CI are reported on a FLAT 1u basis; booked P&L is carried
    beside them, labelled, on the quarter-Kelly stakes actually placed.

    This used to be `ROI = sum(profit_loss_units) / n` -- a quarter-Kelly
    numerator over a flat-1u denominator.  That was correct while every bet
    was a flat single unit, but quarter-Kelly went live 2026-07-27 with
    stakes of 3-9u, after which the figure overstated return by roughly the
    average stake: the 2026-08-13 review saw it print "+259.2%" and
    "+375.7%".  market_signal_check.py never had the bug because it builds a
    flat-1u settlement first; clv_tracker now does the same, so the two
    tools' ROI figures are comparable to each other and to
    edge_reality_check.py.
    """
    if not rows:
        print(f"  {label:<24} (0)"); return
    n = len(rows); w = sum(x["won"] for x in rows)
    flat = sum(x["pl_flat"] for x in rows); booked = sum(x["booked"] for x in rows)
    s = (f"  {label:<24} n={n:>3}  {w}-{n-w} ({w/n*100:>3.0f}%)  "
         f"ROI {flat/n*100:>+6.1f}%  flat {flat:>+6.1f}u  booked {booked:>+7.1f}u")
    if ci:
        lo, hi = boot_ci([x["pl_flat"] for x in rows])
        s += f"  95%CI[{lo*100:+.0f}%,{hi*100:+.0f}%]{'  REAL' if lo>0 else ''}"
    print(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", default="YRFI", choices=["YRFI", "NRFI"])
    ap.add_argument("--since", default="2026-01-01")
    args = ap.parse_args()
    rows = load(args.side, args.since)
    if not rows:
        print("no rows"); return
    avg_edge = sum(x["edge"] for x in rows) / len(rows)
    pos = sum(1 for x in rows if x["edge"] > 0)
    print(f"{args.side} STRONG bets (real odds) since {args.since}: {len(rows)}")
    print(f"avg edge at bet (model - book devig): {avg_edge*100:+.1f}pp   "
          f"positive-edge bets: {pos}/{len(rows)} ({pos/len(rows)*100:.0f}%)")
    print("ROI, 'flat' and the CI are a FLAT 1u basis -- the model's edge, "
          "independent of sizing.")
    print("'booked' is the real money the ledger settled at the "
          "quarter-Kelly stakes actually placed.\n")

    print("=== 1. Is the edge REAL? bucket by edge-at-bet -> realized result ===")
    show([x for x in rows if x["edge"] < 0.0], "negative edge (overpaid)", ci=True)
    show([x for x in rows if 0.0 <= x["edge"] < 0.05], "thin edge 0-5pp")
    show([x for x in rows if 0.05 <= x["edge"] < 0.10], "good edge 5-10pp", ci=True)
    show([x for x in rows if x["edge"] >= 0.10], "big edge 10pp+", ci=True)
    print("  (if higher-edge buckets win more, the pricing edge is real &")
    print("   negative-edge bets are a skippable leak.)")

    print("\n=== 2. Are we OVERPAYING? price-quality breakdown ===")
    show([x for x in rows if x["price"] is not None and x["price"] > 0], "plus money (+)")
    show([x for x in rows if x["price"] is not None and -120 <= x["price"] <= -100], "light juice -100..-120")
    show([x for x in rows if x["price"] is not None and -150 <= x["price"] < -120], "heavy juice -121..-150")
    show([x for x in rows if x["price"] is not None and x["price"] < -150], "steep juice -150+")
    print("  (heavy-juice buckets needing 57-60% are where a thin edge dies.)")


if __name__ == "__main__":
    main()
