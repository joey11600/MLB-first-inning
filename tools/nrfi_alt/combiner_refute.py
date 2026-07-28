"""Refutation test: does a learned combiner beat the product (1-pT1)(1-pB1)?

ANALYSIS ONLY.  Reads CSVs, writes nothing.

Per-half probabilities are recovered exactly from the stored lambdas:
    nT1 = exp(-lambda_t1)   (= 1 - P(run in top 1))
    nB1 = exp(-lambda_b1)
    product p_nrfi = exp(-(lam_t1+lam_b1))    [verified 0 mismatches vs stored raw]
"""
import csv, glob, math, sys
import numpy as np

rng = np.random.default_rng(20260728)


def _f(v, d=None):
    try:
        x = float(v)
        return x if math.isfinite(x) else d
    except (TypeError, ValueError):
        return d


def load_backtest(path):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        la, lb = _f(r.get("away_lambda")), _f(r.get("home_lambda"))
        tot = _f(r.get("fi_total_runs"))
        if la is None or lb is None or tot is None:
            continue
        out.append(dict(date=r["date"], lam_t1=la, lam_b1=lb,
                        y=1 if tot == 0 else 0,
                        t1_y=1 if _f(r.get("fi_away_runs"), 0) == 0 else 0,
                        b1_y=1 if _f(r.get("fi_home_runs"), 0) == 0 else 0,
                        nrfi_odds=None, yrfi_odds=None))
    return out


def load_picks(path="data/picks_2026.csv"):
    out = []
    for r in csv.DictReader(open(path, encoding="utf-8")):
        la, lb = _f(r.get("lambda_lr_t1")), _f(r.get("lambda_lr_b1"))
        tot = _f(r.get("fi_total_runs"))
        if la is None or lb is None or tot is None:
            continue
        out.append(dict(date=r["date"], lam_t1=la, lam_b1=lb,
                        y=1 if tot == 0 else 0,
                        t1_y=1 if _f(r.get("fi_away_runs"), 0) == 0 else 0,
                        b1_y=1 if _f(r.get("fi_home_runs"), 0) == 0 else 0,
                        nrfi_odds=_f(r.get("market_nrfi_odds")),
                        yrfi_odds=_f(r.get("market_yrfi_odds")),
                        cal_p=_f(r.get("nrfi_prob"))))
    return out


def logit(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def feats(rows, kind):
    X = []
    for r in rows:
        n1 = math.exp(-r["lam_t1"])
        n2 = math.exp(-r["lam_b1"])
        x1, x2 = logit(n1), logit(n2)
        s = x1 + x2
        if kind == "product":
            X.append([s])
        elif kind == "free":
            X.append([x1, x2])
        elif kind == "balance":
            X.append([s, abs(x1 - x2)])
        elif kind == "weakest":
            X.append([s, min(x1, x2)])
        elif kind == "free_balance":
            X.append([x1, x2, abs(x1 - x2)])
        elif kind == "prodmin":
            X.append([s, min(n1, n2)])
        else:
            raise ValueError(kind)
    return np.asarray(X, float)


def fit_lr(X, y, iters=400, l2=1e-3):
    """Plain Newton/IRLS logistic regression on standardized X."""
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    w = np.zeros(Z.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-Z @ w))
        W = np.clip(p * (1 - p), 1e-8, None)
        g = Z.T @ (y - p) - l2 * w
        H = (Z * W[:, None]).T @ Z + l2 * np.eye(Z.shape[1])
        step = np.linalg.solve(H, g)
        w += step
        if np.max(np.abs(step)) < 1e-9:
            break
    return dict(mu=mu, sd=sd, w=w)


def pred(m, X):
    Z = np.hstack([(X - m["mu"]) / m["sd"], np.ones((len(X), 1))])
    return 1 / (1 + np.exp(-Z @ m["w"]))


