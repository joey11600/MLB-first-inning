#!/usr/bin/env python3
"""tools/heal_2026_08_13_split_brain_stake.py

ONE-SHOT stake correction for 2026-08-13 CIN@CWS (game_pk 824561),
STRONG YRFI @ -120, the night's No.1.

WHAT WENT WRONG
===============
The stake and the published probability were written by DIFFERENT HOSTS
running the model independently, and they disagreed:

    GitHub Actions  15:58:04Z run ->  YRFI 66.87%   (published everywhere)
    Railway         16:11:31Z run ->  YRFI 58.6%    (sized the bet)

Railway is the only host that can reach DraftKings (PREDICTOR_SCRAPE_DK),
so Railway owns `bet_placed` / `units_risked` / `edge_*`.  Quarter-Kelly on
its own 0.586 @ -120 = 2.25u -> rounds to 2u.  Quarter-Kelly on the
PUBLISHED 0.6687 @ -120 = 6.78u -> 7u.  The row therefore shipped with
GHA's probability sitting next to Railway's stake, which is why
`tools/pl_calc.py` and `tools/stake_drift.py` both flag it:

    ledger= 2.00u   rule= 7.00u   (-5.00u)

This is NOT the T8.18 pre-lock freeze (PART 1 is enabled and re-derived
correctly -- to 2u, every cycle, because it re-reads the probability on
the host it runs on).  It is a new class: split-brain between the sizing
host and the publishing host.  See docs/proposals/one_source_stake.md.

WHY THIS IS A DELIBERATE, JOURNALED ACT
=======================================
`tools/stake_drift.py` reports and STOPS by design -- "rewriting a locked
stake is itself a money-path write, from two concurrent hosts, with no
version column".  Operator made the call on 2026-08-13 to correct this row
to the rule (7u) rather than preserve the published 2u, and to re-issue the
Discord announcement to match.  That is the opposite of the 2026-08-09
decision recorded in data/stake_drift_exempt.csv for DET@OAK / SD@ARI,
where the published number was preserved -- note those two were preserved
because correcting them would have COST ~4.97u, whereas this one was
under-staked on a winner.  Both directions are the operator's call; this
file records which was taken and why.

WHAT IT CHANGES
===============
Everything is derived from SHIPPED functions -- no arithmetic is written
by hand:

  units_risked      2  -> tracker.kelly_stake_units(yrfi_prob, -120) = 7
  edge_nrfi/yrfi/on_pick  recomputed by tracker._apply_edges_to_row from
                    the row's CURRENT probabilities.  T8.18's rule is that
                    all three edge columns move WITH the stake or none do:
                    leaving edge_on_pick=0.0409 (which encodes p=0.5864)
                    next to a 7u stake (which encodes p=0.6687) rebuilds
                    exactly the inconsistency this heal exists to remove.
  profit_loss_units recomputed by tracker._calc_pnl once the row is graded.

The heal REFUSES to run unless the rule stake is exactly the 7.0u that was
diagnosed and shown to the operator -- if the model or price has moved
since, that is a different decision and needs a fresh look.

Both stores are written: the CSV (source of truth) and Supabase (what the
dashboard reads) via `patch_picks`, the documented primitive for a
data-correction script -- it sends ONLY the named columns, so the grade and
odds Supabase already holds are left untouched.  A full `mirror_picks`
here would blank whatever the local CSV happens to be missing, which is
the 2026-05-05 wipe.

Idempotent: a second run sees the healed shape and no-ops.

Usage:
  python tools/heal_2026_08_13_split_brain_stake.py --dry-run   # report only
  python tools/heal_2026_08_13_split_brain_stake.py             # apply
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_dotenv() -> None:
    """Populate SUPABASE_* from .env when the shell doesn't already have
    them.  setdefault, so a real environment always wins."""
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

import tracker                              # noqa: E402
from db import supabase_writer              # noqa: E402

ISO_DATE  = "2026-08-13"
GAME_PK   = "824561"
SEASON    = 2026
SIDE      = "YRFI"
ODDS_COL  = "market_yrfi_odds"
PROB_COL  = "yrfi_prob"

# The stake the operator reviewed and approved.  If the rule no longer
# produces this, something moved and the heal must not proceed silently.
EXPECTED_RULE_STAKE = 7.0
EXPECTED_OLD_STAKE  = 2.0

PATCH_FIELDS = [
    "units_risked",
    "profit_loss_units",
    "edge_nrfi",
    "edge_yrfi",
    "edge_on_pick",
    "implied_nrfi_prob",
    "implied_yrfi_prob",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change; write nothing.")
    args = ap.parse_args()

    csv_path = tracker._csv_path(SEASON)
    rows = tracker._read_rows(csv_path)

    idx = next(
        (i for i, r in enumerate(rows)
         if r.get("date") == ISO_DATE
         and str(r.get("game_pk") or "").strip() == GAME_PK),
        None,
    )
    if idx is None:
        print(f"!! {ISO_DATE} game_pk={GAME_PK} not found in {csv_path}")
        return 1

    row = rows[idx]
    tag = f"{row.get('away_team')}@{row.get('home_team')} (pk={GAME_PK})"

    # ---- guards -----------------------------------------------------
    if (row.get("bet_placed") or "").strip().upper() != "Y":
        print(f"!! {tag}: bet_placed is not 'Y' -- this heal only corrects a "
              f"PLACED bet's size.  Aborting.")
        return 1
    if (row.get("pick_side") or "").strip().upper() != SIDE:
        print(f"!! {tag}: pick_side is {row.get('pick_side')!r}, expected "
              f"{SIDE!r}.  Aborting.")
        return 1

    try:
        old_stake = float((row.get("units_risked") or "").strip() or 0.0)
    except ValueError:
        old_stake = 0.0

    try:
        p_model = float((row.get(PROB_COL) or "").strip())
    except (TypeError, ValueError):
        print(f"!! {tag}: {PROB_COL} is unparseable -- aborting.")
        return 1

    odds = (row.get(ODDS_COL) or "").strip()
    if not odds:
        print(f"!! {tag}: no captured {ODDS_COL} -- aborting.")
        return 1

    # The rule stake, from the shipped sizer.  No game_date: this is a pure
    # function of (probability, price).  Passing game_date would consult the
    # daily budget, which already contains THIS row's 2u -- re-deriving a row
    # against a tally that includes itself is the double-count trap named in
    # tools/stake_drift.py's docstring.  The cap is not near binding today
    # (2u committed of 15u), so the pure figure is also the allocated one.
    rule_stake = tracker.kelly_stake_units(p_model, odds, season=SEASON)
    if rule_stake is None:
        print(f"!! {tag}: sizer returned None -- aborting.")
        return 1

    if abs(old_stake - EXPECTED_RULE_STAKE) < 1e-9:
        print(f"   {tag}: already at {EXPECTED_RULE_STAKE}u -- CSV no-op, "
              f"re-patching Supabase for safety.")
    elif abs(old_stake - EXPECTED_OLD_STAKE) > 1e-9:
        print(f"!! {tag}: ledger stake is {old_stake}u, expected "
              f"{EXPECTED_OLD_STAKE}u.  Something else has written this row. "
              f"Aborting -- re-diagnose before healing.")
        return 1

    if abs(rule_stake - EXPECTED_RULE_STAKE) > 1e-9:
        print(f"!! {tag}: rule now says {rule_stake}u, but this heal was "
              f"approved for {EXPECTED_RULE_STAKE}u (p={p_model} @ {odds}). "
              f"Aborting -- the inputs moved.")
        return 1

    # ---- apply ------------------------------------------------------
    old_edge  = row.get("edge_on_pick")
    old_pl    = row.get("profit_loss_units")

    if not tracker._apply_edges_to_row(
            row, row.get("market_nrfi_odds", ""), row.get("market_yrfi_odds", "")):
        print(f"!! {tag}: edge recompute failed (unparseable probs) -- aborting.")
        return 1

    row["units_risked"]      = tracker._fmt(rule_stake, 2)
    row["profit_loss_units"] = tracker._calc_pnl(row)

    graded = (row.get("graded_result") or "").strip() or "(not yet graded)"
    print(f"   {tag}  STRONG {SIDE} @ {odds}   grade={graded}")
    print(f"     p (published)      : {p_model}")
    print(f"     units_risked       : {old_stake}  ->  {row['units_risked']}")
    print(f"     edge_on_pick       : {old_edge}  ->  {row['edge_on_pick']}")
    print(f"     profit_loss_units  : {old_pl or '(blank)'}  ->  "
          f"{row['profit_loss_units'] or '(blank -- fills at grade)'}")

    if args.dry_run:
        print("\n[dry-run] nothing written.")
        return 0

    tracker._write_rows(csv_path, rows)
    print(f"\n   CSV written: {csv_path}")

    try:
        tracker._record_pick_change(
            iso_date    = ISO_DATE,
            game_pk     = GAME_PK,
            away_team   = (row.get("away_team") or "").upper(),
            home_team   = (row.get("home_team") or "").upper(),
            game_time   = (row.get("game_time_et") or ""),
            old_label   = f"STRONG YRFI · {old_stake:g}u",
            new_label   = (f"STRONG YRFI · {rule_stake:g}u · STAKE HEAL "
                           f"(heal_2026_08_13_split_brain_stake)"),
            captured_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        print("   pick_changes.csv journalled")
    except Exception as exc:            # noqa: BLE001 -- journal is advisory
        print(f"   journal write failed (non-fatal): {exc!r}", file=sys.stderr)

    n = supabase_writer.patch_picks([row], SEASON, PATCH_FIELDS)
    if n:
        print(f"   Supabase patched ({n} row, fields={PATCH_FIELDS})")
    else:
        print("!! Supabase patch wrote 0 rows -- the dashboard still shows the "
              "OLD stake.  Check SUPABASE_URL / SUPABASE_SERVICE_KEY and re-run.",
              file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
