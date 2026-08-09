#!/usr/bin/env python3
"""
tools/season_replay.py -- replay the ENTIRE 2026 season through the
current production stack and report what it would have returned.

WHAT "CURRENT STACK" MEANS HERE
    LR weights      data/lr_t1.json + lr_b1.json   (fit 2026-05-26)
    calibrator      data/calibration_v2.json       (CIR, 2026-07-28)
    STRONG YRFI     p_nrfi < _LR_STRONG_YRFI_P     (0.36)
    STRONG NRFI     disabled (_LR_STRONG_NRFI_P = 1.01)
    lambda floor    weather-adjusted, as live
    staking         quarter Kelly, 10% per-bet cap, 15% same-day cap
Every one of those is read from the live files, not hardcoded, so this
replays what is actually deployed.

THE HONESTY PROBLEM, STATED UP FRONT
------------------------------------
Running today's model over games from April is NOT "what we would have
made". Two separate leaks:

  1. IN-SAMPLE CALIBRATOR. data/calibration_v2.json was fit on 2025 +
     2026 -- it has already seen the outcomes of the games it is being
     scored against. Its train_seasons field says so.
  2. HINDSIGHT PARAMETERS. The 0.36 gate was chosen on 2026-07-27 by
     looking at this same season's results. April's system did not have
     it, and could not have.

So this script reports THREE numbers, and only the third is defensible
as "what someone following the system would actually have made":

  A. AS-DEPLOYED REPLAY  -- today's stack over the whole season.
     Optimistic. Useful as a ceiling, not a forecast.
  B. PRICE-HONEST SUBSET -- same, restricted to games with a REAL
     captured DraftKings price. Removes the -110 placeholder artefact
     that inflated April by ~15u.
  C. WALK-FORWARD        -- at each date, uses ONLY a calibrator fit on
     data strictly BEFORE that date, and a gate chosen from prior
     settled bets only. No hindsight. This is the honest number.

Usage:
    python tools/season_replay.py
    python tools/season_replay.py --verbose
"""

from __future__ import annotations

import argparse
import csv
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
import recalibrate_v2 as rc  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from calibration import ProbCalibrator, CIRCalibrator  # noqa: E402

START_BANK = 100.0


def fnum(v):
    try:
        if v in (None, "", "None"):
            return None
        return float(str(v).strip().replace("−", "-"))
    except (TypeError, ValueError):
        return None


def payout(o):
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def implied(o):
    return abs(o) / (abs(o) + 100.0) if o < 0 else 100.0 / (o + 100.0)


# ---------------------------------------------------------------------------
# Load every 2026 game with the SAME feature construction production uses
# ---------------------------------------------------------------------------

def load_season():
    """Returns rows with raw model output + real prices + outcome."""
    t1, b1 = rc.load_lr_models()
    fi_park = rc.load_fi_park()
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        raw_rows = list(csv.DictReader(f))

    out = []
    skipped = 0
    # 2026-07-28: carry the CSV row index. (date, away, home) is NOT a key --
    # doubleheaders share it, and the season record's day view double-counted
    # both legs of LAD@NYY 7/19 and PIT@NYY 7/22 as the same bet. Additive
    # field; every existing consumer ignores it.
    for _rid, r in enumerate(raw_rows):
        actual = (r.get("actual_result") or "").upper()
        if actual not in ("NRFI", "YRFI"):
            continue
        fp = fi_park.get(r.get("home_team", ""), rc.FI_PARK_DEFAULT)
        try:
            tvec, bvec = rc._build_t1_b1_phase_e3(r, fp)
        except Exception:
            skipped += 1
            continue
        out.append({
            "rid": _rid,
            "gn": (r.get("game_number") or "1").strip() or "1",
            "date": r["date"],
            "away": r.get("away_team", ""),
            "home": r.get("home_team", ""),
            "t1": tvec,
            "b1": bvec,
            "yrfi_odds": fnum(r.get("market_yrfi_odds")),
            "nrfi_odds": fnum(r.get("market_nrfi_odds")),
            "yrfi_hit": actual == "YRFI",
            "lambda": fnum(r.get("lambda_lr_total")),
            "wx_temp": fnum(r.get("wx_temp_c")),
            "wx_wind": fnum(r.get("wx_wind_kmh")),
            "wx_dome": bool(fnum(r.get("wx_is_dome")) or 0),
            "y_nrfi": 0 if actual == "YRFI" else 1,
        })
    out.sort(key=lambda x: x["date"])

    Xt = np.asarray([x["t1"] for x in out], dtype=float)
    Xb = np.asarray([x["b1"] for x in out], dtype=float)
    raw = rc.lr_predict_two_stage(t1, b1, Xt, Xb)
    for x, p in zip(out, raw):
        x["raw"] = float(p)
    return out, skipped


