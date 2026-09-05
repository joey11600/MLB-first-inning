#!/usr/bin/env python3
"""
fi_form.py -- a starter's shrunk first-inning "form" rate, for the SHADOW model.

WHAT IT IS.  The fraction of a starter's starts THIS SEASON in which he kept
his own half of the first inning clean, shrunk toward the league rate with
K_STARTS starts of prior, using only starts strictly BEFORE the game date.
It is the empirical-Bayes version of the live `*_p_last10_pitcher_nrfi` input
(a raw fraction over the last 10 starts, 26 distinct values, no shrinkage).

WHY K = 65.  Measured 2026-09-03 (tools/refit2026/build_fi_form.py docstring):
the between-pitcher variance in first-inning clean rate is real and stable
across 2024/2025/2026 (tau^2 +0.00286 / +0.00323 / +0.00295), and the implied
shrinkage prior is 63-70 starts.  A starter makes ~30 starts a year, so a
10-start sample deserves ~13% weight on itself.  PRIOR_SEASON_W = 0 because
cross-season carryover measured weak and inconsistent (+0.08 / +0.21 / -0.14)
and the no-carry configuration won every variant of the three-split test.

WHERE THE HISTORY COMES FROM.  Nothing external: the two backtest files hold
2024 and 2025, and the ledger holds every graded 2026 game (the predictor
scores every MLB game, so every start is there).  Both carry the pitcher's
NAME, which is the key -- the 2024/25 files have no player id.  On 2026, 5 of
355 names map to more than one id, all September call-ups; accepted.

EQUIVALENCE.  For a graded 2026 game this reproduces the validated research
column `K65_pw0` in data/candidates/factor_fi_form.csv exactly (the test
suite checks it), the same way fi_pitcher_pool.py is held to its batch
builder.  The one intended difference is the league mean on a LIVE date:
the research builder looks the date up in its own log (and a future date is
absent, so it would fall back to the seed), whereas this module computes the
expanding league mean over all starts strictly before the date, which is the
definition.  Late in a season the two agree to ~1e-4.

FAIL-OPEN.  Any problem (missing file, unparsable row, unknown pitcher) yields
the league mean, which is what "no information" means here.  This module is
imported by the predictor at runtime and must not depend on numpy, pandas or
tools/ being importable.

CLI
    python fi_form.py "Eury Perez" 2026-09-02      # one estimate
    python fi_form.py --check                      # rebuild + agree with the research column
"""
from __future__ import annotations

import bisect
import csv
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BT = ROOT / "data" / "backtests"
SOURCES: list[tuple[Path, int]] = [
    (BT / "backtest_2024-04-01_to_2024-09-30_truepit_ptfix.csv", 2024),
    (BT / "backtest_2025-04-01_to_2025-09-30_truepit_ptfix.csv", 2025),
    (ROOT / "data" / "picks_2026.csv", 2026),
]

K_STARTS = 65.0          # shrinkage prior, in starts (empirical Bayes, 2026-09-03)
PRIOR_SEASON_W = 0.0     # weight on earlier seasons' starts at each season change
HALFLIFE: float | None = None   # recency decay in starts; None = every prior start counts equally
LEAGUE_SEED = 0.7129     # overall clean-first-inning rate, 2024-26
SEED_N = 50.0            # pseudo-starts behind the seed in the expanding league mean

_SKIP = {"", "nan", "none", "tbd", "tba"}


def _norm(name: str | None) -> str:
    return unicodedata.normalize("NFC", (name or "").strip())


