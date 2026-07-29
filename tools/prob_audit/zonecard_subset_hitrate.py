"""ANALYSIS ONLY. What the ZoneCard *would* say if hit rate / edge were
computed over the same population as the units figure (real-priced bets)."""
import csv, math
from collections import defaultdict

CSV = r"C:\Users\Pinellas Liquidation\MLB-first-inning\data\picks_2026.csv"
BE = 110 / 210
WIN, LOSS = 100 / 110, -1.0
START, TODAY = "2026-01-01", "2026-07-28"

rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
agg = defaultdict(lambda: dict(aw=0, al=0, apl=0.0, rw=0, rl=0, rpl=0.0,
                               pw=0, pl_=0, ppl=0.0))

for r in rows:
    d = (r.get("date") or "")[:10]
    if not d or d < START or d > TODAY:
        continue
    side = (r.get("pick_side") or "").upper()
    st = (r.get("pick_strength") or "").upper()
    if side not in ("NRFI", "YRFI") or st != "STRONG":
        continue
    g = (r.get("graded_result") or "").upper()
    if g not in ("WIN", "LOSS"):
        continue
    raw = (r.get("profit_loss_units") or "").strip()
    pl = None
    if raw:
        try:
            v = float(raw)
            if math.isfinite(v):
                pl = v
        except ValueError:
            pass
    if pl is None:
        pl = WIN if g == "WIN" else LOSS
    col = r.get("market_nrfi_odds") if side == "NRFI" else r.get("market_yrfi_odds")
    priced = bool((col or "").strip())

    b = agg[f"STRONG {side}"]
    b["apl"] += pl
    b["aw" if g == "WIN" else "al"] += 1
    if priced:
        b["rpl"] += pl
        b["rw" if g == "WIN" else "rl"] += 1
    else:
        b["ppl"] += pl
        b["pw" if g == "WIN" else "pl_"] += 1

tot = dict(aw=0, al=0, apl=0.0, rw=0, rl=0, rpl=0.0, pw=0, pl_=0, ppl=0.0)
for k in list(agg):
    for f in tot:
        tot[f] += agg[k][f]
agg["TOTAL (STRONG)"] = tot

for k, b in agg.items():
    ab, rb, pb = b["aw"] + b["al"], b["rw"] + b["rl"], b["pw"] + b["pl_"]
    ah = b["aw"] / ab if ab else float("nan")
    rh = b["rw"] / rb if rb else float("nan")
    ph = b["pw"] / pb if pb else float("nan")
    print(f"\n--- {k} ---")
    print(f"  ALL GRADED   n={ab:<4} {b['aw']}-{b['al']}  hit={100*ah:5.1f}%  "
          f"edge={100*(ah-BE):+5.1f}pp  pl={b['apl']:+8.4f}u")
    print(f"  REAL-PRICED  n={rb:<4} {b['rw']}-{b['rl']}  hit={100*rh:5.1f}%  "
          f"edge={100*(rh-BE):+5.1f}pp  pl={b['rpl']:+8.4f}u   <-- units figure")
    print(f"  PLACEHOLDER  n={pb:<4} {b['pw']}-{b['pl_']}  hit={100*ph:5.1f}%  "
          f"edge={100*(ph-BE):+5.1f}pp  pl={b['ppl']:+8.4f}u")
    print(f"  CARD PRINTS: {b['rpl']:+.2f}u  |  {100*ah:.1f}% hit  |  "
          f"{100*(ah-BE):+.1f}pp vs break-even   [units from n={rb}, "
          f"hit/edge from n={ab}]")
