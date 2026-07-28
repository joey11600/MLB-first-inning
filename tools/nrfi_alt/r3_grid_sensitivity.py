#!/usr/bin/env python3
"""
r3_grid_sensitivity.py -- is (top 35%, L2=10) a stable configuration or
one cell in a grid?  Sweep region quantile x L2, both directions, and
report SPEC-minus-BASE and SPEC-minus-GEN in-region AUC.
A real effect should be a smooth plateau, not a speckle.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402

QS = (0.20, 0.25, 0.30, 0.35, 0.40, 0.50)
L2S = (1.0, 3.0, 10.0, 30.0, 100.0)


def run(train, test, tag):
    Xtr, _ = C.design(train)
    Xte, _ = C.design(test)
    ytr = np.asarray([r["y_nrfi"] for r in train], float)
    yte = np.asarray([r["y_nrfi"] for r in test], float)
    ptr = np.asarray([r["prod"] for r in train], float)
    pte = np.asarray([r["prod"] for r in test], float)
    print(f"\n=== {tag} : SPEC - BASE (in-region AUC) ===")
    print("  q\\L2 " + "".join(f"{l:>9.0f}" for l in L2S))
    cells = 0
    pos = 0
    for q in QS:
        thr = float(np.quantile(ptr, 1 - q))
        the = float(np.quantile(pte, 1 - q))
        mtr, mte = ptr >= thr, pte >= the
        base = C.auc(yte[mte], pte[mte])
        row = f"  {q:.2f} "
        for l2 in L2S:
            sp = C.fit_lr(Xtr[mtr], ytr[mtr], l2=l2)
            d = C.auc(yte[mte], C.predict_lr(sp, Xte[mte])) - base
            cells += 1
            pos += d > 0
            row += f"{d:>+9.4f}"
        print(row)
    print(f"  cells={cells}  positive={pos}")
    return cells


def main():
    r25 = C.attach_production(C.load_2025())
    r26 = C.attach_production(C.load_2026())
    c = 0
    c += run(r25, r26, "TRAIN 2025 -> TEST 2026")
    c += run(r26, r25, "TRAIN 2026 -> TEST 2025")
    days = sorted({r["date"] for r in r26})
    cut = days[int(len(days) * 0.6)]
    c += run([r for r in r26 if r["date"] < cut],
             [r for r in r26 if r["date"] >= cut],
             "TRAIN 2026-early -> TEST 2026-late")
    print(f"\nTOTAL GRID CELLS SEARCHED (this script only): {c}")


if __name__ == "__main__":
    main()
