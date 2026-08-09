#!/usr/bin/env python3
"""tools/lock_commit.py

T8.18 PART 2 -- commit a STRONG pick when its lock window opens, even
if DraftKings never re-quoted it.

THE BUG THIS CLOSES.  A pick's stake used to be computed in exactly one
place: `tracker._apply_odds_to_row`, which only runs when a NEW DK price
arrives.  `units_risked` / `edge_on_pick` / `market_*_odds` are on
`log_picks`' always-preserve list, so a predict tick never touched them,
and the T2.25 probability freeze is gated on `bet_placed=="Y"` which
under T2.58 only happens inside the 60-minute lock window.  Net effect:
PRE-LOCK the probability moved freely and the stake could not follow.
Three git-history-verified victims --

    2026-08-09 LAD@ARI  p 0.5831 -> 0.6288 pre-lock; stake stayed 2u, rule says 5u
    2026-08-02 DET@OAK  p 0.6994 -> 0.6012 pre-lock; stake stayed 7u, rule says 1u
    2026-08-04 SD@ARI   p 0.7128 -> 0.6873 pre-lock; stake stayed 9u, rule says 8u

-- and on all three `bet_placed` only became "Y" AFTER first pitch, via
`tools/end_of_day_check.py`'s orphan heal, because no price arrived in
the window at all.  PART 1 (`tracker._rederive_pre_lock_stake`) keeps the
stake tracking the model BEFORE the lock.  This is the other half: the
row still has to actually COMMIT at T-60 without waiting on a price that
may never come.

WHY THIS IS A SEPARATE SUBPROCESS AND NOT A STEP INSIDE `log_picks`.
Predict runs BEFORE import-odds on both hosts (GitHub Actions' predict
block and Railway's `workers/predictor_loop.cycle`).  Committing inside
`log_picks` would therefore set `bet_placed="Y"` before the price lands,
and `_apply_odds_to_row`'s T2.23 early-return ("once bet_placed=Y the
captured price is frozen") would then DISCARD the very price that just
arrived -- measured at 64% of STRONG rows.  So the commit has to run
after import-odds, which means after that subprocess exits, which means
it is its own subprocess.

OFF BY DEFAULT.  `NRFI_LOCK_COMMIT=enabled` activates it; anything else
(including unset) is a silent no-op that still exits 0.  Same pattern as
`PREDICTOR_SCRAPE_DK` and `NRFI_STAKE_REDERIVE`: a new writer on the
money path earns its way on, it is not switched on by a deploy.

SOFT-FAIL BY CONTRACT.  This tool ALWAYS exits 0.  It is wired into two
crons whose real job is predicting and grading; a bad night here must
degrade to "no commit happened" and never to "the cron went red".

Usage:
  python tools/lock_commit.py                      # today's ET slate
  python tools/lock_commit.py --date 2026-08-09    # a specific slate
  python tools/lock_commit.py --dry-run            # report only, no writes
  python tools/lock_commit.py --season 2026        # override the ledger year
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

ET = ZoneInfo("America/New_York")

# Grades that mean "this row is finished" -- matching
# `tracker._pick_is_locked` and `tracker._rederive_pre_lock_stake`
# exactly, POSTPONED and SUSPENDED included.  A postponed row can be
# re-graded if the game resumes (T1.5), but it must not be committed
# and telegrammed in the meantime.
TERMINAL_GRADES = ("WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED")

# Set by tools/apply_cluster_demotion.py on rows the operator has
# deliberately taken off the board.  Importing the constant from that
# module would drag its json/config machinery in for one string.
DEMOTION_LABEL_PREFIX = "PASS - Cluster demotion"


def _enabled() -> bool:
    """The env gate.  Default "skip"; only the literal "enabled" arms it."""
    return os.environ.get("NRFI_LOCK_COMMIT", "skip").strip().lower() == "enabled"


def _picked_side_odds(row: dict) -> str:
    """The captured DK price on the side we are actually betting."""
    side = (row.get("pick_side") or "").strip().upper()
    col = ("market_nrfi_odds" if side == "NRFI"
           else "market_yrfi_odds" if side == "YRFI" else "")
    return (row.get(col) or "").strip() if col else ""


def _is_candidate(row: dict, iso_date: str) -> bool:
    """Every filter, and they ALL run before any sizing call (rule R3 in
    `tracker.kelly_reset_daily_committed`): sizing a row and then skipping
    it leaks the day's risk budget to a stake nobody ever published."""
    if (row.get("date") or "").strip() != iso_date:
        return False
    if (row.get("pick_strength") or "").strip().upper() != "STRONG":
        return False
    if (row.get("pick_side") or "").strip().upper() not in ("NRFI", "YRFI"):
        return False
    if (row.get("bet_placed") or "").strip().upper() == "Y":
        return False        # already committed -- idempotent, and T2.23 freezes it
    if (row.get("graded_result") or "").strip().upper() in TERMINAL_GRADES:
        return False
    if (row.get("pick_label") or "").strip().startswith(DEMOTION_LABEL_PREFIX):
        return False        # deliberately demoted; leave it demoted

    gtet = (row.get("game_time_et") or "").strip()
    # Doubleheader Game 2 / TBD rows carry a placeholder time ("After
    # Game 1"), so there is no lock instant to be inside of.  Never
    # commit one -- same refusal as T-V21-2026-05-06e, where the old
    # "always lockable" fallback was stamping bet_placed=Y on DH-2 rows
    # 6-12 hours before Game 1 had even finished.
    if tracker._parse_game_time_et(gtet, iso_date) is None:
        return False
    if not tracker._is_inside_lock_window(gtet, iso_date):
        return False        # pre-lock: the verdict is still advisory

    # THE THIRD LOCK PREDICATE IS THE ACTUAL FIX, and it is not
    # redundant with the one above.  `_is_inside_lock_window` is
    # UNBOUNDED ABOVE -- its own docstring says it returns True from
    # T-60 onward "(or after start, including post-game)".  Without this
    # guard, any ungraded row whose game is already over -- grading
    # lagging behind a late west-coast finish, or a POSTPONED row that
    # got cleared back to blank -- would be committed, SIZED against
    # tonight's budget, and TELEGRAMMED to the operator as a bet to go
    # place on a game that has finished.
    if tracker._game_has_started(gtet, iso_date):
        return False

    # No captured price on the picked side means nothing to size against.
    # Never synthesize one: fabricated odds are fabricated CLV and
    # fabricated P&L (CLAUDE.md, money rules).  `end_of_day_check`'s
    # orphan path plus `_notify_strong_orphan_no_odds_telegram` already
    # own the "STRONG bet with no price" case.
    if not _picked_side_odds(row):
        return False
    return True


