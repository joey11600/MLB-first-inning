#!/usr/bin/env python3
"""
r2_specialist_null.py -- how surprising is the specialist's best money
cell, given how many cells were searched?

1. NULL: replace the specialist score with a RANDOM ranking of the same
   398 priced region rows.  Repeat 5000x.  For each draw, take the same
   8 slices and record the BEST slice edge (pp) and BEST slice units --
   exactly the selection rule a human applies when reading the table.
   Then ask what fraction of random draws beat the specialist's best.

2. TEMPORAL STABILITY: split the 2026 priced region by date into halves.
   Does the top-15% slice work in both?

3. Does anything clear the 5.65pp pricing wall?
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402

L2 = 10.0
TOPQ = 0.35
FRACS = (1.0, 0.75, 0.5, 0.35, 0.25, 0.15, 0.10, 0.05)


def slices(rows, score, fracs=FRACS):
    order = np.argsort(-np.asarray(score))
    out = []
    for f in fracs:
        k = max(1, int(round(len(rows) * f)))
        sel = [rows[i] for i in order[:k]]
        w = np.array([r["y_nrfi"] for r in sel], float)
        pay = np.array([C.payout(r["nrfi_odds"]) for r in sel])
        need = np.mean([C.implied(r["nrfi_odds"]) for r in sel])
        u = float(np.sum(np.where(w > 0, pay, -1.0)))
        out.append((f, k, w.mean(), need, w.mean() - need, u))
    return out


def main():
    r25 = C.attach_production(C.load_2025())
    r26 = C.attach_production(C.load_2026())

    X25, _ = C.design(r25)
    y25 = np.asarray([r["y_nrfi"] for r in r25], float)
    p25 = np.asarray([r["prod"] for r in r25], float)
    th = float(np.quantile(p25, 1.0 - TOPQ))
    spec = C.fit_lr(X25[p25 >= th], y25[p25 >= th], l2=L2)

    X26, _ = C.design(r26)
    p26 = np.asarray([r["prod"] for r in r26], float)
    th26 = float(np.quantile(p26, 1.0 - TOPQ))
    reg = [r for i, r in enumerate(r26)
           if p26[i] >= th26 and r["nrfi_odds"] is not None]
    Xreg = np.asarray([r for r in C.design(reg)[0]], float)
    s = C.predict_lr(spec, Xreg)
    print(f"priced region n={len(reg)}")

    obs = slices(reg, s)
    print("\nSPEC observed slices:")
    for f, k, hit, need, edge, u in obs:
        print(f"  {f*100:>5.0f}%  n={k:>4}  hit={100*hit:6.2f}  "
              f"need={100*need:6.2f}  edge={100*edge:+6.2f}pp  u={u:+7.2f}")
    best_edge = max(x[4] for x in obs)
    best_u = max(x[5] for x in obs)
    print(f"\nSPEC best-of-8 slice: edge {100*best_edge:+.2f}pp  "
          f"units {best_u:+.2f}")

    # ---- 1. NULL from random rankings ----
    rng = np.random.default_rng(3)
    B = 5000
    ne, nu = [], []
    for _ in range(B):
        rs = rng.random(len(reg))
        sl = slices(reg, rs)
        ne.append(max(x[4] for x in sl))
        nu.append(max(x[5] for x in sl))
    ne = np.asarray(ne)
    nu = np.asarray(nu)
    print(f"\nNULL (random ranking, best-of-8 slice, B={B}):")
    print(f"  edge pp   median {100*np.median(ne):+.2f}  "
          f"90th {100*np.quantile(ne,0.90):+.2f}  "
          f"95th {100*np.quantile(ne,0.95):+.2f}  "
          f"99th {100*np.quantile(ne,0.99):+.2f}")
    print(f"  units     median {np.median(nu):+.2f}  "
          f"90th {np.quantile(nu,0.90):+.2f}  "
          f"95th {np.quantile(nu,0.95):+.2f}  "
          f"99th {np.quantile(nu,0.99):+.2f}")
    print(f"  P(random best-of-8 edge  >= SPEC's {100*best_edge:+.2f}pp) = "
          f"{np.mean(ne >= best_edge):.3f}")
    print(f"  P(random best-of-8 units >= SPEC's {best_u:+.2f}u)   = "
          f"{np.mean(nu >= best_u):.3f}")

    # ---- 2. temporal stability of the winning slice ----
    print("\nTEMPORAL STABILITY of the SPEC top-15% slice:")
    order = np.argsort(-s)
    k = max(1, int(round(len(reg) * 0.15)))
    sel = [reg[i] for i in order[:k]]
    ds = sorted({r["date"] for r in reg})
    mid = ds[len(ds) // 2]
    for lbl, sub in (("first half", [r for r in sel if r["date"] < mid]),
                     ("second half", [r for r in sel if r["date"] >= mid])):
        if not sub:
            print(f"  {lbl}: empty")
            continue
        w = np.array([r["y_nrfi"] for r in sub], float)
        need = np.mean([C.implied(r["nrfi_odds"]) for r in sub])
        u = float(np.sum([C.payout(r["nrfi_odds"]) if r["y_nrfi"] else -1.0
                          for r in sub]))
        print(f"  {lbl}: n={len(sub)}  hit={100*w.mean():.2f}  "
              f"need={100*need:.2f}  edge={100*(w.mean()-need):+.2f}pp  "
              f"u={u:+.2f}")

    # ---- 3. wall check ----
    print("\nWALL CHECK -- does any slice clear +5.65pp of true edge?")
    for f, kk, hit, need, edge, u in obs:
        flag = "CLEARS" if edge >= 0.0565 else "no"
        print(f"  {f*100:>5.0f}%  edge {100*edge:+6.2f}pp  -> {flag}")


if __name__ == "__main__":
    main()
