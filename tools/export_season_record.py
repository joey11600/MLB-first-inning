#!/usr/bin/env python3
"""
tools/export_season_record.py -- writes data/season_record.json: the two
numbers the dashboard displays as THE system record, plus a per-date
reconciliation of what the live system did against what the current
model would do.

THE TWO RECORDS (operator spec, 2026-07-28)
-------------------------------------------
  PROJECTED  The whole season, April 1 to today. Every game the current
             system would bet. Where a real DraftKings price exists we
             use it; where none was ever captured we use an EXPLICIT
             -125, every time, no exceptions.

  REAL       2026-05-07 onward -- the first day DK price capture became
             reliable (>=80% sustained; 99.6% of games priced from that
             date). REAL captured prices only. Nothing filled, nothing
             assumed. This is the defensible number.

BOTH include YRFI (live gate, read from the predictor) and NRFI at
p_nrfi >= 0.60. Live NRFI *betting* stays disabled, but the operator's
decision is that the displayed record counts both sides.

TWO METHODS, BOTH REPORTED (changed 2026-07-28)
-----------------------------------------------
The card used to say "CURRENT MODEL REPLAYED" while actually scoring
every game with a calibrator rebuilt from scratch at each date. Those
are not the same system, and the difference is not small: the rebuilt
curve reads +0.025 higher than the shipped one in April/May, and since
YRFI fires on a LOW p_nrfi, reading high means betting less. Over the
real window that gap alone was 31 bets.

So we now compute BOTH and label them honestly:

  HEADLINE ("deployed")   scores with data/calibration_v2.json -- the
                          calibrator the live system actually uses --
                          at the live gate. This answers "what is the
                          system I am running worth?" It is the number
                          the operator asked to lead with.
                          Caveat: that calibrator was fit on 2025+2026,
                          so it has seen these outcomes. Optimistic.

  FLOOR ("walkForward")   at each date, a calibrator fit ONLY on games
                          strictly before that date. No hindsight.
                          Pessimistic, and the honest lower bound.

Both are written. The dashboard leads with the headline and prints the
floor beside it. Neither is hidden.

THE HEADLINE FIGURE IS FLAT PROFIT, NOT THE KELLY BANKROLL. Compounding
a simulated 100u bank turns a +34u edge into a +880u "profit" that was
never staked; that number now lives under "sim" and is labelled as a
simulation.

PER-DATE RECONCILIATION
-----------------------
days[] carries, for every date, each game that EITHER the live ledger
flagged STRONG or the current model would bet, with both dispositions
side by side and a plain reason for every skip. This exists because the
operator could see "6 STRONG" in the header, "4 graded bets" in the
ledger, and "1 bet" in the record on one screen with nothing explaining
the difference.

Run after nightly grading:
    python tools/export_season_record.py
"""

from __future__ import annotations

import csv
import json
import os
import statistics as st
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402
import mlb_first_inning_predictor as P  # noqa: E402
from recalibrate_v2 import ProbCalibrator  # noqa: E402
from tools.season_replay import load_season, payout, implied  # noqa: E402
from tools.gate_validation import walk_forward_probs  # noqa: E402
from tools.season_record import simulate, START  # noqa: E402

FILL = -125.0
NRFI_GATE = 0.60          # paper gate; live NRFI betting remains disabled
REAL_START = "2026-05-07"  # first day DK capture is >=80% and sustained


# --------------------------------------------------------------------
# disposition: every candidate game + a machine-readable reason
# --------------------------------------------------------------------

