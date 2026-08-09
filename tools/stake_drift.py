#!/usr/bin/env python3
"""tools/stake_drift.py -- T8.18 PART 3.  Does the published stake still
match the rule that was supposed to produce it?

WHY THIS EXISTS
===============
T8.18: a STRONG pick's stake was computed only inside
`tracker._apply_odds_to_row`, i.e. only when a NEW DraftKings price
arrived.  `units_risked` / `edge_on_pick` / `market_*_odds` are on
`log_picks`' always-preserve list, so a predict tick never touched them,
while the T2.25 probability freeze is gated on `bet_placed=="Y"` -- which
under T2.58 only happens inside the 60-minute lock window.  PRE-LOCK the
probability therefore moved freely and the stake could not follow.  Three
victims, all verified in git history:

    2026-08-09 LAD@ARI  p 0.5831 -> 0.6288 pre-lock; stake stayed 2u, rule 5u
    2026-08-02 DET@OAK  p 0.6994 -> 0.6012 pre-lock; stake stayed 7u, rule 1u
    2026-08-04 SD@ARI   p 0.7128 -> 0.6873 pre-lock; stake stayed 9u, rule 8u

Every one was found BY EYE, on the dashboard, days later.  Parts 1 and 2
stop it happening again; this part is the system checking itself, so a
recurrence surfaces the same night instead of the next time somebody
happens to squint at a board.

THE NAIVE INVARIANT IS UNIMPLEMENTABLE -- READ THIS BEFORE "SIMPLIFYING"
========================================================================
The obvious check, `units_risked == kelly_stake_units(p, odds)` per row,
does not work in EITHER form:

  * WITH `game_date`, `tracker._committed_on` seeds the day's exposure
    from the ledger -- which already contains the very row being
    re-derived.  The budget is spent before the first comparison, every
    row recomputes to 0, and 100% of any settled day flags.  (Measured:
    17 of 18 locked STRONG rows since 2026-07-30.)
  * WITHOUT `game_date` there is no daily cap, so every legitimately
    cap-trimmed row flags.  Real example: 2026-07-31 MIL@LAA stored 1.5u
    and recomputes to 5.0u -- AND 1.5 IS CORRECT.  It was trimmed to the
    room left under the 15u/day cap and kept exact by the
    never-round-up-through-a-cap guard in `kelly_stake_units`.

So the check is a PER-DAY REPLAY.  For each slate date: reset the tally,
take that date's STRONG `bet_placed="Y"` rows that carry a captured price
on the side we bet, sort them best-bet-first, re-derive each one WITH
`game_date` so the daily cap is live, and compare the resulting VECTOR
against the ledger's.

Because it passes `game_date` it MUST call
`tracker.kelly_reset_daily_committed()` once per day -- rule R2 of the
reset discipline, and the batch-epoch guard enforces it.

WHAT THE REPLAY CANNOT SEE, STATED PLAINLY
==========================================
On a day where the 15u cap BINDS, the budget is a shared resource and the
per-row split depends on the order it was handed out.  This replay
allocates best-bet-first, using `tools/lock_commit.py`'s own sort key.
The historical ledger was NOT written that way: `kelly_stake_units`
allocates first-come-first-served in whatever order rows are processed --
its own docstring names this as a KNOWN LIMITATION, and CHECK 7 of
`tools/verify_kelly_wiring.py` now measures it (a deliberately failing
assertion).  On a cap-bound day the two orders split the SAME total
differently.

Measured on the real ledger, 2026-07-31 -- a slate that hit the cap to the
unit.  Ledger 8 + 5 + 1.5 + 0.5 = 15.0; replay 8 + 5 + 2.0 + 0.0 = 15.0.
Both are correct allocations of one 15u budget.  Flagging those two rows
would be a false alarm, so a day that (a) is cap-bound in the replay and
(b) has a ledger total matching the replay total is reported as CAP-ORDER,
not as a violation.

This is mostly a HISTORY problem: once `lock_commit` is the writer, the
committing order and the replay order are the same function, and cap-bound
nights should reconcile row-for-row.  A CAP-ORDER note appearing on a
slate written by lock_commit means the two have drifted apart, and is
worth chasing.

THAT SUPPRESSION IS NOT FREE, and here is exactly what it costs: on a
cap-bound day the total is pinned to 15u whichever way the budget is
split, so a genuinely stale stake inside such a day can hide behind a
matching total.  It is deliberate.  The alternative -- one alarm every
cap-bound night -- gets the check ignored inside a week, which is worse
than a narrow blind spot on the ~13% of nights where the cap binds.  A
cap-bound day whose TOTALS disagree is still a violation, because then
the money at risk genuinely differs.

ERA FLOOR
=========
Only slates from `STAKE_DRIFT_ERA_FLOOR` are checked.  Before the
2026-07-30 unit re-basing the ledger was written under different sizing
rules, and re-deriving those rows against today's rule flags 319 of 370
locked STRONG rows.  A check that cries wolf on 86% of history is a check
nobody reads.

This script never writes to the ledger.  Pure read + report.

USAGE
=====
  python tools/stake_drift.py                    # from the era floor to today
  python tools/stake_drift.py --date 2026-08-02
  python tools/stake_drift.py --since 2026-08-01 --until 2026-08-09
  python tools/stake_drift.py --all              # ignore the era floor
  python tools/stake_drift.py --verbose          # print every compared row

Exit code 1 when any violation survives the exemptions, 0 otherwise --
same contract as `tools/pl_calc.py`'s P&L drift check.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Iterable, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tracker  # noqa: E402

# --------------------------------------------------------------------------
# Policy constants
# --------------------------------------------------------------------------

# The unit re-basing (1 unit = 1% of bank, operator 2026-07-30).  Rows
# before this were sized under rules that no longer exist, so re-deriving
# them proves nothing.  The operator may move this forward; moving it
# BACKWARDS re-imports 319 false alarms, so don't, without re-measuring.
STAKE_DRIFT_ERA_FLOOR = "2026-07-30"

# Stakes are whole units with a 0.5u floor (KELLY_STAKE_ROUNDING /
# KELLY_ROUNDED_FLOOR), so the smallest meaningful difference is 0.5.
# 0.005 is loose enough to absorb float formatting round-trips through
# CSV and Supabase and tight enough that a real drift cannot hide in it.
STAKE_DRIFT_TOLERANCE = 0.005

# tools/apply_manual_odds.py stamps this on any row it patches, and it
# writes a FLAT "1" into units_risked when the column was blank.  That is
# a deliberate operator repair, not a Kelly stake, so it can never match
# the replay.  Kept in sync by reading the constant from the tool itself
# where possible -- a literal here would silently rot if it were renamed.
try:                                                    # pragma: no cover
    from tools.apply_manual_odds import DEFAULT_SPORTSBOOK as MANUAL_ODDS_BOOK
except Exception:                                       # noqa: BLE001
    MANUAL_ODDS_BOOK = "DraftKings (manual)"

# THE ALLOCATION ORDER IS IMPORTED, NOT COPIED.  The replay is only
# meaningful if it hands out the daily budget in the SAME order the
# committing writer does; two independent implementations of "best bet
# first" that drift apart would turn this check into a noise generator on
# every cap-bound night.  `tools/lock_commit.py` owns the definition (it
# is the writer); we borrow it, and its demotion-label prefix with it.
#
# The fallbacks exist so a missing lock_commit downgrades the check rather
# than breaking `tools/reconcile.py`, which runs every five minutes on the
# money path.  They are a copy of lock_commit's rule as of T8.18 -- if you
# find yourself editing them, import is the fix, not a better copy.
try:                                                    # pragma: no cover
    from tools.lock_commit import (                     # noqa: E402
        _rank_key as _lock_commit_rank_key,
        DEMOTION_LABEL_PREFIX as CLUSTER_DEMOTION_PREFIX,
    )
except Exception:                                       # noqa: BLE001
    CLUSTER_DEMOTION_PREFIX = "PASS - Cluster demotion"

    def _lock_commit_rank_key(row: dict) -> tuple:      # type: ignore[misc]
        side = (row.get("pick_side") or "").strip().upper()
        name = (f"{(row.get('away_team') or '').strip().upper()}"
                f"@{(row.get('home_team') or '').strip().upper()}")
        try:
            p = float(row.get("nrfi_prob"))
        except (TypeError, ValueError):
            return (1, 1.0, 1.0, name)
        col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
        return (0,) + tracker._top_pick_rank_tuple(side, p, row.get(col), name)

# Grades that end the row's life without it ever having been a live
# Kelly bet.  end_of_day_check books these flat.
TERMINAL_NON_BET_GRADES = ("POSTPONED", "SUSPENDED")

EXEMPT_CSV = ROOT / "data" / "stake_drift_exempt.csv"


# --------------------------------------------------------------------------
# Coercion helpers
#
# Rows reach this module from three sources with three shapes: the CSV
# ledger (all strings), Supabase via reconcile.py (real ints/floats/None),
# and pl_calc's Supabase-to-CSV coercion (strings again).  Everything below
# is written against "might be any of those".
# --------------------------------------------------------------------------

def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _f(v) -> float | None:
    s = _s(v)
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _price_col(side: str) -> str:
    return "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"


def _prob_col(side: str) -> str:
    return "nrfi_prob" if side == "NRFI" else "yrfi_prob"


def _game_name(row: dict) -> str:
    return f"{_s(row.get('away_team')).upper()}@{_s(row.get('home_team')).upper()}"


# --------------------------------------------------------------------------
# The operator's escape hatch
# --------------------------------------------------------------------------

def load_exemptions(path: Path = EXEMPT_CSV) -> set[tuple[str, str]]:
    """(date, game_pk) pairs the operator has declared out of scope.

    Same file convention as data/manual_odds_overrides.csv: real header
    row first, then '#' comment lines, so the file can explain itself
    without a second document to keep in sync.
    """
    keys: set[tuple[str, str]] = set()
    if not path.exists():
        return keys
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(
                    line for line in fh if not line.lstrip().startswith("#")):
                d = _s(rec.get("date"))
                g = _s(rec.get("game_pk"))
                if d and g:
                    keys.add((d, g))
    except Exception as exc:                            # noqa: BLE001
        # A malformed escape hatch must not silently disarm the check, and
        # must not crash the 5-minute reconcile either.  Loud + empty.
        print(f"[stake_drift] could not read {path.name}: {exc!r}",
              file=sys.stderr)
    return keys


# --------------------------------------------------------------------------
# Result types
# --------------------------------------------------------------------------

class Finding(NamedTuple):
    """One row whose ledger stake disagrees with the per-day replay."""
    date:     str
    game_pk:  str
    game:     str
    side:     str
    prob:     float
    odds:     str
    stored:   float
    expected: float

    @property
    def delta(self) -> float:
        return self.stored - self.expected

    def line(self) -> str:
        return (f"  {self.date} {self.game:<9} {self.side:<4} "
                f"p={self.prob:.4f} @ {self.odds:>5}  "
                f"ledger={self.stored:>5.2f}u  rule={self.expected:>5.2f}u  "
                f"({self.delta:+.2f}u)")


class Report:
    def __init__(self) -> None:
        self.days_checked:  int = 0
        self.rows_compared: int = 0
        self.violations:  list[Finding] = []
        self.cap_order:   list[str] = []      # human-readable day notes
        self.exempt:      dict[str, int] = {} # reason -> count
        self.unpriceable: list[str] = []      # replay could not size these
        self.day_errors:  list[str] = []      # a day we could not replay
        self.compared:    list[tuple[Finding, bool]] = []   # (row, ok) for -v

    @property
    def ok(self) -> bool:
        return not self.violations

    def exempt_total(self) -> int:
        return sum(self.exempt.values())

    def summary(self) -> str:
        bits = [f"days={self.days_checked}", f"rows={self.rows_compared}",
                f"violations={len(self.violations)}"]
        if self.cap_order:
            bits.append(f"cap-order days={len(self.cap_order)}")
        if self.exempt_total():
            bits.append(f"exempt={self.exempt_total()}")
        if self.unpriceable:
            bits.append(f"unpriceable={len(self.unpriceable)}")
        if self.day_errors:
            bits.append(f"day-errors={len(self.day_errors)}")
        return "  ".join(bits)


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def _classify_exempt(row: dict, exempt_keys: set[tuple[str, str]]) -> str | None:
    """Return a reason string if this row must not be compared, else None."""
    if (_s(row.get("date")), _s(row.get("game_pk"))) in exempt_keys:
        return "operator exemption file"
    if _s(row.get("graded_result")).upper() in TERMINAL_NON_BET_GRADES:
        return "postponed / suspended"
    if _s(row.get("sportsbook")) == MANUAL_ODDS_BOOK:
        return "manual odds override"
    if _s(row.get("pick_label")).startswith(CLUSTER_DEMOTION_PREFIX):
        return "cluster demotion"
    return None


def check_rows(
    rows:        Iterable[dict],
    *,
    season:      int,
    since:       str = STAKE_DRIFT_ERA_FLOOR,
    until:       str | None = None,
    exempt_keys: set[tuple[str, str]] | None = None,
) -> Report:
    """Per-day replay of the Kelly allocation against the ledger.

    MUTATES tracker's in-process Kelly state and restores it.  The reset
    discipline requires `kelly_reset_daily_committed()` before any capped
    sizing call (R2), and that clears the daily tally, the bankroll cache
    and bumps the batch epoch.  A caller that is CONCURRENTLY sizing real
    bets in the same process would have its budget wiped mid-batch, so the
    three globals are saved and restored around the whole sweep.  Both
    shipped callers are safe anyway -- `tools/pl_calc.py` is standalone and
    `tools/reconcile.py` is launched as a SUBPROCESS by
    `workers.predictor_loop.step_reconcile` -- but a self-check that can
    corrupt the thing it is checking is not a self-check.
    """
    exempt_keys = exempt_keys or set()
    rep = Report()

    # --- population -------------------------------------------------------
    # STRONG, actually placed, on a real side, with a captured price for the
    # side we bet.  Anything else has no Kelly stake to reproduce.
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        date = _s(r.get("date"))
        if not date or date < since:
            continue
        if until and date > until:
            continue
        if _s(r.get("pick_strength")).upper() != "STRONG":
            continue
        if _s(r.get("bet_placed")).upper() != "Y":
            continue
        side = _s(r.get("pick_side")).upper()
        if side not in ("NRFI", "YRFI"):
            continue
        if not _s(r.get(_price_col(side))):
            continue          # unpriced -- never guessed, never sized
        by_day.setdefault(date, []).append(r)

    saved_committed = dict(tracker._daily_committed)
    saved_bankroll  = tracker._bankroll_cache
    saved_epoch     = tracker._kelly_batch_epoch
    try:
        for date in sorted(by_day):
            _check_one_day(date, by_day[date], season, exempt_keys, rep)
    finally:
        tracker._daily_committed.clear()
        tracker._daily_committed.update(saved_committed)
        tracker._bankroll_cache   = saved_bankroll
        tracker._kelly_batch_epoch = saved_epoch

    return rep


def _check_one_day(date: str, night: list[dict], season: int,
                   exempt_keys: set[tuple[str, str]], rep: Report) -> None:
    # --- split the night into comparable rows and spoken-for exposure ----
    comparable: list[dict] = []
    exempt_units = 0.0
    for r in night:
        reason = _classify_exempt(r, exempt_keys)
        if reason:
            rep.exempt[reason] = rep.exempt.get(reason, 0) + 1
            # An exempt row still CONSUMED budget on the night -- a flat-1u
            # orphan heal is 1u genuinely at risk.  Seed it so the rows we
            # do compare are replayed against the room that was actually
            # left, rather than a budget we know they never had.
            exempt_units += _f(r.get("units_risked")) or 0.0
            continue
        comparable.append(r)
    if not comparable:
        return

    # --- R2: one reset per day, before the first capped sizing call ------
    tracker.kelly_reset_daily_committed()
    # THE SEED IS THE WHOLE TRICK.  `_committed_on` memoises per date and
    # only reads the ledger on a miss, so priming the key makes the replay
    # start the night with an empty budget instead of one already spent on
    # the very rows it is about to re-derive.  Without this the day's
    # exposure is double-counted and every row recomputes to 0.
    tracker._daily_committed[date] = exempt_units

    # --- best-bet-first ---------------------------------------------------
    # tools/lock_commit.py's OWN sort key, imported above rather than
    # reimplemented.  If the writer's order and the replay's order are not
    # literally the same function, a cap-bound night flags every time.
    def _rank(r: dict):
        # Sort on a STRINGIFIED view of the three fields the key reads.
        # `_top_pick_rank_tuple` underneath calls `.strip()` on the price
        # and catches only (TypeError, ValueError), so a numeric odds value
        # -- what a Supabase row hands us if a column is ever typed REAL
        # instead of TEXT -- would raise AttributeError out of the sort and
        # take a whole slate unchecked with it.
        side = _s(r.get("pick_side")).upper()
        view = dict(r)
        view["pick_side"] = side
        view["nrfi_prob"] = _s(r.get("nrfi_prob"))
        view[_price_col(side)] = _s(r.get(_price_col(side)))
        return _lock_commit_rank_key(view)

    try:
        comparable.sort(key=_rank)
    except Exception as exc:                            # noqa: BLE001
        rep.day_errors.append(f"{date}: could not rank slate ({exc!r})")
        return

    rep.days_checked += 1
    day_findings: list[Finding] = []
    cap_bound     = False
    stored_total  = 0.0
    replay_total  = 0.0

    for r in comparable:
        side   = _s(r.get("pick_side")).upper()
        game   = _game_name(r)
        pk     = _s(r.get("game_pk"))
        odds   = _s(r.get(_price_col(side)))
        p      = _f(r.get(_prob_col(side)))
        stored = _f(r.get("units_risked"))

        if p is None or stored is None:
            rep.unpriceable.append(
                f"{date} {game}: no usable "
                f"{'probability' if p is None else 'stored stake'}")
            continue

        try:
            # UNCAPPED FIRST, and it is not a spare call.  game_date=None is
            # a projection (rule R1) and mutates nothing, so it is safe to
            # ask twice; the pair is what tells us whether the DAILY CAP --
            # rather than the model -- decided this stake.  Without that we
            # cannot separate a cap-order artifact from real drift.
            uncapped = tracker.kelly_stake_units(p, odds, season=season)
            expected = tracker.kelly_stake_units(p, odds, season=season,
                                                 game_date=date)
        except tracker.KellyBudgetUnavailable as exc:
            rep.day_errors.append(f"{date}: budget unreadable ({exc})")
            return
        except Exception as exc:                        # noqa: BLE001
            rep.day_errors.append(f"{date} {game}: sizing failed ({exc!r})")
            continue

        if expected is None:
            # Kelly has no opinion (unparseable price/probability).  A
            # projection that cannot price a row leaves it alone, and so
            # does this check -- but it is COUNTED, because a check that
            # goes quietly blind is worse than one that fails.
            rep.unpriceable.append(f"{date} {game}: Kelly returned None")
            continue

        if uncapped is not None and expected < uncapped - STAKE_DRIFT_TOLERANCE:
            cap_bound = True

        rep.rows_compared += 1
        stored_total += stored
        replay_total += expected

        f = Finding(date=date, game_pk=pk, game=game, side=side,
                    prob=p, odds=odds, stored=stored, expected=expected)
        agrees = abs(stored - expected) <= STAKE_DRIFT_TOLERANCE
        rep.compared.append((f, agrees))
        if not agrees:
            day_findings.append(f)

    if not day_findings:
        return

    # --- cap-order artifact, or genuine drift? ---------------------------
    # See the module docstring.  A cap-bound night whose ledger and replay
    # TOTALS agree differs only in how one fixed budget was split, which is
    # the documented first-come-first-served limitation of
    # kelly_stake_units, not a stale stake.  Totals disagreeing means the
    # money at risk genuinely differs, and that is always a violation.
    if cap_bound and abs(stored_total - replay_total) <= STAKE_DRIFT_TOLERANCE:
        rep.cap_order.append(
            f"{date}: daily cap bound; ledger and replay both total "
            f"{stored_total:.2f}u but split it differently across "
            f"{len(day_findings)} game(s) -- allocation order, not drift"
        )
        return

    rep.violations.extend(day_findings)


# --------------------------------------------------------------------------
# Rendering -- shared so pl_calc and reconcile say the same thing
# --------------------------------------------------------------------------

def render(rep: Report, *, verbose: bool = False) -> list[str]:
    out: list[str] = []
    if verbose and rep.compared:
        out.append("Compared rows (ledger vs per-day replay):")
        for f, agrees in rep.compared:
            out.append(f.line() + ("" if agrees else "   <-- MISMATCH"))
        out.append("")

    if rep.violations:
        out.append(f"!! STAKE DRIFT: {len(rep.violations)} row(s) whose "
                   f"published stake does not match the sizing rule:")
        out.extend(f.line() for f in rep.violations)
        out.append("   The ledger is NOT auto-corrected -- rewriting a locked "
                   "stake is a money-path write.")
        out.append("   Investigate, then either heal deliberately or add the "
                   "row to data/stake_drift_exempt.csv.")
    else:
        out.append(f"Stake drift: none over {rep.rows_compared} locked STRONG "
                   f"row(s) on {rep.days_checked} slate(s).")

    for note in rep.cap_order:
        out.append(f"   (cap-order) {note}")
    if rep.exempt_total():
        detail = ", ".join(f"{n} {k}" for k, n in sorted(rep.exempt.items()))
        out.append(f"   ({rep.exempt_total()} row(s) exempt: {detail})")
    for u in rep.unpriceable:
        out.append(f"   (skipped) {u}")
    for e in rep.day_errors:
        out.append(f"   (unchecked) {e}")
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _today_et() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="single ET slate date (YYYY-MM-DD)")
    ap.add_argument("--since", default=None,
                    help=f"start date (default: the era floor, "
                         f"{STAKE_DRIFT_ERA_FLOOR})")
    ap.add_argument("--until", default=None, help="end date (inclusive)")
    ap.add_argument("--all", action="store_true",
                    help="ignore the era floor and replay the whole season "
                         "(expect a wall of pre-re-basing noise -- see the "
                         "module docstring)")
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--verbose", action="store_true",
                    help="print every compared row, not just the mismatches")
    args = ap.parse_args(argv)

    if args.date:
        since = until = args.date
    else:
        since = args.since or ("2026-01-01" if args.all
                               else STAKE_DRIFT_ERA_FLOOR)
        until = args.until

    season = args.season or int((until or since or _today_et())[:4])

    # THE CSV, DELIBERATELY.  pl_calc reads Supabase first because it
    # publishes numbers; this CLI is a local spot-check and the CSV is the
    # copy a developer can actually inspect line by line.  The Supabase-
    # backed run of this same code is the one wired into pl_calc and
    # reconcile, which is where a stale local mirror would matter.
    path = tracker._csv_path(season)
    if not path.exists():
        print(f"ERROR: ledger CSV not found at {path}", file=sys.stderr)
        return 2
    rows = tracker._read_rows(path)

    rep = check_rows(rows, season=season, since=since, until=until,
                     exempt_keys=load_exemptions())

    print(f"Stake drift replay -- {since} to {until or 'today'}")
    print(f"Source: {path}")
    print(f"Rule:   per-day Kelly replay, best-bet-first, "
          f"tolerance {STAKE_DRIFT_TOLERANCE}")
    print()
    for line in render(rep, verbose=args.verbose):
        print(line)
    print()
    print(rep.summary())
    return 1 if rep.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