def auc(y, s):
    y = np.asarray(y); s = np.asarray(s)
    n1, n0 = y.sum(), (1 - y).sum()
    if n1 == 0 or n0 == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), float)
    sr = s[order]
    i = 0
    while i < len(sr):
        j = i
        while j + 1 < len(sr) and sr[j + 1] == sr[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1
        i = j + 1
    return (ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


KINDS = ["product", "free", "balance", "weakest", "free_balance", "prodmin"]


def run(train, test, tag, regime_lo=0.50):
    ytr = np.array([r["y"] for r in train], float)
    yte = np.array([r["y"] for r in test], float)
    p_prod_te = np.array([math.exp(-(r["lam_t1"] + r["lam_b1"])) for r in test])
    p_prod_tr = np.array([math.exp(-(r["lam_t1"] + r["lam_b1"])) for r in train])
    hi = p_prod_te >= regime_lo
    print(f"\n=== {tag} ===  train n={len(train)} (NRFI {ytr.mean():.3f})"
          f"  test n={len(test)} (NRFI {yte.mean():.3f})"
          f"  high-regime n={hi.sum()} (NRFI {yte[hi].mean():.3f})")
    scores = {}
    for k in KINDS:
        m = fit_lr(feats(train, k), ytr)
        s_te = pred(m, feats(test, k))
        s_tr = pred(m, feats(train, k))
        scores[k] = s_te
        print(f"  {k:13s} AUC_all {auc(yte, s_te):.4f}  AUC_hi {auc(yte[hi], s_te[hi]):.4f}"
              f"   [in-sample all {auc(ytr, s_tr):.4f}]  w={np.round(m['w'],3)}")
    # raw product ranking (no refit) as sanity
    print(f"  {'raw-product':13s} AUC_all {auc(yte, p_prod_te):.4f}  AUC_hi {auc(yte[hi], p_prod_te[hi]):.4f}")
    return scores, yte, hi, p_prod_te


def day_block_boot(test, yte, mask, s_a, s_b, B=2000):
    """Bootstrap over DAYS of the delta AUC(b) - AUC(a) on masked subset."""
    days = {}
    for i, r in enumerate(test):
        if mask[i]:
            days.setdefault(r["date"], []).append(i)
    keys = list(days)
    deltas = []
    for _ in range(B):
        pick = rng.integers(0, len(keys), len(keys))
        idx = np.concatenate([days[keys[j]] for j in pick])
        yy = yte[idx]
        if yy.sum() == 0 or yy.sum() == len(yy):
            continue
        deltas.append(auc(yy, s_b[idx]) - auc(yy, s_a[idx]))
    d = np.array(deltas)
    return np.percentile(d, [2.5, 50, 97.5]), (d > 0).mean(), len(keys)


if __name__ == "__main__":
    b25 = load_backtest("data/backtests/backtest_2025-04-01_to_2025-09-30_truepit.csv")
    p26 = load_picks()
    print(f"2025 backtest rows w/ outcome: {len(b25)}")
    print(f"2026 picks rows w/ outcome:    {len(p26)}")

    s26, y26, hi26, pp26 = run(b25, p26, "TRAIN 2025 -> TEST 2026")
    s25, y25, hi25, pp25 = run(p26, b25, "TRAIN 2026 -> TEST 2025")

    print("\n--- day-block bootstrap of AUC delta vs product, high regime, test=2026 ---")
    for k in KINDS[1:]:
        ci, pgt, nd = day_block_boot(p26, y26, hi26, s26["product"], s26[k])
        print(f"  {k:13s} delta median {ci[1]:+.4f}  95% CI [{ci[0]:+.4f},{ci[2]:+.4f}]"
              f"  P(delta>0)={pgt:.2f}  days={nd}")
    print("\n--- same, test=2025 ---")
    for k in KINDS[1:]:
        ci, pgt, nd = day_block_boot(b25, y25, hi25, s25["product"], s25[k])
        print(f"  {k:13s} delta median {ci[1]:+.4f}  95% CI [{ci[0]:+.4f},{ci[2]:+.4f}]"
              f"  P(delta>0)={pgt:.2f}  days={nd}")