def disposition(rows, probs, *, side, gate, fill):
    """Yield (row, p_nrfi, reason, odds) for every game on this side.

    reason is 'candidate' when the game clears every pre-staking filter
    (staking may still zero it); otherwise it names the filter that
    rejected it. The old select() dropped rejects silently, which is
    precisely why the dashboard could not explain a missing bet.
    """
    out = []
    for r, p in zip(rows, probs):
        if p is None:
            out.append((r, None, "warmup", None))
            continue
        if side == "YRFI":
            fl = P._weather_adjusted_floor(
                P._LR_LAMBDA_YRFI_FLOOR, r["wx_temp"], r["wx_wind"], r["wx_dome"])
            if r["lambda"] is not None and r["lambda"] < fl:
                out.append((r, p, "lambda-floor", None))
                continue
            if p >= gate:
                out.append((r, p, "gate", None))
                continue
            odds, win = r["yrfi_odds"], r["yrfi_hit"]
        else:
            if p < gate:
                out.append((r, p, "gate", None))
                continue
            if r["lambda"] is not None and r["lambda"] > P._LR_LAMBDA_NRFI_CEILING:
                out.append((r, p, "lambda-ceiling", None))
                continue
            odds, win = r["nrfi_odds"], (not r["yrfi_hit"])
        assumed = odds is None
        if assumed:
            if fill is None:
                out.append((r, p, "no-price", None))
                continue
            odds = float(fill)
        out.append((r, p, "candidate", (odds, win, assumed)))
    return out


def build_bets(disp, side, date_from=None):
    """Candidate rows -> the bet dicts simulate() consumes."""
    bets = []
    for r, p, why, extra in disp:
        if why != "candidate":
            continue
        if date_from and r["date"] < date_from:
            continue
        odds, win, assumed = extra
        bets.append({
            "date": r["date"],
            "p": (1.0 - p) if side == "YRFI" else p,
            "odds": odds, "win": win, "side": side, "assumed": assumed,
            "game": _label(r),
            "_key": (r["rid"], side),
        })
    return bets


def _label(r):
    """AWAY@HOME, with a game number when it is a doubleheader leg.

    Both legs otherwise render identically and read as a duplicated row.
    """
    base = f"{r['away']}@{r['home']}"
    return base if r.get("gn", "1") == "1" else f"{base} G{r['gn']}"


def summarize(staked):
    """Headline stats over the bets that actually got a stake."""
    n = len(staked)
    if n == 0:
        return None
    w = sum(1 for b in staked if b["win"])
    flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in staked)
    need = st.mean([implied(b["odds"]) for b in staked])
    return {
        "bets": n, "wins": w, "losses": n - w,
        "hitRate": round(w / n, 4),
        "breakEvenNeeded": round(need, 4),
        "edgePts": round(100 * (w / n - need), 2),
        "flatProfit": round(flat, 2),
        "assumedBets": sum(1 for b in staked if b.get("assumed")),
    }


CODES = {
    "gate": "gate", "lambda-floor": "lambda_floor",
    "lambda-ceiling": "lambda_ceiling", "no-price": "no_price",
    "warmup": "unscored",
}

SKIP_COPY = {
    # Plain English. The operator is not a developer; "p >= gate" means
    # nothing to them. YRFI fires when the no-run chance is LOW, so a
    # gate rejection reads as "too likely to be scoreless".
    "gate":           "model wasn't confident enough",
    "lambda-floor":   "too few runs projected",
    "lambda-ceiling": "too many runs projected",
    "no-price":       "no DraftKings price was captured",
    "warmup":         "not enough history yet to score it",
}


