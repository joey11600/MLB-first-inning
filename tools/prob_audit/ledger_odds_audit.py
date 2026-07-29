"""ANALYSIS ONLY -- recompute stored odds-derived columns from raw odds."""
import csv, sys, statistics
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import tracker

rows = list(csv.DictReader(open(ROOT / "data" / "picks_2026.csv", encoding="utf-8")))
ap, ppu = tracker.american_to_prob, tracker.payout_per_unit

def F(r, k):
    v = (r.get(k) or "").strip()
    if not v: return None
    try: return float(v)
    except ValueError: return None

# ---- 3. implied probability columns ---------------------------------------
errs = {"implied_nrfi_prob": [], "implied_yrfi_prob": []}
missing = {"implied_nrfi_prob": 0, "implied_yrfi_prob": 0}
extra   = {"implied_nrfi_prob": 0, "implied_yrfi_prob": 0}
for r in rows:
    for col, ocol in (("implied_nrfi_prob", "market_nrfi_odds"),
                      ("implied_yrfi_prob", "market_yrfi_odds")):
        want = ap(r.get(ocol, ""))
        got  = F(r, col)
        if want is None and got is None: continue
        if want is not None and got is None: missing[col] += 1; continue
        if want is None and got is not None: extra[col] += 1; continue
        errs[col].append(abs(want - got))
print("=" * 78); print("3. STORED implied_*_prob vs recomputed from market_*_odds"); print("=" * 78)
for col in errs:
    e = errs[col]
    print(f"  {col}: n={len(e)}  max|err|={max(e) if e else 0:.2e}  "
          f"mean|err|={statistics.fmean(e) if e else 0:.2e}  "
          f"odds-but-no-stored={missing[col]}  stored-but-no-odds={extra[col]}")

# ---- 4. edge columns -------------------------------------------------------
print(); print("=" * 78); print("4. STORED edge_* vs recomputed (model prob - RAW implied)"); print("=" * 78)
for col, mcol, ocol in (("edge_nrfi", "nrfi_prob", "market_nrfi_odds"),
                        ("edge_yrfi", "yrfi_prob", "market_yrfi_odds")):
    e, miss = [], 0
    for r in rows:
        imp = ap(r.get(ocol, "")); m = F(r, mcol); got = F(r, col)
        if imp is None or m is None:
            continue
        if got is None: miss += 1; continue
        e.append(abs((m - imp) - got))
    print(f"  {col}: n={len(e)}  max|err|={max(e) if e else 0:.2e}  "
          f"mean|err|={statistics.fmean(e) if e else 0:.2e}  computable-but-blank={miss}")

# edge_on_pick consistency
bad_pick, n_pick = [], 0
for r in rows:
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI", "YRFI"): continue
    src = F(r, "edge_nrfi" if side == "NRFI" else "edge_yrfi")
    got = F(r, "edge_on_pick")
    if src is None and got is None: continue
    n_pick += 1
    if src is None or got is None or abs(src - got) > 1e-9:
        bad_pick.append((r["date"], r["away_team"], r["home_team"], side, src, got))
print(f"  edge_on_pick == edge_<picked side>: n={n_pick}  mismatches={len(bad_pick)}")
for b in bad_pick[:5]: print("    ", b)

# model probs sum to 1?
s = [abs((F(r,'nrfi_prob') or 0) + (F(r,'yrfi_prob') or 0) - 1.0)
     for r in rows if F(r,'nrfi_prob') is not None and F(r,'yrfi_prob') is not None]
print(f"  |nrfi_prob + yrfi_prob - 1|: n={len(s)} max={max(s):.2e}")
oob = [r['date'] for r in rows if (F(r,'nrfi_prob') is not None and not (0 < F(r,'nrfi_prob') < 1))]
print(f"  nrfi_prob outside (0,1): {len(oob)}")

# ---- 5. THE VIG -----------------------------------------------------------
print(); print("=" * 78); print("5. VIG / OVERROUND on the two-sided market"); print("=" * 78)
ov, ov_bet = [], []
for r in rows:
    a, b = ap(r.get("market_nrfi_odds","")), ap(r.get("market_yrfi_odds",""))
    if a is None or b is None: continue
    ov.append(a + b)
    if (r.get("bet_placed") or "").strip().upper() == "Y": ov_bet.append(a + b)
print(f"  all two-sided rows n={len(ov)}  mean sum={statistics.fmean(ov):.5f}  "
      f"median={statistics.median(ov):.5f}  min={min(ov):.5f}  max={max(ov):.5f}")
print(f"  bet_placed=Y rows  n={len(ov_bet)} mean sum={statistics.fmean(ov_bet):.5f}  "
      f"median={statistics.median(ov_bet):.5f}")
print(f"  => mean take = {(statistics.fmean(ov)-1)*100:.3f}%  (bet rows {(statistics.fmean(ov_bet)-1)*100:.3f}%)")

