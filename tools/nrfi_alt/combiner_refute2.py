"""Combiner refutation, v2 -- SAME (current) LR half-models on BOTH seasons.

v1 was invalid: the stored away_lambda/home_lambda in the 2025 backtest come
from an older model version (mean raw p_nrfi 0.406, only 0.5% of games >= 0.50)
while picks_2026 stores the current model's lambdas.  Here we re-score both
seasons with data/lr_t1.json + lr_b1.json so the halves are comparable.

ANALYSIS ONLY.
"""
from __future__ import annotations
import csv, math, sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import recalibrate_v2 as rc  # noqa: E402

rng = np.random.default_rng(20260728)


def _f(v, d=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def score(rows, home_key, park_col=None):
    t1m, b1m = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    Xt, Xb, keep = [], [], []
    for r in rows:
        fp = None
        if park_col:
            fp = _f(r.get(park_col))
        if fp is None:
            fp = fi_park.get(r.get(home_key, ""), rc.FI_PARK_DEFAULT)
        try:
            tv, bv = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            continue
        Xt.append(tv); Xb.append(bv); keep.append(r)
    Xt = np.asarray(Xt, float); Xb = np.asarray(Xb, float)
    p_t1 = rc.lr_predict_raw(t1m, Xt)   # P(run in top 1)
    p_b1 = rc.lr_predict_raw(b1m, Xb)
    return keep, 1.0 - p_t1, 1.0 - p_b1   # nT1, nB1


def load(path, home_key, park_col, tot_col, away_col, home_runs_col):
    raw = list(csv.DictReader(open(path, encoding="utf-8")))
    raw = [r for r in raw if _f(r.get(tot_col)) is not None]
    keep, n1, n2 = score(raw, home_key, park_col)
    out = []
    for r, a, b in zip(keep, n1, n2):
        out.append(dict(date=r["date"], n1=float(a), n2=float(b),
                        y=1 if _f(r[tot_col]) == 0 else 0,
                        nrfi_odds=_f(r.get("market_nrfi_odds")),
                        yrfi_odds=_f(r.get("market_yrfi_odds"))))
    return out


def logit(p):
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


KINDS = ["product", "free", "balance", "weakest", "free_balance", "prodmin"]


def feats(rows, kind):
    X = []
    for r in rows:
        x1, x2 = logit(r["n1"]), logit(r["n2"])
        s = x1 + x2
        if kind == "product":       X.append([s])
        elif kind == "free":        X.append([x1, x2])
        elif kind == "balance":     X.append([s, abs(x1 - x2)])
        elif kind == "weakest":     X.append([s, min(x1, x2)])
        elif kind == "free_balance":X.append([x1, x2, abs(x1 - x2)])
        elif kind == "prodmin":     X.append([s, min(r["n1"], r["n2"])])
    return np.asarray(X, float)


def fit_lr(X, y, l2=1e-3):
    mu, sd = X.mean(0), X.std(0); sd[sd == 0] = 1.0
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    w = np.zeros(Z.shape[1])
    for _ in range(300):
        p = 1 / (1 + np.exp(-Z @ w))
        W = np.clip(p * (1 - p), 1e-8, None)
        g = Z.T @ (y - p) - l2 * w
        H = (Z * W[:, None]).T @ Z + l2 * np.eye(Z.shape[1])
        st = np.linalg.solve(H, g); w += st
        if np.max(np.abs(st)) < 1e-10: break
    return dict(mu=mu, sd=sd, w=w)


def pred(m, X):
    Z = np.hstack([(X - m["mu"]) / m["sd"], np.ones((len(X), 1))])
    return 1 / (1 + np.exp(-Z @ m["w"]))


def auc(y, s):
    y = np.asarray(y, float); s = np.asarray(s, float)
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0: return float("nan")
    o = np.argsort(s, kind="mergesort"); sr = s[o]
    ranks = np.empty(len(s)); i = 0
    while i < len(sr):
        j = i
        while j + 1 < len(sr) and sr[j + 1] == sr[i]: j += 1
        ranks[o[i:j + 1]] = (i + j) / 2.0 + 1; i = j + 1
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def day_boot(rows, y, mask, sa, sb, B=3000):
    days = {}
    for i, r in enumerate(rows):
        if mask[i]: days.setdefault(r["date"], []).append(i)
    keys = list(days); d = []
    for _ in range(B):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([days[keys[j]] for j in pick])
        yy = y[idx]
        if yy.sum() in (0, len(yy)): continue
        d.append(auc(yy, sb[idx]) - auc(yy, sa[idx]))
    d = np.array(d)
    return np.percentile(d, [2.5, 50, 97.5]), (d > 0).mean(), len(keys)


def run(train, test, tag, lo):
    ytr = np.array([r["y"] for r in train], float)
    yte = np.array([r["y"] for r in test], float)
    pte = np.array([r["n1"] * r["n2"] for r in test])
    hi = pte >= lo
    print(f"\n=== {tag} (high regime = product p_nrfi >= {lo}) ===")
    print(f"    train n={len(train)} NRFI={ytr.mean():.3f} | test n={len(test)} "
          f"NRFI={yte.mean():.3f} | hi n={hi.sum()} NRFI={yte[hi].mean() if hi.sum() else float('nan'):.3f}")
    sc = {}
    for k in KINDS:
        m = fit_lr(feats(train, k), ytr)
        s = pred(m, feats(test, k)); sc[k] = s
        print(f"    {k:13s} AUC_all {auc(yte,s):.4f}   AUC_hi {auc(yte[hi],s[hi]):.4f}   w={np.round(m['w'],3)}")
    return sc, yte, hi, pte


if __name__ == "__main__":
    b25 = load("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv",
               "home", "fi_park_nrfi_rate", "fi_total_runs", "fi_away_runs", "fi_home_runs")
    p26 = load("data/picks_2026.csv", "home_team", None,
               "fi_total_runs", "fi_away_runs", "fi_home_runs")
    for nm, d in (("2025", b25), ("2026", p26)):
        p = np.array([r["n1"] * r["n2"] for r in d])
        print(f"{nm}: n={len(d)} NRFI={np.mean([r['y'] for r in d]):.4f} "
              f"raw p mean {p.mean():.4f} pct {np.round(np.percentile(p,[5,50,95]),3)} "
              f"frac>=0.50 {(p>=0.50).mean():.3f} frac>=0.55 {(p>=0.55).mean():.3f}")

    for lo in (0.50, 0.55):
        sc26, y26, hi26, _ = run(b25, p26, "TRAIN 2025 -> TEST 2026", lo)
        sc25, y25, hi25, _ = run(p26, b25, "TRAIN 2026 -> TEST 2025", lo)
        print(f"\n  -- day-block bootstrap, delta AUC vs product, high regime --")
        for k in KINDS[1:]:
            ci, pg, nd = day_boot(p26, y26, hi26, sc26["product"], sc26[k])
            print(f"    2026 {k:13s} {ci[1]:+.4f}  CI[{ci[0]:+.4f},{ci[2]:+.4f}] P>0={pg:.2f} days={nd}")
        for k in KINDS[1:]:
            ci, pg, nd = day_boot(b25, y25, hi25, sc25["product"], sc25[k])
            print(f"    2025 {k:13s} {ci[1]:+.4f}  CI[{ci[0]:+.4f},{ci[2]:+.4f}] P>0={pg:.2f} days={nd}")
