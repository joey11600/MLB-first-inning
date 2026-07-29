"""Read-only: replay kelly_stake_units against the REAL ledger rows and
diff against the stored units_risked.  Also tests the side-correctness of
the probability that gets passed in.  Modifies nothing."""
import csv
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import tracker  # noqa: E402

PATH = os.path.join("data", "picks_2026.csv")
rows = list(csv.DictReader(open(PATH, newline="", encoding="utf-8")))
print(f"ledger rows: {len(rows)}")

SEP = "=" * 78


def f(x):
    try:
        return float(str(x).strip())
    except Exception:
        return None


# ---------------------------------------------------------------- 1
print(SEP)
print(" A. nrfi_prob + yrfi_prob == 1 on every row?")
print(SEP)
bad = []
worst = 0.0
n = 0
for r in rows:
    a, b = f(r["nrfi_prob"]), f(r["yrfi_prob"])
    if a is None or b is None:
        continue
    n += 1
    d = abs(a + b - 1.0)
    worst = max(worst, d)
    if d > 5e-4:
        bad.append((r["date"], r["away_team"], r["home_team"], a, b, d))
print(f"   checked {n} rows; max |p_nrfi + p_yrfi - 1| = {worst:.3e}; violations>5e-4: {len(bad)}")
for x in bad[:10]:
    print("    ", x)

# ---------------------------------------------------------------- 2
print()
print(SEP)
print(" B. Which probability does the KELLY-ERA stake reproduce -- picked side or opposite?")
print(SEP)
epoch = tracker.KELLY_BANKROLL_EPOCH
kelly_rows = [r for r in rows
              if (r.get("date") or "") >= epoch
              and (r.get("pick_strength") or "").strip().upper() == "STRONG"
              and (r.get("units_risked") or "").strip()]
print(f"   STRONG rows dated >= {epoch} with a stake: {len(kelly_rows)}")

# Also look at every STRONG row with a stake != 1.00 / 0.50 (i.e. clearly Kelly-sized)
cand = [r for r in rows
        if (r.get("pick_strength") or "").strip().upper() == "STRONG"
        and f(r.get("units_risked")) not in (None, 1.0, 0.5, 0.0)]
print(f"   STRONG rows anywhere with a non-flat stake: {len(cand)}")

byday = defaultdict(list)
for r in cand:
    byday[r["date"]].append(r)

hit_right = hit_wrong = miss = 0
examples = []
for d in sorted(byday):
    # replay the day the way import_odds would: fresh tally, bank as-of
    tracker.kelly_reset_daily_committed()
    tracker._bankroll_cache = tracker.KELLY_BANKROLL_UNITS
    tracker._daily_committed[d] = 0.0
    for r in byday[d]:
        side = (r.get("pick_side") or "").strip().upper()
        stored = f(r.get("units_risked"))
        p_right = f(r["nrfi_prob"] if side == "NRFI" else r["yrfi_prob"])
        p_wrong = f(r["yrfi_prob"] if side == "NRFI" else r["nrfi_prob"])
        o_right = r["market_nrfi_odds"] if side == "NRFI" else r["market_yrfi_odds"]
        o_wrong = r["market_yrfi_odds"] if side == "NRFI" else r["market_nrfi_odds"]
        # no daily cap here -- we only want the per-bet number
        tracker.kelly_reset_daily_committed()
        tracker._bankroll_cache = tracker.KELLY_BANKROLL_UNITS
        s_right = tracker.kelly_stake_units(p_right, o_right)
        s_wrong = tracker.kelly_stake_units(p_wrong, o_wrong)
        s_mix = tracker.kelly_stake_units(p_wrong, o_right)
        ok_r = s_right is not None and abs(s_right - stored) < 0.011
        ok_w = s_wrong is not None and abs(s_wrong - stored) < 0.011
        if ok_r:
            hit_right += 1
        elif ok_w:
            hit_wrong += 1
        else:
            miss += 1
        if len(examples) < 14:
            examples.append((r["date"], f"{r['away_team']}@{r['home_team']}", side,
                             p_right, o_right, stored, s_right, s_wrong, s_mix))

print(f"   stored stake == Kelly(p_PICKED_SIDE, odds_PICKED_SIDE) : {hit_right}")
print(f"   stored stake == Kelly(p_OPPOSITE,    odds_OPPOSITE)    : {hit_wrong}")
print(f"   neither (daily-cap trim / stale bank / flat)           : {miss}")
print()
print(f"   {'date':<11}{'game':<10}{'side':<6}{'p_side':>8}{'odds':>7}"
      f"{'stored':>8}{'K(right)':>10}{'K(wrong)':>10}{'K(mixed)':>10}")