# Columns this tool is allowed to move.  Snapshotted before/after so the
# Supabase mirror carries exactly the rows that actually changed.
_TOUCHED_COLS = (
    "bet_placed", "units_risked", "profit_loss_units",
    "implied_nrfi_prob", "implied_yrfi_prob",
    "edge_nrfi", "edge_yrfi", "edge_on_pick",
)


def _snapshot(row: dict) -> tuple:
    return tuple((row.get(c) or "") for c in _TOUCHED_COLS)


def _rank_key(row: dict) -> tuple:
    """Sort key: BEST BET FIRST.

    `kelly_stake_units`' own docstring names first-come-first-served
    allocation as its known limitation ("later picks are trimmed or get 0
    even if they were the stronger plays") and best-first as the fix.
    Using `_top_pick_rank_tuple` -- the same ordering the №1 rule and
    `dashboard/lib/top-pick-rank.ts` use -- means the daily budget and
    the headline pick can never disagree about which play is the best one
    on the board.

    A row whose `nrfi_prob` will not parse sorts LAST: it cannot be sized
    either (Kelly needs the probability), so it must not be allowed to
    take budget ahead of a real bet.
    """
    side = (row.get("pick_side") or "").strip().upper()
    name = (f"{(row.get('away_team') or '').strip().upper()}"
            f"@{(row.get('home_team') or '').strip().upper()}")
    try:
        p = float(row.get("nrfi_prob"))
    except (TypeError, ValueError):
        return (1, 1.0, 1.0, name)
    odds_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    return (0,) + tracker._top_pick_rank_tuple(side, p, row.get(odds_col), name)


def _describe(row: dict) -> str:
    return (f"{(row.get('away_team') or '').strip().upper()}"
            f"@{(row.get('home_team') or '').strip().upper()} "
            f"{(row.get('game_time_et') or '').strip()} "
            f"{(row.get('pick_side') or '').strip().upper()}")