def make_record(rows, probs_dep, probs_wf, ledger, *, label, price_fill,
                date_from, date_to):
    """One record: deployed headline + walk-forward floor + day detail."""
    def run(probs, fill):
        d_y = disposition(rows, probs, side="YRFI", gate=P._LR_STRONG_YRFI_P, fill=fill)
        d_n = disposition(rows, probs, side="NRFI", gate=NRFI_GATE, fill=fill)
        bets = build_bets(d_y, "YRFI", date_from) + build_bets(d_n, "NRFI", date_from)
        bets.sort(key=lambda b: b["date"])
        bank, mdd, curve, staked = simulate(bets)
        return d_y, d_n, bets, bank, mdd, curve, staked

    d_y, d_n, bets, bank, mdd, curve, staked = run(probs_dep, price_fill)
    head = summarize(staked)
    if not head:
        return None
    _, _, wf_bets, wf_bank, wf_mdd, _, wf_staked = run(probs_wf, price_fill)
    floor = summarize(wf_staked)

    dropped = [b for b in bets if b.get("stake", 0.0) <= 0]
    dropped_flat = sum(payout(b["odds"]) if b["win"] else -1.0 for b in dropped)

    bym = defaultdict(list)
    for b in staked:
        bym[b["date"][:7]].append(b)
    monthly = []
    for m in sorted(bym):
        g = bym[m]
        mw = sum(1 for b in g if b["win"])
        monthly.append({
            "month": m, "bets": len(g), "wins": mw, "losses": len(g) - mw,
            "flat": round(sum(payout(x["odds"]) if x["win"] else -1.0 for x in g), 2),
            "assumedBets": sum(1 for x in g if x.get("assumed")),
        })

    # ---- per-date reconciliation -----------------------------------
    stakemap = {b["_key"]: b for b in bets}
    bank_after = dict(curve)
    byd = defaultdict(list)

    for side, disp in (("YRFI", d_y), ("NRFI", d_n)):
        for r, p, why, extra in disp:
            if date_from and r["date"] < date_from:
                continue
            key = (r["rid"], side)
            lrow = ledger[r["rid"]] if r["rid"] < len(ledger) else {}
            was_strong = (lrow.get("pick_strength") == "STRONG"
                          and lrow.get("pick_side") == side)
            b = stakemap.get(key)
            did_bet = bool(b and b.get("stake", 0.0) > 0)
            # Only games one of the two systems cared about. Listing all
            # 12 games x 120 days would bloat the file and bury the
            # comparison the operator actually needs.
            if not (was_strong or did_bet):
                continue

            if did_bet:
                rec = {
                    "action": "BET",
                    "stake": round(b["stake"], 2),
                    "odds": int(b["odds"]),
                    "win": bool(b["win"]),
                    "pnl": round(b["stake"] * payout(b["odds"]) if b["win"]
                                 else -b["stake"], 2),
                    "assumed": bool(b.get("assumed")),
                }
            elif why == "candidate":
                odds = extra[0]
                edge = ((1.0 - p) if side == "YRFI" else p) - implied(odds)
                rec = {"action": "SKIP",
                       "code": "kelly_no_edge" if edge <= 0 else "daily_cap",
                       "reason": ("no edge at that price" if edge <= 0
                                  else "daily risk cap already used")}
            else:
                # 'code' is the stable machine-readable reason; 'reason' is
                # the prose shown to the operator. The dashboard keys its
                # copy off code so re-wording prose never breaks the UI.
                rec = {"action": "SKIP", "code": CODES.get(why, why),
                       "reason": SKIP_COPY.get(why, why)}
                if why == "gate":
                    rec["reason"] += (f" ({p:.3f} vs {P._LR_STRONG_YRFI_P:.2f} needed)"
                                      if side == "YRFI"
                                      else f" ({p:.3f} vs {NRFI_GATE:.2f} needed)")

            entry = {
                "game": _label(r),
                "side": side,
                "modelP": round(p, 4) if p is not None else None,
                "record": rec,
            }
            if was_strong:
                pl = (lrow.get("profit_loss_units") or "").strip()
                entry["ledger"] = {
                    "strength": "STRONG",
                    "placed": (lrow.get("bet_placed") or "").strip().upper() == "Y",
                    "unitsRisked": _f(lrow.get("units_risked")),
                    "odds": _i(lrow.get("market_yrfi_odds") if side == "YRFI"
                               else lrow.get("market_nrfi_odds")),
                    "pnl": float(pl) if pl else None,
                }
            byd[r["date"]].append(entry)

    days = []
    for d in sorted(byd):
        g = byd[d]
        day_bets = [e for e in g if e["record"]["action"] == "BET"]
        days.append({
            "date": d,
            "flatPnl": round(sum(payout(e["record"]["odds"]) if e["record"]["win"]
                                 else -1.0 for e in day_bets), 2),
            "simPnl": round(sum(e["record"]["pnl"] for e in day_bets), 2),
            "simBankAfter": (round(bank_after[d], 2) if d in bank_after else None),
            "flagged": sum(1 for e in g if "ledger" in e),
            "placed": sum(1 for e in g if e.get("ledger", {}).get("placed")),
            "bet": len(day_bets),
            "games": g,
        })

    return {
        "label": label,
        "priceFill": price_fill,
        "from": date_from, "to": date_to,
        **head,
        "selectedBets": len(bets),
        "droppedZeroStake": len(dropped),
        "droppedFlatPnl": round(dropped_flat, 2),
        # The compounded bankroll is a SIMULATION -- it is not money that
        # was ever staked. Nested and labelled so it cannot masquerade as
        # the headline again.
        "sim": {
            "startBank": START,
            "finalBank": round(bank, 2),
            "profit": round(bank - START, 2),
            "maxDrawdownPct": round(mdd, 2),
            "kellyFraction": tracker.KELLY_FRACTION,
            # When the bank compounds ~10x, the 10%-per-bet cap means late
            # stakes are enormous in absolute units. Reporting the largest
            # single stake is the honest way to show what compounding
            # actually asks of the operator.
            "largestStake": round(max((b.get("stake", 0.0) for b in staked),
                                      default=0.0), 2),
            "medianStake": round(st.median([b.get("stake", 0.0) for b in staked]), 2),
        },
        # The no-hindsight lower bound, always shown beside the headline.
        "floor": (dict(floor, sim={"finalBank": round(wf_bank, 2),
                                   "profit": round(wf_bank - START, 2),
                                   "maxDrawdownPct": round(wf_mdd, 2),
                                   "largestStake": round(max(
                                       (b.get("stake", 0.0) for b in wf_staked),
                                       default=0.0), 2)})
                  if floor else None),
        "monthly": monthly,
        "days": days,
    }