# de-vigged edge vs stored raw edge, on the picked side, bet rows
print()
deltas, fairs = [], []
for r in rows:
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    a, b = ap(r.get("market_nrfi_odds","")), ap(r.get("market_yrfi_odds",""))
    if a is None or b is None: continue
    tot = a + b
    imp_pick = a if side == "NRFI" else b
    fair = imp_pick / tot                     # proportional (multiplicative) de-vig
    m = F(r, "nrfi_prob" if side=="NRFI" else "yrfi_prob")
    if m is None: continue
    raw_edge  = m - imp_pick
    fair_edge = m - fair
    deltas.append(fair_edge - raw_edge)
    fairs.append((r, raw_edge, fair_edge))
print(f"  de-vig would ADD to edge_on_pick: n={len(deltas)}  mean=+{statistics.fmean(deltas)*100:.3f}pp  "
      f"median=+{statistics.median(deltas)*100:.3f}pp  min=+{min(deltas)*100:.3f}pp  max=+{max(deltas)*100:.3f}pp")
neg_to_pos = sum(1 for _, rw, fe in fairs if rw < 0 <= fe)
print(f"  rows whose edge flips NEGATIVE -> NON-NEGATIVE under de-vig: {neg_to_pos} / {len(fairs)}")

# ---- 6. P&L ---------------------------------------------------------------
print(); print("=" * 78); print("6. profit_loss_units vs tracker._calc_pnl"); print("=" * 78)
drift, n, fb = [], 0, 0
for r in rows:
    want = tracker._calc_pnl(r)
    got  = (r.get("profit_loss_units") or "").strip()
    if want == "" and got == "": continue
    n += 1
    if want != got:
        drift.append((r["date"], r["away_team"], r["home_team"],
                      r.get("pick_side"), r.get("graded_result"),
                      r.get("bet_placed"), r.get("units_risked"),
                      r.get("market_nrfi_odds"), r.get("market_yrfi_odds"),
                      "stored=" + got, "recomp=" + want))
    # count -110-fallback wins
    if (r.get("graded_result")=="WIN" and (r.get("bet_placed") or "").upper()=="Y"):
        side=(r.get("pick_side") or "").upper()
        col="market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
        if ppu(r.get(col,"")) is None: fb += 1
print(f"  rows compared={n}  DRIFT={len(drift)}")
for d in drift[:12]: print("   ", d)
print(f"  WIN rows priced by the -110 FALLBACK (no captured picked-side price): {fb}")

# ---- 7. Kelly -------------------------------------------------------------
print(); print("=" * 78); print("7. KELLY"); print("=" * 78)
for p, o in [(0.60,"-110"),(0.60,"+110"),(0.50,"-110"),(0.5238,"-110"),
             (0.40,"+150"),(0.40,"-150"),(0.65,"-200"),(0.01,"+100"),(0.0,"-110"),(1.0,"-110")]:
    f = tracker.kelly_fraction_of_bankroll(p, o)
    b = ppu(o)
    ref = None if b is None else max((p*b-(1-p))/b, 0.0)
    print(f"  p={p:<7} odds={o:<6} f*={f}   textbook={ref}   "
          f"{'OK' if (f is None and ref is None) or (f is not None and abs(f-ref)<1e-12) else '**MISMATCH**'}")
print(f"  KELLY_ENABLED={tracker.KELLY_ENABLED} FRACTION={tracker.KELLY_FRACTION} "
      f"MAX_STAKE={tracker.KELLY_MAX_STAKE_FRAC} MAX_DAILY={tracker.KELLY_MAX_DAILY_FRAC} "
      f"MIN_STAKE={tracker.KELLY_MIN_STAKE_UNITS} BANKROLL={tracker.KELLY_BANKROLL_UNITS} "
      f"EPOCH={tracker.KELLY_BANKROLL_EPOCH}")

# breakeven: Kelly's implicit gate is p > raw implied.  What would de-vig do?
print()
print("  Kelly's zero-stake gate is p_model > RAW implied (vig included).")
print("  On a -110/-110 market that gate is p>0.5238; the fair (de-vigged) prob is 0.5000.")

# ---- 8. clv_pct -----------------------------------------------------------
print(); print("=" * 78); print("8. clv_pct"); print("=" * 78)
e, same = [], 0
for r in rows:
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    oc = "opened_nrfi_odds" if side=="NRFI" else "opened_yrfi_odds"
    mc = "market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"
    o_, m_ = ap(r.get(oc,"")), ap(r.get(mc,""))
    got = F(r,"clv_pct")
    if o_ is None or m_ is None or got is None: continue
    e.append(abs((m_-o_) - got))
    if abs(m_-o_) < 1e-12: same += 1
print(f"  n={len(e)} max|err|={max(e) if e else 0:.2e}  rows where open==close: {same}")