def run(iso_date: str, season: int, dry_run: bool) -> int:
    path = tracker._csv_path(season)
    rows = tracker._read_rows(path)
    if not rows:
        print(f"[lock-commit] date={iso_date} season={season} "
              f"scanned=0 candidates=0 committed=0 refused=0 "
              f"no_budget=0 unpriceable=0 notify_calls=0 dry_run={dry_run} "
              f"(ledger empty or missing: {path.name})")
        return 0

    # The whole slate in memory, used two ways below: as the candidate
    # pool, and as the №1 gate's rival view.
    slate = [r for r in rows if (r.get("date") or "").strip() == iso_date]
    candidates = [r for r in slate if _is_candidate(r, iso_date)]
    candidates.sort(key=_rank_key)
    n_candidates = len(candidates)      # reported even if the loop is skipped

    committed: list[dict] = []
    touched:   list[dict] = []
    n_refused = n_no_budget = n_unpriceable = 0

    # KELLY IS DISABLED -> THERE IS NOTHING THIS TOOL CAN DO.  We call
    # `_size_row_stake` in PROJECTION MODE (units_strong=None), which by
    # contract has no flat fallback: with Kelly off there is no stake to
    # write and the row would be left untouched.  Bail before the loop
    # rather than iterate to no effect.
    if candidates and not tracker.KELLY_ENABLED:
        print("[lock-commit] Kelly sizing is disabled (NRFI_KELLY_ENABLED); "
              "this tool has no flat fallback by design -- skipping.")
        candidates = []

    # RULE R2: ONE reset, HERE, before the first row -- never per row.
    # A per-row reset re-reads the ledger and hands every row the full
    # 15u/day budget, which silently stops the cap binding at all.  The
    # batch epoch this bumps is also what LETS `_size_row_stake`'s commit
    # branch pass `game_date`; without it `kelly_stake_units` raises.
    # Called even under --dry-run for exactly that reason -- a dry run
    # must price rows the same way a real one does.
    if candidates:
        tracker.kelly_reset_daily_committed()

    for row in candidates:
        before = _snapshot(row)

        # Edges first, from the price the row ALREADY carries.  We do not
        # re-price: market_*_odds / odds_captured_at / opened_* are never
        # touched here, so T2.23 capture semantics and CLV are unaffected.
        if not tracker._apply_edges_to_row(row,
                                           row.get("market_nrfi_odds", ""),
                                           row.get("market_yrfi_odds", "")):
            # Probabilities unparseable.  Kelly could not price this row
            # either, so skip it rather than hand it to the sizer (R3).
            n_unpriceable += 1
            print(f"[lock-commit]   SKIP  {_describe(row)} -- probabilities unparseable")
            if _snapshot(row) != before:
                touched.append(row)
            continue

        try:
            # EXACTLY ONE sizing call per row, and the only one in this
            # tool.  inside_lock=True is what routes `game_date` through
            # to `kelly_stake_units` (rule R1) -- this IS the allocation.
            # units_strong=None keeps us in projection mode so a row Kelly
            # cannot price is left exactly as found instead of being
            # stamped with a flat fabricated stake.
            #
            # KNOWN GAP, and it is in `_size_row_stake`, not here.  Its
            # docstring promises "at COMMIT a refusal is written as a
            # literal 0.00, never blank" -- but the `projection_mode`
            # branch is tested BEFORE the `inside_lock` branch and has no
            # `and not inside_lock` guard, so with units_strong=None a
            # zero-stake refusal still takes the projection path and keeps
            # the 0.50 floor.  Net effect from here is a NO-OP on refusals
            # (the row stays exactly as PART 1's pre-lock projection left
            # it), not a regression -- but the honest 0.00 that would stop
            # `tools/end_of_day_check.py` reading `N + units>0` as a
            # pre-lock pending does not get written.  Do not paper over it
            # in this file: `_size_row_stake` is THE ONLY WRITER of the
            # (bet_placed, units_risked) pair and must stay that way.
            tracker._size_row_stake(row, season=season, inside_lock=True,
                                    units_lean=0.0, units_strong=None)
        except tracker.KellyBudgetUnavailable as exc:
            # The day's committed exposure could not be read, so the
            # remaining risk budget is unknown.  Sizing against an unknown
            # budget is how you allocate another 28u against a published
            # 15u/day cap -- leave the row EXACTLY as found.
            n_no_budget += 1
            print(f"[lock-commit]   SKIP  {_describe(row)} -- budget unreadable ({exc})",
                  file=sys.stderr)
            continue

        row["profit_loss_units"] = tracker._calc_pnl(row)

        after = _snapshot(row)
        if after != before:
            touched.append(row)

        if (row.get("bet_placed") or "").strip().upper() == "Y":
            committed.append(row)
            print(f"[lock-commit]   COMMIT {_describe(row)} "
                  f"{_picked_side_odds(row)} {row.get('units_risked')}u "
                  f"edge={row.get('edge_on_pick')}")
        else:
            # Kelly declined: either the model does not beat the market's
            # implied probability, or the 15u/day cap left no room.  Both
            # are deliberate refusals, and both are worth one log line --
            # otherwise the operator meets this decision only as a STRONG
            # pick that quietly never appeared on the board.
            n_refused += 1
            print(f"[lock-commit]   REFUSE {_describe(row)} "
                  f"{_picked_side_odds(row)} "
                  f"stake={(row.get('units_risked') or '')!r} "
                  f"(no positive-EV stake, or the daily cap left no room)")

    if dry_run:
        print(f"[lock-commit] date={iso_date} season={season} "
              f"scanned={len(slate)} candidates={n_candidates} "
              f"committed={len(committed)} refused={n_refused} "
              f"no_budget={n_no_budget} unpriceable={n_unpriceable} "
              f"notify_calls=0 dry_run=True (no CSV write, no Supabase, no Telegram)")
        return 0

    if touched:
        tracker._write_rows(path, rows)
        # Mirror EVERY row we moved, not just the commits.  A refusal
        # writes bet_placed="N" + units_risked="0.0"; if that never
        # reaches Supabase, `tools/sync_csv_from_supabase.py` pulls the
        # stale positive stake back over it on the next tick and the
        # board prints a stake for a pick the system refused (T8.17).
        try:
            tracker._mirror_picks_to_supabase(season, touched)
        except Exception as exc:    # noqa: BLE001 -- CSV is the source of truth
            print(f"[lock-commit]   [warn] Supabase mirror failed: {exc!r}",
                  file=sys.stderr)

    # NOTIFY AFTER THE WRITE, never before: a ping the operator acts on
    # must correspond to a stake that survived to disk.
    #
    # Reuse the EXISTING `strong_locked` event.  A brand-new event type
    # inherits `_DEDUP_WINDOW_M`'s 5-minute fallback, and with the Railway
    # loop cycling every 5 minutes that is precisely what published THE
    # BOARD three times to a paying Discord channel on 2026-08-06.
    # `strong_locked` is registered at 24h, keyed `strong_locked:{game_pk}`.
    #
    # `rivals` is the whole in-memory slate, NOT just this sweep's
    # candidate list.  The №1 gate skips any rival at bet_placed="N", so
    # the pre-batch copy on disk hides every row this pass just committed
    # -- but a candidates-only view has the opposite hole and it is worse:
    # rows that locked EARLIER TODAY are already bet_placed="Y" and are
    # therefore excluded from `candidates` entirely, so a late mediocre
    # pick would find no rival, crown itself №1, and fire a second "place
    # this bet" alert on a night that already had one.  The slate view has
    # neither hole.
    # `notify_calls` counts INVOCATIONS, not deliveries -- the №1 gate and
    # the 24h notifications_log dedup both live inside the notifier and
    # neither reports back.  Do not re-evaluate the gate here just to make
    # the number prettier: two copies of a rule is how the board and the
    # ledger start disagreeing about which play is the night's №1.
    notify_calls = 0
    for row in committed:
        try:
            tracker._notify_strong_locked_telegram(row, rivals=slate)
            notify_calls += 1
        except Exception as exc:    # noqa: BLE001 -- advisory, never fatal
            print(f"[lock-commit]   [warn] telegram failed for "
                  f"{_describe(row)}: {exc!r}", file=sys.stderr)

    print(f"[lock-commit] date={iso_date} season={season} "
          f"scanned={len(slate)} candidates={n_candidates} "
          f"committed={len(committed)} refused={n_refused} "
          f"no_budget={n_no_budget} unpriceable={n_unpriceable} "
          f"notify_calls={notify_calls} dry_run=False")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--date", help="ISO ET slate date (YYYY-MM-DD); default = today ET.")
    p.add_argument("--season", type=int, default=None,
                   help="Season override (defaults to the year of --date).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would commit; write nothing, mirror "
                        "nothing, send nothing.")
    args = p.parse_args()

    if not _enabled():
        print("[lock-commit] skipped (set NRFI_LOCK_COMMIT=enabled to activate)")
        return 0

    iso_date = args.date or datetime.now(ET).strftime("%Y-%m-%d")
    season   = args.season or int(iso_date[:4])

    try:
        return run(iso_date, season, args.dry_run)
    except Exception as exc:    # noqa: BLE001
        # ALWAYS 0.  This is a soft-fail step in two crons whose real job
        # is predicting and grading; a failure here must degrade to "no
        # commit happened", never to a red workflow.
        print(f"[lock-commit] FAILED ({exc!r}); continuing", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
