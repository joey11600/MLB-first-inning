#!/usr/bin/env python3
"""Same top-slice ladder as alt_08 panel C, but on 2025 (no odds) and on the
honest out-of-fold 2026 refit.  If the NRFI-side ranking failure is structural
it should appear in 2025 too.  If 2025's NRFI ladder rises normally, the
failure is a 2026 model/market condition, not an architecture defect.

Reference line: DK's typical NRFI break-even in 2026 was 0.537 average and
~0.577 for the games the model likes most, so 0.537-0.577 is the bar.
Read-only."""
from __future__ import annotations
import numpy as np
import alt_common as ac

BAR_MEAN, BAR_TOP = 0.5369, 0.5772


def ladder(tag, p, y, dates, side):
    """side='nrfi' ranks by p; side='yrfi' ranks by 1-p against 1-y."""
    if side == "yrfi":
        p, y = 1 - p, 1 - y
    print(f"  {tag}  ({side.upper()} side)")
    print(f"    {'slice':<12}{'n':>6}{'min p':>9}{'realised':>11}"
          f"{'vs 0.537':>11}{'vs 0.577':>11}{'95% CI on realised':>24}")
    for frac in (0.50, 0.30, 0.20, 0.10, 0.05, 0.02):
        thr = np.quantile(p, 1 - frac)
        s = np.where(p >= thr)[0]
        if len(s) < 20:
            continue
        r = y[s].mean()
        rng = np.random.default_rng(9)
        dts = dates[s]; uniq = np.unique(dts)
        by = {u: np.where(dts == u)[0] for u in uniq}
        bs = []
        for _ in range(2000):
            pick = rng.choice(uniq, size=len(uniq), replace=True)
            jj = np.concatenate([by[u] for u in pick])
            bs.append(float(y[s][jj].mean()))
        print(f"    {f'top {frac:.0%}':<12}{len(s):>6}{thr:>9.4f}{r:>11.4f}"
              f"{r-BAR_MEAN:>+11.4f}{r-BAR_TOP:>+11.4f}"
              f"   [{np.percentile(bs,2.5):.4f},{np.percentile(bs,97.5):.4f}]")
    print()


def main():
    print("=" * 104)
    print("  TOP-SLICE LADDERS.  Bar = DK's NRFI break-even (0.537 average / 0.577 on")
    print("  the priciest games).  A bettable NRFI edge requires the realised rate to")
    print("  clear the bar with a CI that excludes it.")
    print("=" * 104)
    for s in ["2025bt", "2026picks"]:
        d = ac.load(s)
        ladder(s, d["cal"], d["y"], d["dates"], "nrfi")
        ladder(s, d["cal"], d["y"], d["dates"], "yrfi")

    print("=" * 104)
    print("  SAME LADDER ON THE HONEST OUT-OF-FOLD REFIT (day-blocked 5-fold), 2026.")
    print("  Removes any suspicion that the live model's 2026 NRFI failure is a")
    print("  fitting artefact of the frozen 2026-05-26 weights.")
    print("=" * 104)
    import alt_06_honest_cv as cv
    d = ac.load("2026picks")
    _, pc = cv.oof(d, seed=0)
    ladder("2026 OOF refit", pc, d["y"], d["dates"], "nrfi")
    ladder("2026 OOF refit", pc, d["y"], d["dates"], "yrfi")


if __name__ == "__main__":
    main()
