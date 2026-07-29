"""
ANALYSIS ONLY -- replicate dashboard/lib/roi.ts loadRoi() season aggregation
in Python and check whether ZoneCard's three printed numbers (units, hit
rate, edge-vs-break-even) are computed over the same population.

No files are modified.
"""
import csv, math, sys
from collections import defaultdict

CSV = r"C:\Users\Pinellas Liquidation\MLB-first-inning\data\picks_2026.csv"

DEFAULT_WIN_PROFIT_UNITS = 100 / 110
DEFAULT_LOSS_UNITS = -1.0
DEFAULT_BREAK_EVEN_RATE = 110 / 210

START = "2026-01-01"
TODAY = "2026-07-28"

STRENGTHS = ["STRONG", "LEAN", "NO EDGE", "NO DATA", "STARTER PENDING",
             "LINEUP PENDING", "LOW LAMBDA"]


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    print(f"rows in csv: {len(rows)}")

    z = defaultdict(lambda: dict(picks=0, wins=0, losses=0, postponed=0,
                                 ungraded=0, unitsPL=0.0,
                                 realPricedBets=0, placeholderBets=0,
                                 realPricedPL=0.0))
    # extra diagnostics not in roi.ts
    bp_split = defaultdict(lambda: defaultdict(lambda: dict(w=0, l=0, pl=0.0, priced=0)))

    for r in rows:
        date = (r.get("date") or "")[:10]
        if not date or date < START or date > TODAY:
            continue
        side_raw = (r.get("pick_side") or "").upper()
        st_raw = (r.get("pick_strength") or "").upper()
        side = side_raw if side_raw in ("NRFI", "YRFI") else "PASS"
        strength = st_raw if st_raw in STRENGTHS else "NO EDGE"
        key = f"{side}|{strength}"
        b = z[key]
        b["picks"] += 1

        graded = (r.get("graded_result") or "").upper()
        if graded in ("WIN", "LOSS"):
            if graded == "WIN":
                b["wins"] += 1
            else:
                b["losses"] += 1

            if strength == "LEAN" and side in ("NRFI", "YRFI"):
                pl = DEFAULT_WIN_PROFIT_UNITS if graded == "WIN" else DEFAULT_LOSS_UNITS
            else:
                raw = (r.get("profit_loss_units") or "").strip()
                pl = None
                if raw:
                    try:
                        v = float(raw)
                        if math.isfinite(v):
                            pl = v
                    except ValueError:
                        pl = None
                if pl is None:
                    pl = DEFAULT_WIN_PROFIT_UNITS if graded == "WIN" else DEFAULT_LOSS_UNITS

            priced = False
            if strength != "LEAN":
                col = r.get("market_nrfi_odds") if side == "NRFI" else r.get("market_yrfi_odds")
                priced = bool((col or "").strip())
                if priced:
                    b["realPricedBets"] += 1
                    b["realPricedPL"] += pl
                else:
                    b["placeholderBets"] += 1
            b["unitsPL"] += pl

            bpv = (r.get("bet_placed") or "").strip()
            d = bp_split[key][bpv]
            d["w" if graded == "WIN" else "l"] += 1
            d["pl"] += pl
            d["priced"] += 1 if priced else 0
        elif graded in ("POSTPONED", "SUSPENDED"):
            b["postponed"] += 1
        elif graded == "PASS":
            pass
        else:
            b["ungraded"] += 1

    def realPL(b):
        known = b["realPricedBets"] + b["placeholderBets"]
        return b["realPricedPL"] if known > 0 else b["unitsPL"]

    print("\n=== ZONE CARDS (what RoiPanel ZoneCard prints) ===")
    hdr = f"{'zone':<14} {'W-L(all)':>9} {'bets':>5} {'unitsPL':>9} {'realPL':>9} {'realN':>6} {'plchN':>6} {'hit%':>7} {'edge pp':>8}"
    print(hdr)
    order = ["NRFI|STRONG", "NRFI|LEAN", "YRFI|LEAN", "YRFI|STRONG"]
    for k in order:
        if k not in z:
            continue
        b = z[k]
        bets = b["wins"] + b["losses"]
        hit = b["wins"] / bets if bets else float("nan")
        edge = hit - DEFAULT_BREAK_EVEN_RATE if bets else float("nan")
        print(f"{k:<14} {b['wins']:>4}-{b['losses']:<4} {bets:>5} "
              f"{b['unitsPL']:>9.2f} {realPL(b):>9.2f} "
              f"{b['realPricedBets']:>6} {b['placeholderBets']:>6} "
              f"{100*hit:>6.1f}% {100*edge:>+7.1f}")

    # Hit rate / edge recomputed over the REAL-PRICED subset only, for
    # comparison -- this is the population the units figure describes.
    print("\n=== TOTAL (STRONG only) -- LegacyLedgerLine ===")
    tot = dict(picks=0, wins=0, losses=0, unitsPL=0.0,
               realPricedBets=0, placeholderBets=0, realPricedPL=0.0)
    for k, b in z.items():
        s, st = k.split("|")
        if s == "PASS" or st != "STRONG":
            continue
        for f in tot:
            tot[f] += b[f]
    bets = tot["wins"] + tot["losses"]
    print(f"bets={bets} W-L={tot['wins']}-{tot['losses']} "
          f"unitsPL={tot['unitsPL']:.2f} realPricedPL={tot['realPricedPL']:.2f} "
          f"realN={tot['realPricedBets']} placeholderN={tot['placeholderBets']}")
    print(f"LegacyLedgerLine prints: {realPL(tot):+.2f}u over {bets} settled "
          f"bets ({tot['wins']}-{tot['losses']})")

    print("\n=== bet_placed split within STRONG zones ===")
    for k in ("NRFI|STRONG", "YRFI|STRONG"):
        if k not in bp_split:
            continue
        print(f"  {k}")
        for bpv, d in sorted(bp_split[k].items()):
            print(f"    bet_placed={bpv!r:>5}: {d['w']}W-{d['l']}L  "
                  f"pl={d['pl']:+.2f}u  priced={d['priced']}")


if __name__ == "__main__":
    main()