def _clean(v: str | None) -> int | None:
    """1 if the half-inning had no run, 0 if it had one, None if unknown."""
    try:
        return 1 if float(v) == 0.0 else 0          # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class _Log:
    """Per-start records plus the indices the estimate needs.  Rebuilt when any
    source file changes on disk (the ledger moves every cycle; the rebuild is
    ~13k rows of csv, well under a second)."""

    def __init__(self) -> None:
        self.by_name: dict[str, list[tuple[str, int, int]]] = {}
        self.dates: list[str] = []          # distinct dates, sorted
        self.cum_n: list[float] = []        # starts strictly before dates[i]
        self.cum_s: list[float] = []        # clean starts strictly before dates[i]
        self.total_n = 0.0
        self.total_s = 0.0
        self.stamp: tuple = ()

    @staticmethod
    def _stamp() -> tuple:
        return tuple((str(p), os.path.getmtime(p) if p.exists() else -1.0) for p, _ in SOURCES)

    def build(self) -> "_Log":
        # One record per (season, game_pk, side).  A postponed-then-played
        # game keeps its original ledger row (re-graded when played) and gains
        # one on the makeup date, same game_pk, same result -- the LATER date
        # wins, because that is when the start happened.  Rows without a
        # game_pk cannot be deduplicated and are kept.  Same rule as the
        # research builder (tools/refit2026/build_fi_form.py), which the test
        # suite holds this module to.
        keyed: dict[tuple, tuple[str, int, str, int]] = {}
        loose: list[tuple[str, int, str, int]] = []
        for path, season in SOURCES:
            if not path.exists():
                continue
            with open(path, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(fh):
                    if (r.get("fi_total_runs") or "").strip() == "":
                        continue                       # not graded
                    d = (r.get("date") or "")[:10]
                    if len(d) != 10:
                        continue
                    gp = (r.get("game_pk") or "").strip().split(".")[0]
                    # home starter works the TOP of the 1st (away bats);
                    # away starter works the BOTTOM (home bats)
                    for who, runs in (("home_pitcher", "fi_away_runs"),
                                      ("away_pitcher", "fi_home_runs")):
                        name = _norm(r.get(who))
                        c = _clean(r.get(runs))
                        if name.lower() in _SKIP or c is None:
                            continue
                        rec = (d, season, name, c)
                        if gp:
                            k = (season, gp, who)
                            if k not in keyed or d > keyed[k][0]:
                                keyed[k] = rec
                        else:
                            loose.append(rec)
        rows = list(keyed.values()) + loose
        rows.sort(key=lambda t: (t[0], t[2]))
        by_name: dict[str, list[tuple[str, int, int]]] = {}
        per_day: dict[str, list[int]] = {}
        for d, season, name, c in rows:
            by_name.setdefault(name, []).append((d, season, c))
            per_day.setdefault(d, [0, 0])
            per_day[d][0] += 1
            per_day[d][1] += c
        dates = sorted(per_day)
        cum_n, cum_s, n, s = [], [], 0.0, 0.0
        for d in dates:                       # cumulative BEFORE each date
            cum_n.append(n); cum_s.append(s)
            n += per_day[d][0]; s += per_day[d][1]
        self.by_name, self.dates, self.cum_n, self.cum_s = by_name, dates, cum_n, cum_s
        self.total_n, self.total_s = n, s
        self.stamp = self._stamp()
        return self

    def fresh(self) -> bool:
        return bool(self.stamp) and self.stamp == self._stamp()

    def league_mean_before(self, date_iso: str) -> float:
        """Expanding league clean rate over every start strictly before the date."""
        i = bisect.bisect_left(self.dates, date_iso)
        if i < len(self.dates):
            n, s = self.cum_n[i], self.cum_s[i]
        else:
            n, s = self.total_n, self.total_s
        return (s + SEED_N * LEAGUE_SEED) / (n + SEED_N)

    def estimate(self, name: str, date_iso: str, K: float | None = None,
                 prior_w: float | None = None, halflife: float | None = None) -> float:
        """Exactly the research builder's recursion (build_fi_form.estimates):
        walk the pitcher's starts in date order, multiply the carried mass by
        `prior_w` at each season change, decay it by 0.5**(1/halflife) per
        start, add the start, and stop at the first start on or after the
        query date.  Parameters default to the module constants; the shadow
        model passes the ones its candidate was fit with (meta.json)."""
        K = K_STARTS if K is None else float(K)
        pw = PRIOR_SEASON_W if prior_w is None else float(prior_w)
        hl = HALFLIFE if halflife is None else halflife
        decay = 0.5 ** (1.0 / float(hl)) if hl else 1.0
        n_acc = s_acc = 0.0
        season_seen: int | None = None
        for d, se, c in self.by_name.get(_norm(name), ()):
            if d >= date_iso:
                break                         # sorted by date; nothing later counts
            if season_seen is not None and se != season_seen:
                n_acc *= pw; s_acc *= pw
            season_seen = se
            n_acc = n_acc * decay + 1.0
            s_acc = s_acc * decay + c
        if season_seen is not None and season_seen < int(date_iso[:4]):
            n_acc *= pw; s_acc *= pw          # the query is in a later season than his last start
        mu = self.league_mean_before(date_iso)
        return (s_acc + K * mu) / (n_acc + K)


_log: _Log | None = None


def get_log() -> _Log:
    global _log
    if _log is None or not _log.fresh():
        _log = _Log().build()
    return _log


def estimate(name: str, date_iso: str, K: float | None = None,
             prior_w: float | None = None, halflife: float | None = None) -> float:
    """Shrunk clean-first-inning rate for `name` as of `date_iso` (strictly
    before it).  Never raises: falls back to the league mean."""
    try:
        return get_log().estimate(name, date_iso, K=K, prior_w=prior_w, halflife=halflife)
    except Exception as e:                    # noqa: BLE001 -- fail open by design
        print(f"  [warn] fi_form estimate failed for {name!r} on {date_iso}: {e}", file=sys.stderr)
        try:
            return get_log().league_mean_before(date_iso)
        except Exception:
            return LEAGUE_SEED


# Research configs this module must reproduce, with the parameters the
# research builder used for each (tools/refit2026/build_fi_form.GRID).
_CHECK_CONFIGS = {
    "K65_pw0":  dict(K=65.0, prior_w=0.0, halflife=None),
    "K65_all":  dict(K=65.0, prior_w=0.3, halflife=None),
    "K65_hl15": dict(K=65.0, prior_w=0.3, halflife=15.0),
}


def _check() -> int:
    """Agree with the validated research columns on every graded 2026 game.

    A rescheduled game keeps its original ledger row (graded with the makeup
    game's result, under starters who never threw that first inning) and
    gains a row on the makeup date.  Both builders keep only the LATEST row
    per (game_pk, side), so the comparison skips the superseded rows too.
    """
    path = ROOT / "data" / "candidates" / "factor_fi_form.csv"
    if not path.exists():
        print(f"no research column at {path}; run tools/refit2026/build_fi_form.py first")
        return 2
    ref: dict[str, dict] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            ref[str(r["game_pk"]).split(".")[0]] = r
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("fi_total_runs") or "").strip() != ""]
    latest: dict[tuple[str, str], str] = {}
    for r in rows:
        gp = str(r.get("game_pk", "")).split(".")[0]
        for who in ("home_pitcher", "away_pitcher"):
            k = (gp, who)
            if r["date"][:10] > latest.get(k, ""):
                latest[k] = r["date"][:10]
    log = get_log()
    ok = True
    for cfg, params in _CHECK_CONFIGS.items():
        n = 0; worst = 0.0; worst_row = None
        for r in rows:
            gp = str(r.get("game_pk", "")).split(".")[0]
            got = ref.get(gp)
            if not got:
                continue
            for who, col in (("home_pitcher", f"home_{cfg}"), ("away_pitcher", f"away_{cfg}")):
                want = got.get(col, "")
                if want in ("", "nan") or _norm(r.get(who)).lower() in _SKIP:
                    continue
                if latest.get((gp, who)) != r["date"][:10]:
                    continue                  # superseded duplicate row
                mine = log.estimate(r[who], r["date"][:10], **params)
                diff = abs(mine - float(want))
                n += 1
                if diff > worst:
                    worst, worst_row = diff, (r["date"], r[who], round(mine, 6), float(want))
        good = worst < 1e-9
        ok &= good
        print(f"fi_form check [{cfg}]: {n} pitcher-games vs factor_fi_form.csv  "
              f"worst |module - research| = {worst:.2e}  {'PASS' if good else 'FAIL ' + str(worst_row)}")
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--check":
        raise SystemExit(_check())
    if len(sys.argv) == 3:
        print(f"{estimate(sys.argv[1], sys.argv[2]):.4f}")
        raise SystemExit(0)
    print(__doc__)