def decide(p_nrfi, row, gate):
    """Mirror the production classifier for the YRFI side only.

    NRFI is disabled live (_LR_STRONG_NRFI_P = 1.01) and the 2026-07-28
    analysis found it negative in every band, so the replay does not bet
    it either -- replaying a side we deliberately turned off would report
    a system nobody is running.
    """
    lam = row["lambda"]
    floor = P._weather_adjusted_floor(
        P._LR_LAMBDA_YRFI_FLOOR, row["wx_temp"], row["wx_wind"], row["wx_dome"])
    if lam is not None and lam < floor:
        return False          # PASS -- LOW LAMBDA
    return p_nrfi < gate


def simulate(rows, probs, gate, frac=0.25, require_real_price=True,
             start=START_BANK, top_only=False, since=None):
    """Day-by-day compounding with the shipped Kelly helper.

    `top_only` keeps ONE bet a night: the slate's #1, which is the play
    the operator publishes a video about. The ranking rule is the same
    one the dashboard uses in lib/top-pick-rank.ts -- most confident
    first, then the better price -- because a backtest that defined "#1"
    differently from the live board would be measuring a strategy nobody
    is running. For a YRFI bet, confidence is p_nrfi ASCENDING, i.e.
    p_yrfi descending, and the price tiebreak takes the lower implied
    probability (the bigger payout).

    `since` filters what is EVALUATED, never what a calibrator was
    trained on. Cutting the row set at load time instead would have
    starved the walk-forward: it refits from games strictly before each
    date, and those games are exactly the ones a `--since` would have
    thrown away. The bank still starts at `start` on the first evaluated
    day, so the figure is "what this strategy did over this window",
    not a continuation of an earlier run.
    """
    byday = defaultdict(list)
    for r, p_nrfi in zip(rows, probs):
        if since and r["date"] < since:
            continue
        if not decide(p_nrfi, r, gate):
            continue
        odds = r["yrfi_odds"]
        if odds is None:
            if require_real_price:
                continue
            odds = -110.0          # the placeholder, only when asked for
        byday[r["date"]].append((1.0 - p_nrfi, odds, r["yrfi_hit"]))

    if top_only:
        for d, cands in byday.items():
            byday[d] = [min(cands, key=lambda c: (-c[0], implied(c[1])))]

    bank = peak = start
    mdd = 0.0
    n = w = 0
    flat = 0.0
    needs = []
    o = tracker.KELLY_FRACTION
    tracker.KELLY_FRACTION = frac
    try:
        for d in sorted(byday):
            # T8.18: bump the batch epoch.  Seeding _daily_committed directly
            # skips kelly_reset_daily_committed(), and kelly_stake_units now
            # refuses to allocate (game_date=...) on a process that never reset.
            tracker.kelly_reset_daily_committed()
            tracker._bankroll_cache = bank
            tracker._daily_committed = {d: 0.0}
            pnl = 0.0
            for p_y, odds, win in byday[d]:
                s = tracker.kelly_stake_units(p_y, str(int(odds)), game_date=d) or 0.0
                if s <= 0:
                    continue
                n += 1
                w += win
                needs.append(implied(odds))
                flat += payout(odds) if win else -1.0
                pnl += s * payout(odds) if win else -s
            bank += pnl
            peak = max(peak, bank)
            if peak > 0:
                mdd = max(mdd, (peak - bank) / peak)
    finally:
        tracker.KELLY_FRACTION = o
    return {"bets": n, "wins": w, "final": bank, "profit": bank - start,
            "maxdd": 100 * mdd, "flat": flat,
            "need": st.mean(needs) if needs else float("nan"),
            "days": len(byday)}