def _f(v):
    try:
        return float((v or "").strip())
    except (ValueError, AttributeError):
        return None


def _i(v):
    try:
        return int(float((v or "").strip()))
    except (ValueError, AttributeError):
        return None


def load_ledger():
    """CSV rows in file order, so ledger[row['rid']] is an exact join.

    Keying on (date, away, home) collided on doubleheaders and on one
    mislabelled pair (2026-06-17 SF@ATL, both legs marked game 1), which
    made the day view show a bet twice and double its day total.
    """
    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> int:
    rows, _ = load_season()
    ledger = load_ledger()
    last_date = max(r["date"] for r in rows)

    # HEADLINE: the calibrator the live system actually scores with.
    shipped = ProbCalibrator.load(ROOT / "data" / "calibration_v2.json")
    dep = [shipped.predict(r["raw"]) for r in rows]
    # FLOOR: refit at each date from strictly earlier games only.
    wf = walk_forward_probs(rows)

    projected = make_record(rows, dep, wf, ledger,
                            label="Projected profit", price_fill=FILL,
                            date_from=min(r["date"] for r in rows),
                            date_to=last_date)
    real = make_record(rows, dep, wf, ledger,
                       label="Real profit", price_fill=None,
                       date_from=REAL_START, date_to=last_date)

    out = {
        "generatedUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "headlineMethod": ("the model that is live right now -- the shipped "
                           "calibrator and the current gate -- replayed over "
                           "every game of the season"),
        "floorMethod": ("no-hindsight check: at each date the calibrator is "
                        "refit using only games played before that date"),
        "caveat": ("the shipped calibrator was fit on 2025+2026 data, so it has "
                   "already seen these outcomes; the model weights are the fixed "
                   "2026-05-26 refit (trained through 2026-05-11), which makes "
                   "bets on or before 2026-05-11 partially in-sample"),
        "gates": {"yrfi": P._LR_STRONG_YRFI_P, "nrfi": NRFI_GATE},
        "kellyFraction": tracker.KELLY_FRACTION,
        "startBank": START,
        "realStart": REAL_START,
        "projected": projected,
        "real": real,
    }

    dest = ROOT / "data" / "season_record.json"
    # Atomic write: a cron tick reading this mid-write would otherwise get
    # truncated JSON and the card would silently vanish.
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    print(f"wrote {dest.relative_to(ROOT)} ({dest.stat().st_size // 1024} KB)")
    for r in (projected, real):
        if not r:
            continue
        fl = r.get("floor") or {}
        print(f"  {r['label']:<18} HEADLINE {r['wins']}W-{r['losses']}L "
              f"({100*r['hitRate']:.1f}%) edge {r['edgePts']:+.1f}pts "
              f"flat {r['flatProfit']:+.2f}u  |  floor {fl.get('wins')}W-"
              f"{fl.get('losses')}L flat {fl.get('flatProfit')}u  |  "
              f"sim bank {r['sim']['finalBank']:.0f}u maxDD "
              f"{r['sim']['maxDrawdownPct']:.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
