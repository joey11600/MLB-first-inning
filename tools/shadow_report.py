#!/usr/bin/env python3
"""
tools/shadow_report.py -- the live model and the SHADOW model, side by side.

WHAT THIS IS (2026-09-04).  Every predict tick scores each game twice: once
with the live model (that pick is published, staked and alerted) and once
with the shadow candidate (recorded in the ledger's shadow_* columns, never
published, never bet).  This is the nightly reading of those columns: what
each model would have bet, how it did, and -- the part that matters -- the
PAIRED comparison on the same nights at the same prices, which strips out the
luck of which nights each model happened to bet.

THREE LINES PER MODEL, on purpose:
  booked      the live model's real stakes and P&L from the ledger (only the
              live model has this; it is the number the dashboard shows)
  same-rule   BOTH models sized by one formula -- quarter Kelly on the model's
              own calibrated probability at the captured YRFI price, 10u cap,
              whole units with a 0.5u floor -- and NO daily cap or lock-order
              effects, so the two are compared on identical sizing
  flat 1u     the hit rate priced without any sizing at all

The shadow's "would-be" stake is computed HERE, not in the money path: the
ledger stores the shadow's probability and verdict, and this script prices
them.  So nothing in tracker.py's sizing block knows the shadow exists.

Read this with the 2026-08-13 fortnight review in mind: ~20 nights cannot
settle which model is better.  What accumulates here is operational proof
(the feature computes live, the candidate's picks look sane) and paired
rows for the offseason decision.

Writes data/diagnostics/shadow_report.json (the dashboard can read it) and
prints the same to the workflow log.  Exits 0 always; a report cannot break
the cron.

CLI
    python tools/shadow_report.py                 # since the first shadow row
    python tools/shadow_report.py --since 2026-09-05
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "picks_2026.csv"
OUT = ROOT / "data" / "diagnostics" / "shadow_report.json"


def _f(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def payout(odds: float) -> float:
    return odds / 100.0 if odds > 0 else 100.0 / -odds


def implied(odds: float) -> float:
    return -odds / (-odds + 100.0) if odds < 0 else 100.0 / (odds + 100.0)


def _rhe(x: float, nd: int = 0) -> float:
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_EVEN))


def stake_same_rule(p_win: float, odds: float) -> float:
    """dashboard/lib/kelly-sim.stakeUnitsFor: quarter Kelly, 10u cap, whole
    units with a 0.5u floor.  Per-bet only -- no daily cap."""
    b = payout(odds)
    if not (b > 0) or not (0.0 < p_win < 1.0):
        return 0.0
    f = min(max((p_win * b - (1.0 - p_win)) / b, 0.0) * 0.25, 0.10)
    s = f * 100.0
    if s < 0.10:
        return 0.0
    s = _rhe(s, 2)
    r = _rhe(s / 1.0) * 1.0
    if r < 0.5:
        r = 0.5
    if r > 10.0:
        r = s
    return _rhe(r, 2)


def load_rows(since: str | None) -> tuple[list[dict], str | None]:
    with open(LEDGER, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    shadow_dates = sorted({r["date"][:10] for r in rows if (r.get("shadow_model") or "").strip()})
    if not shadow_dates:
        return [], None
    start = since or shadow_dates[0]
    return [r for r in rows if r["date"][:10] >= start], start


def side_of(label: str) -> tuple[str, str]:
    """('STRONG'|'LEAN'|'PASS', 'YRFI'|'NRFI'|'')."""
    s = (label or "").strip().upper()
    if s.startswith("STRONG"):
        return "STRONG", ("YRFI" if "YRFI" in s else "NRFI")
    if s.startswith("LEAN"):
        return "LEAN", ("YRFI" if "YRFI" in s else "NRFI")
    return "PASS", ""


def summarize(bets: list[dict]) -> dict:
    n = len(bets)
    w = sum(1 for b in bets if b["won"])
    return {
        "bets": n, "W": w, "L": n - w, "hit": (w / n) if n else None,
        "stated": (sum(b["p"] for b in bets) / n) if n else None,
        "break_even": (sum(implied(b["odds"]) for b in bets) / n) if n else None,
        "flat_units": sum((payout(b["odds"]) if b["won"] else -1.0) for b in bets),
        "same_rule_units": sum(b["pnl_same_rule"] for b in bets),
        "same_rule_staked": sum(b["stake_same_rule"] for b in bets),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="first slate date to include (default: first row with a shadow value)")
    ap.add_argument("--json", default=str(OUT))
    args = ap.parse_args()

    rows, start = load_rows(args.since)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not rows:
        print("shadow_report: no ledger rows carry a shadow value yet -- nothing to compare.")
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps({"as_of": now, "since": None, "graded_rows": 0}, indent=1), encoding="utf-8")
        return 0

    graded = [r for r in rows
              if (r.get("graded_result") or "").strip().upper() in ("WIN", "LOSS", "PASS")
              and _f(r.get("fi_total_runs")) is not None]
    live_bets: list[dict] = []
    shadow_bets: list[dict] = []
    agree = defaultdict(int)
    for r in graded:
        y_run = (_f(r.get("fi_total_runs")) or 0.0) > 0
        odds = _f(r.get("market_yrfi_odds"))
        live_lbl = r.get("pick_label") or ""
        sh_lbl = r.get("shadow_pick_label") or ""
        ls, lside = side_of(live_lbl)
        ss, sside = side_of(sh_lbl)
        agree[f"live={ls}{('/' + lside) if lside else ''} shadow={ss}{('/' + sside) if sside else ''}"] += 1

        game = f"{r.get('away_team')}@{r.get('home_team')}"
        if (r.get("bet_placed") or "").strip().upper() == "Y" and lside == "YRFI" and odds is not None:
            p = 1.0 - (_f(r.get("nrfi_prob")) or 0.5)
            st = stake_same_rule(p, odds)
            live_bets.append({
                "date": r["date"][:10], "game": game, "p": p, "odds": odds, "won": y_run,
                "stake_booked": _f(r.get("units_risked")) or 0.0,
                "pnl_booked": _f(r.get("profit_loss_units")) or 0.0,
                "stake_same_rule": st, "pnl_same_rule": (st * payout(odds) if y_run else -st),
            })
        if ss == "STRONG" and sside == "YRFI" and odds is not None:
            sp = _f(r.get("shadow_nrfi_prob"))
            if sp is not None:
                p = 1.0 - sp
                st = stake_same_rule(p, odds)
                shadow_bets.append({
                    "date": r["date"][:10], "game": game, "p": p, "odds": odds, "won": y_run,
                    "stake_same_rule": st, "pnl_same_rule": (st * payout(odds) if y_run else -st),
                })

    def no1(bets: list[dict]) -> dict[str, dict]:
        """The night's No.1 = highest p(YRFI), better price breaking ties."""
        best: dict[str, dict] = {}
        for b in bets:
            cur = best.get(b["date"])
            if cur is None or (b["p"], -implied(b["odds"])) > (cur["p"], -implied(cur["odds"])):
                best[b["date"]] = b
        return best

    live_no1, shadow_no1 = no1(live_bets), no1(shadow_bets)
    nights = sorted(set(live_no1) | set(shadow_no1))
    paired = []
    for d in nights:
        a, b = live_no1.get(d), shadow_no1.get(d)
        paired.append({
            "date": d,
            "live": (f"{a['game']} {'W' if a['won'] else 'L'} {a['pnl_same_rule']:+.2f}u" if a else "-"),
            "shadow": (f"{b['game']} {'W' if b['won'] else 'L'} {b['pnl_same_rule']:+.2f}u" if b else "-"),
            "same_game": bool(a and b and a["game"] == b["game"]),
        })
    both = [d for d in nights if d in live_no1 and d in shadow_no1]

    def no1_sum(sel: dict[str, dict], only: list[str] | None = None) -> dict:
        xs = [v for k, v in sel.items() if only is None or k in only]
        return {"nights": len(xs), "W": sum(1 for x in xs if x["won"]),
                "hit": (sum(1 for x in xs if x["won"]) / len(xs)) if xs else None,
                "same_rule_units": sum(x["pnl_same_rule"] for x in xs)}

    tonight = [r for r in rows if not (r.get("graded_result") or "").strip()]
    today = {
        "date": max((r["date"][:10] for r in tonight), default=None),
        "live_strong": [f"{r.get('away_team')}@{r.get('home_team')}" for r in tonight if side_of(r.get("pick_label") or "") == ("STRONG", "YRFI")],
        "shadow_strong": [f"{r.get('away_team')}@{r.get('home_team')}" for r in tonight if side_of(r.get("shadow_pick_label") or "") == ("STRONG", "YRFI")],
    }

    report = {
        "as_of": now, "since": start, "graded_rows": len(graded),
        "shadow_model": next((r.get("shadow_model") for r in rows if (r.get("shadow_model") or "").strip()), ""),
        "live":   {**summarize(live_bets), "booked_units": sum(b["pnl_booked"] for b in live_bets),
                   "booked_staked": sum(b["stake_booked"] for b in live_bets)},
        "shadow": summarize(shadow_bets),
        "no1": {"live": no1_sum(live_no1), "shadow": no1_sum(shadow_no1),
                "nights_both": len(both),
                "live_on_both": no1_sum(live_no1, both), "shadow_on_both": no1_sum(shadow_no1, both),
                "same_game_on_both": sum(1 for p in paired if p["same_game"])},
        "label_agreement": dict(sorted(agree.items(), key=lambda kv: -kv[1])),
        "paired_nights": paired,
        "tonight": today,
    }
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(json.dumps(report, indent=1), encoding="utf-8")

    def fmt(s: dict) -> str:
        if not s["bets"]:
            return "no bets"
        return (f"{s['bets']:>3} bets  {s['W']}-{s['L']}  hit {s['hit']:.3f}  stated {s['stated']:.3f}  "
                f"break-even {s['break_even']:.3f}  flat {s['flat_units']:+.2f}u  same-rule {s['same_rule_units']:+.2f}u "
                f"on {s['same_rule_staked']:.1f}u")

    print(f"SHADOW REPORT  since {start}  ({len(graded)} graded rows, shadow model {report['shadow_model']!r})")
    print(f"  live    {fmt(report['live'])}   booked {report['live']['booked_units']:+.2f}u on {report['live']['booked_staked']:.1f}u")
    print(f"  shadow  {fmt(report['shadow'])}")
    n = report["no1"]
    print(f"  No.1    live {n['live']['nights']} nights hit {n['live']['hit'] if n['live']['hit'] is None else round(n['live']['hit'], 3)} {n['live']['same_rule_units']:+.2f}u | "
          f"shadow {n['shadow']['nights']} nights hit {n['shadow']['hit'] if n['shadow']['hit'] is None else round(n['shadow']['hit'], 3)} {n['shadow']['same_rule_units']:+.2f}u")
    print(f"  PAIRED  {n['nights_both']} nights both had a No.1, same game on {n['same_game_on_both']}: "
          f"live {n['live_on_both']['W']}/{n['live_on_both']['nights']} {n['live_on_both']['same_rule_units']:+.2f}u  vs  "
          f"shadow {n['shadow_on_both']['W']}/{n['shadow_on_both']['nights']} {n['shadow_on_both']['same_rule_units']:+.2f}u")
    print("  label agreement:")
    for k, v in list(report["label_agreement"].items())[:8]:
        print(f"     {v:>4}  {k}")
    if paired:
        print("  by night:")
        for p in paired[-14:]:
            print(f"     {p['date']}  live {p['live']:<28} shadow {p['shadow']:<28} {'same' if p['same_game'] else ''}")
    if today["date"]:
        print(f"  tonight {today['date']}: live STRONG {today['live_strong'] or '-'}   shadow STRONG {today['shadow_strong'] or '-'}")
    print(f"  wrote {args.json}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:                    # noqa: BLE001 -- a report must not break the cron
        print(f"shadow_report failed: {e}", file=sys.stderr)
        raise SystemExit(0)