for e in examples:
    print(f"   {e[0]:<11}{e[1]:<10}{e[2]:<6}{e[3]:>8.4f}{str(e[4]):>7}"
          f"{e[5]:>8.2f}{str(e[6]):>10}{str(e[7]):>10}{str(e[8]):>10}")

# ---------------------------------------------------------------- 3
print()
print(SEP)
print(" C. current_bankroll_units() recomputed by hand")
print(SEP)
tracker.kelly_reset_daily_committed()
code_bank = tracker.current_bankroll_units()
realized = 0.0
counted = skipped_noprice = skipped_pre = 0
for r in rows:
    if (r.get("bet_placed") or "").strip().upper() != "Y":
        continue
    if (r.get("date") or "") < epoch:
        skipped_pre += 1
        continue
    side = (r.get("pick_side") or "").strip().upper()
    col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    if not (r.get(col) or "").strip():
        skipped_noprice += 1
        continue
    v = f(r.get("profit_loss_units"))
    if v is None:
        continue
    realized += v
    counted += 1
print(f"   nominal              = {tracker.KELLY_BANKROLL_UNITS}")
print(f"   post-epoch settled   = {counted} rows, realized {realized:+.4f}u")
print(f"   skipped (pre-epoch)  = {skipped_pre}")
print(f"   skipped (no price)   = {skipped_noprice}")
print(f"   hand bank            = {max(tracker.KELLY_BANKROLL_UNITS + realized, 1.0):.4f}")
print(f"   code bank            = {code_bank:.4f}")

# What the bankroll WOULD be without the epoch guard (the bug it prevents)
allpl = sum(f(r.get("profit_loss_units")) or 0.0
            for r in rows if (r.get("bet_placed") or "").strip().upper() == "Y")
print(f"   (no-epoch would be   = {100 + allpl:.2f}u  -> stakes {100*(100+allpl)/100 - 100:+.0f}% off)")

# ---------------------------------------------------------------- 4
print()
print(SEP)
print(" D. _committed_on(): does the seed double-count or miss?")
print(SEP)
for d in sorted({r["date"] for r in rows})[-6:]:
    tracker.kelly_reset_daily_committed()
    code_c = tracker._committed_on(d)
    hand_y = sum(f(r.get("units_risked")) or 0.0 for r in rows
                 if r["date"] == d
                 and (r.get("pick_strength") or "").strip().upper() == "STRONG"
                 and (r.get("bet_placed") or "").strip().upper() == "Y")
    hand_all = sum(f(r.get("units_risked")) or 0.0 for r in rows
                   if r["date"] == d
                   and (r.get("pick_strength") or "").strip().upper() == "STRONG")
    print(f"   {d}: code={code_c:6.2f}  hand(Y only)={hand_y:6.2f}  "
          f"hand(all STRONG)={hand_all:6.2f}")

# ---------------------------------------------------------------- 5
print()
print(SEP)
print(" E. STAKE-CHIP PARITY: dashboard uses nrfi_pct (1 decimal) not nrfi_prob")
print(SEP)
board = os.path.join("data", "board_latest.csv")
if os.path.exists(board):
    b = list(csv.DictReader(open(board, newline="", encoding="utf-8")))
    print(f"   board rows: {len(b)}; cols include nrfi_pct? {'nrfi_pct' in (b[0] if b else {})}")
    for r in b[:6]:
        print(f"   {r.get('away')}@{r.get('home'):<5} nrfi_pct={r.get('nrfi_pct')} "
              f"yrfi_pct={r.get('yrfi_pct')} sum={f(r.get('nrfi_pct'))+f(r.get('yrfi_pct')):.1f}")
else:
    print("   data/board_latest.csv not present")

# quantify the 1-dp truncation cost on a stake
print()
print("   worst-case stake error from 1-dp percent rounding (bank=100, quarter K):")
for odds in (-140, -110, 120):
    bb = tracker.payout_per_unit(str(odds))
    dfdp = 1 + 1 / bb
    print(f"     {odds:>5}: d(stake)/d(p) = 100*0.25*(1+1/b) = {100*0.25*dfdp:.3f}u per 1.0 prob;"
          f"  +-0.0005 prob -> +-{100*0.25*dfdp*0.0005:.4f}u")
