import csv, statistics as st

def a2p(s):
    if s is None: return None
    s = str(s).strip().replace(" ", "")
    if not s: return None
    try: o = float(s)
    except ValueError: return None
    if o == 0: return None
    return 100.0/(o+100.0) if o > 0 else abs(o)/(abs(o)+100.0)

def F(v):
    v = (v or "").strip()
    try: return float(v)
    except ValueError: return None

rows = list(csv.DictReader(open("data/picks_2026.csv", encoding="utf-8")))
print(f"total rows: {len(rows)}")

# ---------- 1. OVERROUND on every row carrying BOTH prices ----------
S = []
for r in rows:
    n, y = a2p(r.get("market_nrfi_odds")), a2p(r.get("market_yrfi_odds"))
    if n is not None and y is not None:
        S.append(n+y)
print("\n[1] OVERROUND (implied_nrfi + implied_yrfi), both-priced rows")
print(f"    n={len(S)}  mean={st.mean(S):.5f}  median={st.median(S):.5f} "
      f"min={min(S):.5f} max={max(S):.5f}")
print(f"    -> book take = {100*(st.mean(S)-1):.3f}%")
print(f"    rows with S < 1.0 (would break devig): {sum(1 for s in S if s <= 1.0)}")

# same, restricted to bet_placed=Y
SY = []
for r in rows:
    if (r.get("bet_placed") or "").strip().upper() != "Y": continue
    n, y = a2p(r.get("market_nrfi_odds")), a2p(r.get("market_yrfi_odds"))
    if n is not None and y is not None: SY.append(n+y)
print(f"    bet_placed=Y subset: n={len(SY)} mean={st.mean(SY):.5f}")

# ---------- 2. Reproduce the STORED edge exactly (is it raw-implied?) --------
devs = []
for r in rows:
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    stored = F(r.get("edge_on_pick"))
    if stored is None: continue
    p = F(r.get("nrfi_prob" if side=="NRFI" else "yrfi_prob"))
    imp = a2p(r.get("market_nrfi_odds" if side=="NRFI" else "market_yrfi_odds"))
    if p is None or imp is None: continue
    devs.append(abs(stored - (p - imp)))
print(f"\n[2] stored edge_on_pick == model_p - RAW implied?")
print(f"    n={len(devs)} max|dev|={max(devs):.6f} mean|dev|={st.mean(devs):.8f}")
print(f"    rows deviating > 0.0001: {sum(1 for d in devs if d > 1e-4)}")

# ---------- 3. Placed bets: raw vs de-vigged picked-side edge ----------
raw, prop, add = [], [], []
neg_raw = neg_prop = neg_add = 0
placed_two_sided = 0
for r in rows:
    if (r.get("bet_placed") or "").strip().upper() != "Y": continue
    side = (r.get("pick_side") or "").strip().upper()
    if side not in ("NRFI","YRFI"): continue
    n, y = a2p(r.get("market_nrfi_odds")), a2p(r.get("market_yrfi_odds"))
    if n is None or y is None: continue
    p = F(r.get("nrfi_prob" if side=="NRFI" else "yrfi_prob"))
    if p is None: continue
    placed_two_sided += 1
    s = n + y
    imp   = n if side=="NRFI" else y
    fairP = imp / s                    # proportional / multiplicative devig
    fairA = imp - (s - 1.0)/2.0        # additive / shift devig
    raw.append(p - imp); prop.append(p - fairP); add.append(p - fairA)
    neg_raw  += (p - imp)   < 0
    neg_prop += (p - fairP) < 0
    neg_add  += (p - fairA) < 0

print(f"\n[3] PLACED bets with a two-sided price: n={placed_two_sided}")
print(f"    mean edge  RAW           = {100*st.mean(raw):+.3f} pp   negative: {neg_raw}/{placed_two_sided}")
print(f"    mean edge  PROPORTIONAL  = {100*st.mean(prop):+.3f} pp   negative: {neg_prop}/{placed_two_sided}  (shift {100*(st.mean(prop)-st.mean(raw)):+.3f} pp)")
print(f"    mean edge  ADDITIVE      = {100*st.mean(add):+.3f} pp   negative: {neg_add}/{placed_two_sided}  (shift {100*(st.mean(add)-st.mean(raw)):+.3f} pp)")