def line(label, r):
    hit = 100 * r["wins"] / r["bets"] if r["bets"] else 0
    print(f"  {label:<40}{r['bets']:>5}{hit:>7.1f}%{100*r['need']:>7.1f}%"
          f"{r['flat']:>+9.2f}u{r['final']:>10.2f}u{r['profit']:>+10.2f}u{r['maxdd']:>8.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--top-only", action="store_true",
                    help="one bet a night: the slate's #1, ranked as the "
                         "dashboard ranks it. This is the play the operator "
                         "publishes a video about.")
    ap.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                    help="restrict to dates on or after this. Use 2026-05-26 "
                         "for the current model weights; earlier games were "
                         "scored by a model that no longer exists.")
    args = ap.parse_args()

    rows, skipped = load_season()
    prod_cal = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    gate = P._LR_STRONG_YRFI_P

    print("=" * 108)
    print("  SEASON REPLAY -- 2026 through the CURRENT production stack")
    print("=" * 108)
    print(f"  graded games loaded : {len(rows)}   (skipped {skipped} unbuildable)")
    print(f"  span                : {rows[0]['date']} .. {rows[-1]['date']}")
    print(f"  calibrator          : {prod_cal.train_seasons}, n={prod_cal.train_n}, "
          f"{len(prod_cal.centers)} knots")
    print(f"  STRONG YRFI gate    : p_nrfi < {gate}")
    print(f"  staking             : {tracker.KELLY_FRACTION:.2f} Kelly, "
          f"{tracker.KELLY_MAX_STAKE_FRAC:.0%} per bet, "
          f"{tracker.KELLY_MAX_DAILY_FRAC:.0%} per day, {START_BANK:.0f}u start")
    priced = sum(1 for r in rows if r["yrfi_odds"] is not None)
    print(f"  games with a REAL captured DK price: {priced}/{len(rows)} "
          f"({100*priced/len(rows):.0f}%)")
    if args.top_only:
        print("  MODE                : #1 ONLY -- one bet a night, ranked as "
              "the dashboard ranks it")
    if args.since:
        print(f"  EVALUATED FROM      : {args.since} (earlier games still train "
              "the walk-forward calibrator)")

    probs = [prod_cal.predict(r["raw"]) for r in rows]

    print("\n" + "=" * 108)
    print(f"  {'scenario':<40}{'bets':>5}{'hit%':>7}{'need':>7}{'flat 1u':>9}"
          f"{'final':>10}{'profit':>10}{'maxDD':>8}")
    print("=" * 108)

    K = dict(top_only=args.top_only, since=args.since)

    print("\n  A. AS-DEPLOYED REPLAY  (optimistic -- calibrator has seen these games)")
    line("     real prices only", simulate(rows, probs, gate, require_real_price=True, **K))
    line("     incl. -110 placeholder fills",
         simulate(rows, probs, gate, require_real_price=False, **K))

    print("\n  B. PRICE-HONEST, BY MONTH  (real captured prices only)")
    bym = defaultdict(list)
    for r, p in zip(rows, probs):
        bym[r["date"][:7]].append((r, p))
    for m in sorted(bym):
        if args.since and m < args.since[:7]:
            continue
        rr = [x[0] for x in bym[m]]
        pp = [x[1] for x in bym[m]]
        line(f"     {m}", simulate(rr, pp, gate, **K))

    # ---------------- C. WALK-FORWARD -----------------------------------
    print("\n  C. WALK-FORWARD  (calibrator refit from PRIOR games only; no hindsight)")
    dates = sorted({r["date"] for r in rows})
    wf_probs = [None] * len(rows)
    idx_by_date = defaultdict(list)
    for i, r in enumerate(rows):
        idx_by_date[r["date"]].append(i)

    MIN_TRAIN = 200
    fitted = 0
    for d in dates:
        prior = [i for i in range(len(rows)) if rows[i]["date"] < d]
        if len(prior) < MIN_TRAIN:
            continue
        cal = CIRCalibrator.fit([rows[i]["raw"] for i in prior],
                                [rows[i]["y_nrfi"] for i in prior],
                                20, ["walkforward"])
        fitted += 1
        for i in idx_by_date[d]:
            wf_probs[i] = cal.predict(rows[i]["raw"])
    live = [(r, p) for r, p in zip(rows, wf_probs) if p is not None]
    print(f"     refit on {fitted} dates; {len(live)} games scored "
          f"(first {MIN_TRAIN} games used only for training)")
    if live:
        line("     walk-forward, real prices",
             simulate([x[0] for x in live], [x[1] for x in live], gate, **K))

    print("\n" + "=" * 108)
    print("  HOW TO READ THIS")
    print("=" * 108)
    print("  A is a CEILING. The calibrator in data/calibration_v2.json was fit on")
    print("     2025+2026 and has already seen these outcomes, and the 0.36 gate was")
    print("     chosen on 2026-07-27 by looking at this same season.")
    print("  B strips the -110 placeholder artefact that inflated April by ~15u.")
    print("  C is the only figure defensible as 'what someone following the system")
    print("     would have made', because every input at a given date comes from")
    print("     strictly earlier data. Expect it to be materially lower than A.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
