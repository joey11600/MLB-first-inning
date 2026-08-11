#!/usr/bin/env python3
"""
discord_broadcasts.py -- the five subscriber messages, and the rules
that keep them honest.

    1. THE BOARD        slate T-60   the No.1 first, then every game
    2. THE No.1 PLAY    its own lock the one bet, priced
    3. THE No.1 SETTLED that row grades  win or lose, the moment it lands
    4. FINAL RESULTS    all graded    how every pick did
    5. THE No.1 LEDGER  after (4)     record + units at quarter-Kelly

MEASURED FACTS THAT SHAPED THESE (re-run them before changing anything):

  * AT THE SLATE'S T-60, ONLY 15% OF GAMES HAVE A CAPTURED PRICE
    (56/367 across 27 slates). Prices arrive per-game near each lock, not
    up front. So THE BOARD leads with the MODEL PROBABILITY -- present for
    every game from the moment the slate is predicted -- and prints a
    literal "--" where no price exists. It never invents one; CLAUDE.md's
    "never fabricate odds" applies to the product surface too.

  * THE STORED PROBABILITY AND THE PRICE CAN DISAGREE, AND THE LEDGER
    SIDES WITH THE PRICE. Measured on bets since 08-01: `nrfi_prob`
    reproduces `implied(price) + edge_on_pick` on 7 of 9, and drifts on
    two -- DET@OAK by 9.8 points, SD@ARI by 2.6. The tiebreak is not
    academic: SD@ARI 2026-08-04 was staked 9.00u in the ledger, and
    kelly_stake_units() returns 9.0 from the DERIVED probability and 8.0
    from the stored one. The ledger was sized off the derived value.
    ==> For any PRICED row, publish implied(price) + edge_on_pick.
        Publishing the stored number would print arithmetic a subscriber
        can falsify in five seconds -- "model 60.1%, break-even 59.2%,
        edge +10.8%" -- inside the message that IS the product.
    For an UNPRICED row there is no price to reconcile against, so the
    model probability is shown as-is and labelled a projection.

STAKING IS NEVER RE-IMPLEMENTED HERE. Every unit figure comes from
`tracker.kelly_stake_units`, the same function the ledger uses. A second
copy of the staking rule is how two surfaces start quoting different
stakes for the same bet.

UNITS. 1 unit = 1% of YOUR bankroll, so the same unit count is correct
for every subscriber whatever their bank. Totals are only ever summed on
a named fixed basis (quarter-Kelly), and no bank level, growth curve or
drawdown is ever published -- those describe the bettor's money
management, not the system.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

ET = ZoneInfo("America/New_York")

# THE DASHBOARD URL IS NEVER PUBLISHED. Operator, 2026-08-06: "you need
# to never send the link to the actual dashboard ever. that is only for
# me." It is an internal operator surface -- it exposes leans, passes,
# pass reasons, model diagnostics, the full ledger and the replay. The
# subscriber product is these messages, and nothing in them should
# invite a reader to the console behind them. Do NOT reintroduce a link
# here for "convenience"; if a message needs more context, put the
# context IN the message.

# How long before first pitch a pick locks. Mirrors dashboard/lib/lock.ts
# LOCK_MINUTES_PREGAME; if that changes, change this.
LOCK_MINUTES_PREGAME = 60


def _tracker():
    """Import tracker, or return None. NEVER let its failure escape.

    CATCHES SystemExit, NOT JUST Exception, and that distinction is the
    whole point: tracker.py guards its optional dependencies with
    `sys.exit("Missing dependency: pip install mlb-statsapi")`, which
    raises SystemExit -- a BaseException that sails straight through
    `except Exception`. Found live on 2026-08-06 running the transport
    check in Railway's console, where the shell's default `python` is
    not the app venv: the process died instead of degrading.

    That was a harmless console artifact, but the same import runs
    INSIDE workers/predictor_loop.py, which owns the money path. A
    marketing module must never be able to kill predict/grade/odds/lock
    because a dependency went missing.
    """
    try:
        import tracker
        return tracker
    except (Exception, SystemExit):  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# odds / probability helpers
# ---------------------------------------------------------------------------

def american_to_implied(odds: float) -> float:
    """Break-even win rate the price demands, vig included."""
    return (-odds / (-odds + 100.0)) if odds < 0 else (100.0 / (odds + 100.0))


def fmt_odds(o) -> str:
    try:
        n = int(float(str(o).strip()))
    except (TypeError, ValueError):
        return "--"
    return f"+{n}" if n > 0 else str(n)


def _f(v):
    try:
        s = str(v).strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


def side_price(row: dict) -> float | None:
    """The captured price for the side actually picked."""
    side = (row.get("pick_side") or "").strip().upper()
    col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    return _f(row.get(col))


def published_probability(row: dict) -> tuple[float | None, bool]:
    """(probability of the PICKED side, is_price_reconciled).

    THE RULE, and the reason this function exists at all -- see the module
    docstring. Priced row: implied(price) + edge, which is the tuple the
    ledger actually sized from. Unpriced row: the model's own number,
    flagged so the caller can label it a projection.
    """
    side = (row.get("pick_side") or "").strip().upper()
    p_nrfi = _f(row.get("nrfi_prob"))
    odds = side_price(row)
    edge = _f(row.get("edge_on_pick"))

    if odds is not None and edge is not None:
        return american_to_implied(odds) + edge, True

    if p_nrfi is None:
        return None, False
    return (p_nrfi if side == "NRFI" else 1.0 - p_nrfi), False


def stake_for(row: dict) -> float | None:
    """Units to publish.

    THE LEDGER IS THE AUTHORITY -- read `units_risked`, do not recompute.

    WHY, and it cost a live contradiction on 2026-08-06: the dashboard
    showed 3u for SD@ARI while Discord showed 4u for the same bet at the
    same price. TWO independent causes, and an earlier version of this
    docstring named only the second and asserted "both implementations
    are identical" -- which was wrong, so it is corrected here:

      1. THE FORMULAS DID NOT MATCH. `tracker.kelly_stake_units` rounds
         TWICE (round(x, 2) then to whole units), so 3.4975 -> 3.5 -> 4.
         `dashboard/lib/kelly-sim.ts` rounded once: Math.round(3.4975)
         -> 3. Every stake in [x.495, x.5) diverged, as did every exact
         half, because Python rounds half-to-EVEN and JavaScript rounds
         half UP. kelly-sim.ts is now a verified mirror (0 disagreements
         over 396,622 probability/price pairs) and a prebuild guard
         fails the build if that ever drifts again.

      2. THE INPUTS DID NOT MATCH EITHER. The raw stake was 3.4975u,
         a hair under a rounding boundary, and the surfaces fed it
         probabilities of different PRECISION:

             dashboard  yrfiPct 63.4   (1 decimal)  -> 3.4975
             discord    0.6343         (full)       -> 3.5151

    Fixing (1) alone would not have been enough: a 0.0003 difference in
    the input still moves the published stake by a whole unit, and no
    amount of formula parity repairs a lossy input. Any surface that
    recomputes will keep landing on the wrong side of some boundary,
    forever, because rounding boundaries are dense. The only stable
    answer is for every surface to PRINT THE SAME STORED NUMBER -- the
    one the system actually staked.

    Recomputation stays as a fallback ONLY for a row the ledger has not
    priced yet (so a pre-lock board can still show an indicative size),
    and that path is the one place a boundary disagreement can still
    appear. It is bounded to rows carrying no committed stake.

    RETURNS 0.0 FOR A LEDGER REFUSAL, AND THAT IS NOT THE SAME AS None
    (T8.18). None means "the ledger has no opinion yet"; 0.0 means "the
    system looked at this bet and decided to stake nothing" — quarter
    Kelly found no edge at the captured price, or the night's 15u risk
    budget was already spoken for. Since T8.18 that decision is written
    to the ledger as the literal "0.0" at commit time, precisely so it
    survives the Supabase round trip instead of being dropped as a blank
    and restored stale.

    Until this branch existed the `booked > 0` test read "0.0" as
    nothing-booked and fell straight through to the RECOMPUTE below —
    which is uncapped, has no idea the budget is gone, and cheerfully
    produced a positive number. A refusal therefore published as a
    confident stake, on the surface that IS the product. Callers must
    treat 0.0 as a printable answer and not as falsy-means-missing.
    """
    booked = _f(row.get("units_risked"))
    if booked is not None and booked > 0:
        return booked
    if booked is not None:
        # Exactly 0 (or, defensively, anything non-positive) is a decided
        # refusal. Do NOT fall through to the recompute.
        return 0.0

    p, _ = published_probability(row)
    odds = side_price(row)
    if p is None or odds is None:
        return None
    tracker = _tracker()
    if tracker is None:
        return None
    try:
        # NO `game_date=` HERE, DELIBERATELY, AND IT IS LOAD-BEARING (T8.18
        # rule R1): passing it would ALLOCATE against the day's 15u budget
        # and mutate tracker's in-process tally. This module runs IN-PROCESS
        # inside the long-lived Railway loop (workers/predictor_loop.py), so
        # a capped call here would accumulate the day's tally forever — every
        # board render adding to it until the cap chokes every real bet to
        # zero. A published figure is a PROJECTION, never an allocation.
        return tracker.kelly_stake_units(p, str(int(odds)))
    except (Exception, SystemExit):  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# the price ladder -- book-free, so it works at ANY sportsbook
# ---------------------------------------------------------------------------

# THE PRICE LADDER LIVED HERE AND IS GONE (2026-08-07).
#
# `price_ladder()` built the rung table for the Discord board. The
# operator had the ladder removed from the messages on 2026-08-06, which
# left this function defined and called by nothing -- while its docstring
# still claimed to mirror dashboard/lib/price-ladder.ts.
#
# It did not mirror it, and that was the whole problem: it walked -100
# downward in 5-cent steps against the ROUNDED stake, where the
# dashboard solves analytically on the raw quarter-Kelly. Dead code that
# advertises a guarantee it does not keep is worse than no code, because
# the next person to need a ladder here would have reached for it.
#
# `pass_price` below is the survivor, and it now mirrors the TypeScript
# for real -- verified over 2,497 (probability, price) pairs.


def pass_price(p: float) -> int | None:
    """Worst price still worth a full unit; below this, no bet.

    MIRRORS dashboard/lib/price-ladder.ts `worstPriceFor(p, 1)` — the
    analytic solve, then CEIL. It has to, because both numbers are
    published for the same bet and a subscriber reads whichever reaches
    them first.

    IT DID NOT, UNTIL 2026-08-07, AND THE GAP FAVOURED THE WRONG SIDE.
    This walked -100 downward in 5-cent steps against the ROUNDED
    `kelly_stake_units`, so it stopped at the coarsest grid point where
    the rounded stake was still >= 1u. That includes prices whose RAW
    quarter-Kelly is already under a unit and only reaches 1u by
    rounding — exactly the bets price-ladder.ts documents the operator
    deciding not to publish (2026-08-04: "the ladder ends at the last
    full unit"). Measured on the same bets:

        p=0.6343 card -135   here -165   dashboard -162
        p=0.62   card -150   here -155   dashboard -152
        p=0.70   card -200   here -225   dashboard -219

    Every one of those told a subscriber they could lay a worse price
    than the dashboard would allow. SD@ARI on 2026-08-07 published both
    numbers on the same night.

    The solve:  raw = 100 * f/4  and  f = p - (1-p)/b
                =>  b = (1-p) / (p - units/25)

    CEIL, NEVER ROUND: the solve returns a fractional price (-133.6) and
    rounding to -134 publishes a limit one cent PAST the full-unit line
    — the worst price we tell people to take would be one we would not
    take ourselves. ceil lands on the worst ACCEPTABLE integer in both
    directions: ceil(-133.6) = -133, ceil(120.4) = 121.
    """
    import math

    tracker = _tracker()
    if tracker is None:
        return None
    try:
        fraction = float(tracker.KELLY_FRACTION)
        max_units = float(tracker.KELLY_MAX_STAKE_FRAC) * 100.0
    except (Exception, SystemExit):  # noqa: BLE001
        return None
    if not (0.0 < p < 1.0) or fraction <= 0:
        return None

    # A stake bigger than the per-bet cap can never be "wanted", so the
    # one-unit solve is only meaningful below it.
    if 1.0 > max_units:
        return None
    denom = p - 1.0 / (100.0 * fraction)
    if denom <= 0:
        return None                      # p too low to ever want a full unit
    b = (1.0 - p) / denom
    if b <= 0 or not math.isfinite(b):
        return None
    exact = (100.0 * b) if b >= 1.0 else (-100.0 / b)
    return int(math.ceil(exact))


# ---------------------------------------------------------------------------
# slate loading + timing
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    for c in (ROOT / "data", ROOT.parent / "data"):
        if (c / "boards").exists() or (c).exists():
            return c
    return ROOT / "data"


def load_slate(date_iso: str, season: int | None = None) -> list[dict]:
    season = season or int(date_iso[:4])
    path = _data_dir() / f"picks_{season}.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f)
                if (r.get("date") or "").strip() == date_iso]


def game_start_et(date_iso: str, game_time_et: str) -> datetime | None:
    """Parse "6:40 PM ET" on `date_iso` into an ET datetime.

    PARSED, NEVER STRING-SORTED. "10:05 AM" sorts before "9:40 PM" as a
    string, which picks the wrong "first game" on any slate mixing
    one- and two-digit hours.
    """
    t = (game_time_et or "").strip().replace("ET", "").strip()
    if not t:
        return None
    for fmt in ("%I:%M %p", "%H:%M"):
        try:
            hm = datetime.strptime(t, fmt)
            y, m, d = (int(x) for x in date_iso.split("-"))
            return datetime(y, m, d, hm.hour, hm.minute, tzinfo=ET)
        except ValueError:
            continue
    return None


def first_pitch_of_slate(rows: list[dict], date_iso: str) -> datetime | None:
    starts = [game_start_et(date_iso, r.get("game_time_et", "")) for r in rows]
    starts = [s for s in starts if s]
    return min(starts) if starts else None


def last_pitch_of_slate(rows: list[dict], date_iso: str) -> datetime | None:
    """The LATEST first pitch on the slate.

    Exists for the FINAL RESULTS backstop. Anchoring that to the FIRST
    pitch is not enough: on an 11-game card running 12:35 PM to 9:40 PM,
    first-pitch + 20 minutes is 12:55 PM, with ten games still to come.
    """
    starts = [game_start_et(date_iso, r.get("game_time_et", "")) for r in rows]
    starts = [s for s in starts if s]
    return max(starts) if starts else None


def is_strong(row: dict) -> bool:
    return (row.get("pick_strength") or "").strip().upper() == "STRONG"


def is_refused(row: dict) -> bool:
    """Did the system LOOK at this row and decide to stake nothing?

    THE ONE TEST EVERY IMPERATIVE MESSAGE MUST APPLY, 2026-08-10. The
    board learned this at T8.18 and THE No.1 PLAY did not, so the same
    defect shipped twice from one file. Tonight it reached the channel:

        # 🔒 TONIGHT'S №1 PLAY
        **TB @ OAK** · 9:40 PM ET
        ## YRFI — a run scores in the 1st
        Model 58.3% · price -145 (needs 59.2%) · edge -0.9%
        Don't take worse than -130.          <-- at a price of -145
        _1 unit = 1% of your bankroll._

    Quarter-Kelly had already refused it (`units_risked` 0, edge -0.9%),
    and the ledger correctly counted nothing. The MESSAGE said otherwise:
    a padlock, a headline that names it the night's play, a price limit
    the quoted price already violates, and a bankroll footer. The only
    hint it was not a bet was a "-0.9%" mid-sentence.

    THE THREE STATES ARE NOT TWO. `stake_for` separates them and callers
    must not collapse them:

        > 0    the system staked money        -> speak in the imperative
        == 0   the system refused             -> say so; publish no price
        None   no price captured yet          -> a real product path, the
               ladder message; NOT a refusal, so do not announce one

    The middle case is the one that bites, because 0.0 is falsy in
    Python: `if stake:` skipped the stake line entirely and left every
    surrounding line still reading as an instruction to bet.
    """
    return stake_for(row) == 0.0


def _fmt_units(u: float) -> str:
    """Units as a subscriber should read them.

    NEVER `f"{u:.0f}"`. A 0.5u stake formats to "0" under it, so the
    board could print "Stake 0 units" — which reads as a formatting bug,
    invites the reader to bet it at a size of their own choosing, and is
    indistinguishable from the refusal above. `KELLY_ROUNDED_FLOOR` is
    0.5, so this is reachable on any row the pre-lock projection floors.
    """
    return f"{u:.0f}" if abs(u - round(u)) < 1e-9 else f"{u:.1f}"


def is_locked(date_iso: str, row: dict, now: datetime) -> bool:
    """Has this pick passed its OWN lock (T-60 before ITS first pitch)?

    A pick is not final until it locks.  Before that the model keeps
    re-deciding it every predict tick as lineups post, and it may change
    side, strength or stake -- or stop being a play at all.

    WHY THE BOARD MUST ASK THIS, 2026-08-08.  THE BOARD fires at T-60
    before the FIRST game on the card.  Every LATER game is therefore
    still unlocked when the board prints, and stays unlocked for hours.
    Tonight the board fired at 2:07 PM (ahead of a 3:05 PM opener) and
    published:

        # ⭐ THE №1 PLAY
        **CLE @ CWS** · 7:15 PM ET
        ### Stake 6 units

    CLE@CWS did not lock until 6:15 PM -- FOUR HOURS later.  It did not
    survive them.  pick_changes journals the model's own reversals:

        03:37 PM  TOR@PHI  'STRONG YRFI' -> 'LEAN YRFI'
        04:02 PM  CLE@CWS  'STRONG YRFI' -> 'LEAN YRFI'

    leaving the slate with NO STRONG pick at all, while the channel still
    held a published instruction to stake 6 units -- 6% of a subscriber's
    bankroll -- on a game the system had stopped backing.

    This is T8.16's defect with the sign flipped.  That one had the board
    claiming a verdict it had not reached ("declined them all") on games
    still waiting on lineups.  This one has it claiming a COMMITMENT it
    has not made.  Both come from the same root: the board speaks at
    slate time about picks that decide at game time.  A pick that can
    still change must never be printed as an instruction to bet.
    """
    st = game_start_et(date_iso, row.get("game_time_et", ""))
    if st is None:
        return False
    return now >= st - timedelta(minutes=LOCK_MINUTES_PREGAME)


def is_undecided(row: dict) -> bool:
    """Has the model NOT YET judged this game?

    "PASS - Lineup pending" and "PASS - Starter pending" are not verdicts.
    They mean the inputs do not exist yet: a pick commits 60 minutes before
    ITS OWN first pitch, when the lineup posts.

    WHY THIS DISTINCTION IS LOAD-BEARING, 2026-08-07. THE BOARD fires at
    T-60 before the FIRST game of the slate. On a card running 6:40 PM to
    10:15 PM that is 5:40 PM, hours before the late games have lineups --
    so the board is structurally incapable of having judged them. It
    printed:

        ## NO PLAY TONIGHT
        The model looked at every game and declined them all.

        ## PASSING (8)
        `LAD @ ARI`  9:40 PM  57.4%  Lineup Pending      <-- not declined

    and three hours later published LAD@ARI as a 3-unit №1. The board
    contradicted its own body -- it listed four games as "Lineup Pending"
    under a heading claiming all had been declined -- and then contradicted
    itself again across the evening. A subscriber who read the board and
    stopped watching missed the only play of the night.

    "Declined" and "not yet decided" are different claims. Only one of them
    was true.
    """
    s = (row.get("pick_strength") or "").strip().upper()
    if s in ("LINEUP PENDING", "STARTER PENDING"):
        return True
    return "pending" in (row.get("pick_label") or "").strip().lower()


def top_pick(rows: list[dict]) -> dict | None:
    """The night's No.1, using the ledger's own rule so the board badge,
    the dashboard hero and this message cannot disagree."""
    tracker = _tracker()
    if tracker is None:
        return None
    best = None
    for r in rows:
        if not is_strong(r):
            continue
        if (r.get("pick_side") or "").strip().upper() not in ("NRFI", "YRFI"):
            continue
        try:
            if tracker._row_is_nights_top_pick(r):
                # The gate answers "is this THE top pick", so the first
                # row it accepts is the answer.
                return r
        except Exception:  # noqa: BLE001
            pass
        best = best or r
    return best


# ---------------------------------------------------------------------------
# THE MESSAGES
# ---------------------------------------------------------------------------
#
# Written as a PRODUCT, not a debug dump. Rules applied throughout:
#   * every game shows a model probability (available for all games,
#     always) -- the price and edge appear only where a price was really
#     captured, and "--" otherwise. Nothing is invented.
#   * the ladder is book-free, so a subscriber on any sportsbook can act.
#   * units are quarter-Kelly, 1 unit = 1% of the reader's own bankroll.
#   * leans are shown with results but never a running scoreline: they
#     are 154-173 (47.1%) on the season and publishing a nightly record
#     invites totalling up a sub-coin-flip series.

def _hm(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _long_date(date_iso: str) -> str:
    y, m, d = (int(x) for x in date_iso.split("-"))
    return datetime(y, m, d).strftime("%A, %B %d").replace(" 0", " ")


def _side_words(side: str) -> str:
    return ("a run scores in the 1st" if side == "YRFI"
            else "no run in the 1st")


def _game_line(r: dict, date_iso: str) -> str:
    """One compact row: teams, time, side, probability, price if known."""
    st = game_start_et(date_iso, r.get("game_time_et", ""))
    when = _hm(st) if st else "--"
    away = (r.get("away_team") or "").upper()
    home = (r.get("home_team") or "").upper()
    side = (r.get("pick_side") or "").upper()
    p, reconciled = published_probability(r)
    odds = side_price(r)
    prob = f"{p*100:.1f}%" if p is not None else "--"
    if odds is not None:
        edge = _f(r.get("edge_on_pick"))
        tail = f" · {fmt_odds(odds)}" + (f" · edge {edge*100:+.1f}%" if edge is not None else "")
    else:
        tail = " · no price yet"
    return f"`{away:>3} @ {home:<3}` {when:>8}  **{side}** {prob}{tail}"


def build_board(date_iso: str, rows: list[dict],
                now: datetime | None = None) -> str:
    """BROADCAST 1 -- the whole slate, an hour before the first game.

    TIME-AWARE, AND `now` IS THE WHOLE POINT.

    On the normal path this changes nothing: the trigger fires at T-60
    before the FIRST game, when nothing has started. But that condition
    (`now >= first_pitch - 60m`) stays true for the REST OF THE DAY, and
    after it the only thing holding the board back is a Supabase dedupe
    row that is deliberately FAIL-OPEN. On 2026-08-06 it opened, and the
    board published at 8:43, 8:49 and 8:58 PM ET carrying WSH@PHI --
    a 6:05 PM game that had already finished and graded WIN +2.50u --
    under the heading THE PLAYS, with "Stake 3u" and a price floor.

    That is an instruction to bet a game whose result was already known.
    For a service whose entire value is a verifiable FORWARD record, it
    is indistinguishable from post-hoc winner claiming, which is the
    oldest trick in the paid-picks trade and the exact thing this
    product exists to disprove. It is the FINAL RESULTS backstop below
    with the sign flipped, and it needs its own guard for the same
    reason.

    Started games are therefore dropped from the actionable sections and
    counted in one honest line instead.
    """
    now = now or datetime.now(ET)

    def _started(r: dict) -> bool:
        st = game_start_et(date_iso, r.get("game_time_et", ""))
        return st is not None and now >= st

    live = [r for r in rows if not _started(r)]
    gone = len(rows) - len(live)

    fp = first_pitch_of_slate(rows, date_iso)
    plays  = [r for r in live if is_strong(r)]
    leans  = [r for r in live if (r.get("pick_strength") or "").upper() == "LEAN"]
    others = [r for r in live if r not in plays and r not in leans]

    # THE №1 LEADS THE BOARD. (operator, 2026-08-06: "the #1 pick needs
    # to be highlighted most importantly")
    #
    # The №1 is not merely the first item in a list -- it is the tracked
    # product. The published record, the dashboard hero and the separate
    # lock-time broadcast are all ABOUT this one play, so a board that
    # prints it as one of N equal bullets is under-selling the only
    # number the service is judged on.
    #
    # IT IS CHOSEN FROM THE WHOLE SLATE, NOT FROM WHAT IS STILL
    # UNSTARTED. `top_pick` applies tracker._row_is_nights_top_pick, the
    # same gate the dashboard and the record use. Running it over `live`
    # instead would crown the best REMAINING play on a late board, and
    # "№1" would then mean something different in the channel than it
    # means in the record -- which is precisely the kind of quiet
    # divergence that produced tonight's 3u/4u incident. So: resolve the
    # true №1, and only headline it if it has not started.
    number_one = top_pick(rows)
    no1_live = number_one is not None and number_one in live
    rest = [r for r in plays if not (no1_live and r is number_one)]

    L: list[str] = []
    L.append(f"## THE BOARD · {_long_date(date_iso)}")
    L.append(f"{len(rows)} games · first pitch {_hm(fp) if fp else '--'} ET")
    if gone:
        # Say it plainly rather than silently showing a short slate.
        L.append(f"_{gone} game{'s' if gone != 1 else ''} already underway "
                 f"— not listed below._")

    def _play_block(r: dict, headline: bool) -> None:
        p, _ = published_probability(r)
        side = (r.get("pick_side") or "").upper()
        st = game_start_et(date_iso, r.get("game_time_et", ""))
        away = (r.get("away_team") or "").upper()
        home = (r.get("home_team") or "").upper()
        odds = side_price(r)
        stake = stake_for(r)
        pa = pass_price(p) if p is not None else None
        L.append("")
        if headline:
            # Same shape as build_top_pick's lock-time message, so the
            # two read as one product rather than two formats. Discord
            # sizes # > ## > ###, so the matchup is a bold label and the
            # SIDE is the headline -- the side is the instruction.
            L.append(f"**{away} @ {home}** · {_hm(st) if st else '--'} ET")
            L.append("")
            L.append(f"## {side} — {_side_words(side)}")
        else:
            L.append(f"**{away} @ {home}** · {_hm(st) if st else '--'} ET · "
                     f"**{side}** — {_side_words(side)}")
        if p is not None:
            if odds is not None:
                L.append(f"Model **{p*100:.1f}%** · price {fmt_odds(odds)} "
                         f"needs {american_to_implied(odds)*100:.1f}%")
            else:
                L.append(f"Model **{p*100:.1f}%** · price not captured yet")
            if stake == 0.0:
                # T8.18 -- A REFUSAL IS AN ANSWER, SO SAY IT.
                #
                # A 0.0 out of `stake_for` is always a REFUSAL: either the
                # ledger carries a committed "0.0" (quarter Kelly found no
                # edge at the captured price, or the night's risk budget was
                # already spent) or the unpriced-row fallback recomputed one.
                # Two things must not happen to that number here.
                #
                # It must not be recomputed into a positive one -- it was,
                # until stake_for grew its zero branch, because "0.0" is
                # falsy and fell through to the uncapped fallback.
                #
                # And it must not print as "Projected stake 0 units", which
                # reads as a formatting bug rather than a decision and
                # invites the reader to bet it anyway at some size of their
                # own choosing. Name the refusal instead. Note `stake` is
                # None (not 0.0) when we simply have no figure, so an
                # unpriced row still says nothing rather than claiming a
                # refusal we never made.
                if headline:
                    L.append("### No stake at the current price — NOT LOCKED")
                    L.append("**This is not a bet.** At the price on the "
                             "board the model does not have enough of an "
                             "edge to justify money — or tonight's risk "
                             "budget is already committed elsewhere. The "
                             "system is staking nothing here.")
                else:
                    L.append("_no stake at the current price — NOT LOCKED_")
            elif stake:
                # A STAKE IS AN INSTRUCTION. Only print it as one once the
                # pick can no longer change -- see is_locked.
                locked = is_locked(date_iso, r, now)
                lock_at = (st - timedelta(minutes=LOCK_MINUTES_PREGAME)
                           if st else None)
                if headline:
                    if locked:
                        L.append(f"### Stake {_fmt_units(stake)} units")
                        if pa is not None:
                            L.append(f"Don't take worse than **{fmt_odds(pa)}**.")
                    else:
                        L.append(f"### Projected stake {_fmt_units(stake)} units "
                                 f"— NOT LOCKED")
                        L.append(f"**This is not a bet yet.** It locks at "
                                 f"**{_hm(lock_at) if lock_at else '--'} ET**, "
                                 f"60 minutes before first pitch. Until then "
                                 f"the model is still deciding it and the "
                                 f"stake, the side or the play itself can "
                                 f"change.")
                        L.append("_Act on THE №1 PLAY message, not on this "
                                 "line._")
                else:
                    if locked:
                        line = f"**Stake {_fmt_units(stake)}u**"
                        if pa is not None:
                            line += f" · don't take worse than **{fmt_odds(pa)}**"
                    else:
                        line = (f"_projected {_fmt_units(stake)}u · not locked "
                                f"until {_hm(lock_at) if lock_at else '--'} ET_")
                    L.append(line)

    if no1_live:
        L.append("")
        if is_locked(date_iso, number_one, now):
            L.append("# ⭐ THE №1 PLAY")
            L.append("_The highest-conviction play on the card, and the one "
                     "the tracked record is built on._")
        else:
            # Naming it is fine; calling it settled is not.
            L.append("# ⭐ OUT IN FRONT — NOT LOCKED")
            L.append("_The play leading the card right now. It becomes THE "
                     "№1 PLAY only when it locks, and it gets its own "
                     "message when it does._")
        _play_block(number_one, headline=True)

    if rest:
        L.append("")
        # Same rule as the headline: a heading that reads as a committed
        # bet is only honest once the pick can no longer change.
        any_locked = any(is_locked(date_iso, r, now) for r in rest)
        if no1_live:
            word = "ALSO PLAYING" if any_locked else "ALSO IN CONTENTION"
        else:
            word = ("THE PLAY" if any_locked and len(rest) == 1 else
                    "THE PLAYS" if any_locked else "IN CONTENTION")
        L.append("## " + (word if len(rest) == 1 else f"{word} ({len(rest)})"))
        for r in rest:
            _play_block(r, headline=False)

    # A game whose lineup has not posted has NOT been declined -- see
    # is_undecided. Counting these separately is what stops the board
    # claiming a verdict it has not reached.
    pending = [r for r in live if is_undecided(r)]

    if not plays and pending:
        L.append("")
        L.append(f"## NOT SET YET — {len(pending)} game"
                 f"{'s' if len(pending) != 1 else ''} still waiting on lineups")
        L.append("Nothing is playable from this board *yet*. A pick commits "
                 "60 minutes before its OWN first pitch, when the lineup "
                 "posts — so the late games on this card have not been "
                 "judged, not passed on.")
        L.append("**Watch for THE №1 PLAY.** If one of these commits, it "
                 "lands here on its own.")
    elif not plays:
        L.append("")
        L.append("## NO PLAY TONIGHT")
        L.append("Every game on the card has been judged and declined. "
                 "A quiet night is a correct outcome, not a missing message.")

    if plays and pending:
        L.append("")
        L.append(f"_{len(pending)} game{'s' if len(pending) != 1 else ''} on "
                 f"this card {'are' if len(pending) != 1 else 'is'} still "
                 f"waiting on lineups and could still commit. Each one "
                 f"decides 60 minutes before its own first pitch._")

    if leans:
        L.append("")
        L.append("## LEANING — tracked, never staked")
        for r in leans:
            L.append(_game_line(r, date_iso))

    if others:
        L.append("")
        L.append(f"## PASSING ({len(others)})")
        for r in others:
            st = game_start_et(date_iso, r.get("game_time_et", ""))
            p, _ = published_probability(r)
            reason = (r.get("pick_strength") or "PASS").upper()
            L.append(f"`{(r.get('away_team') or '').upper():>3} @ "
                     f"{(r.get('home_team') or '').upper():<3}` "
                     f"{(_hm(st) if st else '--'):>8}  "
                     f"{(f'{p*100:.1f}%' if p is not None else '--'):>6}  {reason.title()}")

    # Count over the games actually LISTED, not the whole slate -- on a
    # late board `rows` includes games that were filtered out above, and
    # "11 of 11 priced" beneath a one-game board reads as a mistake.
    priced = sum(1 for r in live if side_price(r) is not None)
    L.append("")
    L.append(f"_Prices captured for {priced} of {len(live)} listed "
             f"game{'s' if len(live) != 1 else ''} — most books post "
             f"first-inning lines close to game time. Prices shown are "
             f"DraftKings; if your book differs, the stake still holds so "
             f"long as you are inside the 'don't take worse than' number._")
    return "\n".join(L)


def build_no_play(date_iso: str, r: dict,
                  rows: list[dict] | None = None) -> str:
    """BROADCAST 2b -- the night's best look, DECLINED at the price.

    OPERATOR DECISION, 2026-08-10: on a night the system stakes nothing,
    subscribers get this INSTEAD of THE No.1 PLAY -- not a No.1 message
    with a disclaimer bolted on. A message headlined as the night's play
    should only exist when there is a play; anything else trains readers
    to skim the headline, which is exactly how tonight's refusal got read
    as a bet.

    IT PUBLISHES NO PRICE TO ACT ON. `pass_price` is deliberately absent:
    a "don't take worse than" line is a betting instruction, and there is
    no bet. Naming the price it WOULD take is a different message from
    naming a price to lay, and on a refused row only the first is honest
    -- so the break-even is stated as arithmetic, never as a limit.
    """
    away = (r.get("away_team") or "").upper()
    home = (r.get("home_team") or "").upper()
    side = (r.get("pick_side") or "").upper()
    st = game_start_et(date_iso, r.get("game_time_et", ""))
    p, _ = published_probability(r)
    odds = side_price(r)

    # A refused No.1 does not mean a silent card: a less confident STRONG
    # can carry a better price and still be staked. Saying "no play
    # tonight" over a night that HAS one would be the same class of error
    # this message exists to fix, with the sign flipped (T8.16).
    others = [x for x in (rows or [])
              if x is not r and is_strong(x) and (stake_for(x) or 0) > 0]

    L: list[str] = []
    L.append("# NO PLAY ON THE №1" if others else "# NO PLAY TONIGHT")
    L.append("")
    L.append(f"The card's strongest look was **{away} @ {home}** "
             f"({side}, {_hm(st) if st else '--'} ET). "
             f"**The system is staking nothing on it.**")
    L.append("")
    if p is not None and odds is not None:
        L.append(f"Model **{p*100:.1f}%** · the price is "
                 f"**{fmt_odds(odds)}**, which needs "
                 f"**{american_to_implied(odds)*100:.1f}%** just to break "
                 f"even. The number moved past us before it locked.")
    elif p is not None:
        L.append(f"Model **{p*100:.1f}%**, with no price worth taking.")
    L.append("")
    if others:
        L.append(f"_{len(others)} other play{'s' if len(others) != 1 else ''} "
                 f"on tonight's card {'are' if len(others) != 1 else 'is'} "
                 f"still staked — see THE BOARD._")
        L.append("")
    L.append("**This is not a bet, and nothing is added to the record.** "
             "Passing on a bad price is the same decision as taking a good "
             "one, and it gets published the same way.")
    return "\n".join(L)


def build_top_pick(date_iso: str, r: dict,
                   rows: list[dict] | None = None) -> str:
    """BROADCAST 2 -- fires at the No.1 pick's own lock.

    REFUSALS NEVER REACH THE IMPERATIVE HALF OF THIS FUNCTION. The guard
    below is a second line of defence -- `due_broadcasts` already routes a
    refused No.1 to `build_no_play` -- and it is here because this
    function is also reachable by hand (`--which toppick`, `--resend
    toppick`), which is precisely how an operator would try to re-publish
    a night the scheduler had correctly declined to announce.
    """
    if is_refused(r):
        return build_no_play(date_iso, r, rows)

    side = (r.get("pick_side") or "").upper()
    st = game_start_et(date_iso, r.get("game_time_et", ""))
    p, reconciled = published_probability(r)
    odds = side_price(r)
    stake = stake_for(r)

    L: list[str] = []
    L.append("# 🔒 TONIGHT'S №1 PLAY")
    L.append(f"**{(r.get('away_team') or '').upper()} @ "
             f"{(r.get('home_team') or '').upper()}** · "
             f"{_hm(st) if st else '--'} ET")
    L.append("")
    L.append(f"## {side} — {_side_words(side)}")
    if p is not None:
        if odds is not None:
            edge = _f(r.get("edge_on_pick"))
            L.append(f"Model **{p*100:.1f}%** · price **{fmt_odds(odds)}** "
                     f"(needs {american_to_implied(odds)*100:.1f}%)"
                     + (f" · edge **{edge*100:+.1f}%**" if edge is not None else ""))
        else:
            L.append(f"Model **{p*100:.1f}%** · no price captured — "
                     f"use the ladder below at your own book.")
        if stake:
            L.append(f"### Stake {_fmt_units(stake)} units")
        pa = pass_price(p)
        if pa is not None:
            L.append(f"Don't take worse than **{fmt_odds(pa)}**.")
    L.append("_1 unit = 1% of your bankroll._")
    return "\n".join(L)


def _top_pick_record_line(date_iso: str) -> str | None:
    """One line of running record, or None. Same source as THE LEDGER --
    tools/pl_calc.py --top-pick -- because CLAUDE.md forbids a P&L number
    from anywhere else, and a settle ping quoting a different record from
    the ledger posted an hour later is the 3u/4u contradiction again.

    Filtered to `date <= date_iso` exactly as build_ledger does, so the
    two messages cannot disagree on which nights are counted.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_plc", str(ROOT / "tools" / "pl_calc.py"))
        plc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(spec and plc)  # type: ignore[arg-type]
        rows, _src = plc._load_rows(int(date_iso[:4]))
        picks = [p for p in plc.select_top_picks(rows) if p["date"] <= date_iso]
        s = plc.top_pick_summary(picks)
        if not s or not s.get("bets"):
            return None
        return (f"Record: **{s['wins']}—{s['losses']}** "
                f"({s['hit']:.1f}%) · "
                f"**{s['atKelly']:+.2f}u** at quarter-Kelly")
    except Exception:  # noqa: BLE001
        return None


def build_top_pick_settled(date_iso: str, r: dict) -> str:
    """BROADCAST 5 -- fires the moment the No.1 settles, win OR lose.

    WHY IT EXISTS (operator, 2026-08-06): FINAL RESULTS waits for every
    game on the board, so on a night whose No.1 is an early game the
    result sat unannounced for hours. This closes that gap.

    IT FIRES ON A LOSS TOO. OPERATOR-CONFIRMED, 2026-08-06: "keep the
    loss ping."

    The original request was for a "won" ping; this was widened to both
    outcomes and the operator then confirmed it explicitly. Treat that as
    settled policy, not as a default to be tidied away.

    The reasoning, so it survives the person who wrote it: a channel that
    pings on wins and goes quiet on losses is the oldest tell in paid
    picks, and it would be read that way within a week -- by exactly the
    subscribers who are paying for a verifiable record. It also destroys
    the asset being sold: the record IS the product, and a record is only
    worth something if the losses arrive with the same volume as the
    wins. FINAL RESULTS and THE LEDGER already publish every loss, so a
    win-only ping would conceal nothing; it would only look like an
    attempt to.

    Do NOT make this win-only without the operator saying so in as many
    words. It is one line -- the `elif g == "LOSS"` branch -- which is
    precisely why the reasoning is written down at this length.
    """
    g = (r.get("graded_result") or "").strip().upper()
    side = (r.get("pick_side") or "").upper()
    away = (r.get("away_team") or "").upper()
    home = (r.get("home_team") or "").upper()
    pnl = _f(r.get("profit_loss_units"))
    stake = _f(r.get("units_risked"))
    odds = side_price(r)

    # A REFUSED No.1 HAS NO RESULT TO ANNOUNCE, 2026-08-10.
    #
    # This fires off `top_pick`, which ranks by conviction and never asks
    # whether money went down -- so on a night quarter-Kelly declined the
    # No.1 it was about to publish:
    #
    #     # ✅ THE №1 WON
    #     **TB @ OAK** · **YRFI** — a run scored in the 1st
    #     price **-145**
    #     _Record: 47—21 (69.1%) · +88.89u at quarter-Kelly._
    #
    # Both halves are false together. There was no bet, so there is no
    # win; and the record printed underneath EXCLUDES this game (both
    # `select_top_picks` and dashboard/lib/top-pick.ts drop a night whose
    # stake is zero), so the line implies an inclusion that did not
    # happen. Claiming a win nobody staked is the oldest trick in paid
    # picks; printing it above an unchanged record is also the version a
    # subscriber can catch, which makes it worse, not better.
    #
    # On a LOSS it fails the other way: the ping says the No.1 lost while
    # the record does not move, so anyone reconciling the two finds the
    # record understating losses. Silence is not an option either -- see
    # the loss-ping reasoning above -- so the honest message is that the
    # game had no action, which is what actually happened.
    if is_refused(r) and g in ("WIN", "LOSS"):
        happened = (("a run scored in the 1st" if side == "YRFI"
                     else "no run scored in the 1st") if g == "WIN" else
                    ("no run scored in the 1st" if side == "YRFI"
                     else "a run scored in the 1st"))
        L = ["# THE №1 — NO ACTION", ""]
        L.append(f"**{away} @ {home}** · **{side}** — {happened}, so the "
                 f"pick would have {'won' if g == 'WIN' else 'lost'}.")
        L.append("")
        L.append("**No bet was placed.** The price moved past our number "
                 "before it locked and the system staked nothing, so this "
                 "is not counted either way — the record below is "
                 "unchanged by it.")
        rec = _top_pick_record_line(date_iso)
        if rec:
            L.append("")
            L.append(f"_{rec}._")
        return "\n".join(L)

    L: list[str] = []
    if g == "WIN":
        L.append("# ✅ THE №1 WON")
        happened = ("a run scored in the 1st" if side == "YRFI"
                    else "no run scored in the 1st")
    elif g == "LOSS":
        L.append("# ❌ THE №1 LOST")
        happened = ("no run scored in the 1st" if side == "YRFI"
                    else "a run scored in the 1st")
    else:
        # POSTPONED / SUSPENDED / VOID / PASS -- no bet stands. Say so
        # plainly rather than dressing it as a result.
        L.append("# THE №1 — NO ACTION")
        L.append("")
        L.append(f"**{away} @ {home}** · {side} — **{g.title() or 'no result'}**. "
                 f"No bet stands, and nothing is added to the record.")
        return "\n".join(L)

    L.append("")
    L.append(f"**{away} @ {home}** · **{side}** — {happened}")
    L.append("")
    bits = []
    if odds is not None:
        bits.append(f"price **{fmt_odds(odds)}**")
    if stake:
        bits.append(f"staked **{_fmt_units(stake)}u**")
    if pnl is not None:
        bits.append(f"**{pnl:+.2f}u**")
    if bits:
        L.append(" · ".join(bits))

    rec = _top_pick_record_line(date_iso)
    if rec:
        L.append("")
        L.append(f"_{rec}._")
    L.append("")
    L.append("_Full slate results to follow once every first inning is in._")
    return "\n".join(L)


def build_final_results(date_iso: str, rows: list[dict]) -> str:
    """BROADCAST 3 -- fires once every first inning on the board is graded.

    NOT when the last game ends. Every pick here is decided in the FIRST
    INNING, so the results are known hours before the last out. Waiting
    for the final out on 2026-08-05 would have delayed this message from
    ~22:00 ET to ~00:45 ET and changed not one number in it.
    """
    plays = [r for r in rows if is_strong(r)]
    tp = top_pick(rows)

    def graded(r: dict) -> str:
        return (r.get("graded_result") or "").strip().upper()

    settled = [r for r in plays if graded(r) in ("WIN", "LOSS")]
    wins = [r for r in settled if graded(r) == "WIN"]

    L: list[str] = []
    L.append(f"# FINAL RESULTS · {_long_date(date_iso)}")
    L.append("_Every first inning on the board is complete._")

    if tp is not None:
        g = graded(tp)
        pnl = _f(tp.get("profit_loss_units"))
        stake = _f(tp.get("units_risked"))
        mark = "✅ WON" if g == "WIN" else ("❌ LOST" if g == "LOSS" else g or "—")
        L.append("")
        L.append("## The №1 play")
        line = (f"**{(tp.get('away_team') or '').upper()} @ "
                f"{(tp.get('home_team') or '').upper()}** · "
                f"{(tp.get('pick_side') or '').upper()} — **{mark}**")
        if pnl is not None and g in ("WIN", "LOSS"):
            line += f"  ({pnl:+.2f}u on {stake:.0f}u)" if stake else f"  ({pnl:+.2f}u)"
        L.append(line)

    if len(plays) > 1:
        L.append("")
        L.append("## Every play")
        for r in plays:
            g = graded(r)
            mark = "✅" if g == "WIN" else ("❌" if g == "LOSS" else "·")
            pnl = _f(r.get("profit_loss_units"))
            tail = f"  {pnl:+.2f}u" if pnl is not None and g in ("WIN", "LOSS") else f"  {g.title() or 'pending'}"
            L.append(f"{mark} `{(r.get('away_team') or '').upper():>3} @ "
                     f"{(r.get('home_team') or '').upper():<3}` "
                     f"{(r.get('pick_side') or '').upper():<4}{tail}")

    if settled:
        day_pnl = sum(_f(r.get("profit_loss_units")) or 0.0 for r in settled)
        L.append("")
        L.append(f"**Tonight: {len(wins)}-{len(settled)-len(wins)} · "
                 f"{day_pnl:+.2f}u**")
    elif plays:
        L.append("")
        L.append("_No play settled tonight._")
    else:
        L.append("")
        L.append("_No play tonight. The model declined every game._")

    L.append("")
    L.append("_1 unit = 1% of your bankroll · quarter-Kelly._")
    return "\n".join(L)


def build_ledger(date_iso: str) -> str | None:
    """BROADCAST 4 -- the No.1 pick's running record, straight after
    FINAL RESULTS.

    EVERY FIGURE COMES FROM tools/pl_calc.py --top-pick, which is
    gated to reproduce the dashboard exactly (45-21 / +81.76u / 329.00u
    staked / +21.86u realized as of 2026-08-05). CLAUDE.md forbids
    quoting a P&L number from anywhere else, and this is the message
    where that rule matters most: it is the one subscribers judge.

    Returns None when the series cannot be computed, so the caller can
    skip the post rather than publish a zero.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_plc", str(ROOT / "tools" / "pl_calc.py"))
        plc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(spec and plc)  # type: ignore[arg-type]
        rows, _src = plc._load_rows(int(date_iso[:4]))
        picks = [p for p in plc.select_top_picks(rows) if p["date"] <= date_iso]
        if not picks:
            return None
        s = plc.top_pick_summary(picks)
    except Exception:  # noqa: BLE001 -- never break the night's posts
        return None
    if not s or not s.get("bets"):
        return None

    L: list[str] = []
    L.append("# THE №1 PICK — RUNNING RECORD")
    # DISCLOSE THE BASIS. "Every night's top play" was true of the
    # arithmetic and misleading about the population: this series is the
    # top YRFI play of each night since the live weights were fit, and
    # STRONG NRFI is excluded outright because it was switched off
    # 2026-06-07 for losing in every band. On 15 of 92 nights the overall
    # top play was an NRFI pick and the best YRFI play stands in its
    # place, so the record is what TODAY'S rules would have produced --
    # not a transcript of what was alerted at the time.
    #
    # The operator's reasoning for the rule is sound (2026-08-03:
    # "showing them as the record of a system that would not place them
    # is simply wrong") and it is not being changed. What changes is that
    # a subscriber can now tell which of the two they are reading, which
    # is the difference between a modelled record and a misrepresented
    # one.
    L.append(f"_The top YRFI play of every night since "
             f"{plc.CURRENT_SYSTEM_FROM}, when the live model was fit, "
             f"sized by today's rules. NRFI is excluded — it was switched "
             f"off {plc.NRFI_OFF_FROM} for losing._")
    L.append("")
    L.append(f"## {s['wins']}—{s['losses']}  ·  {s['hit']:.1f}%")
    L.append(f"**{s['atKelly']:+.2f} units** at quarter-Kelly — "
             f"the stake the system publishes")
    L.append(f"{s['roiKelly']:+.1f}% per unit risked "
             f"({s['stakedKelly']:.0f}u staked over {s['bets']} plays)")
    L.append("")
    L.append(f"_At a flat 1 unit a night: {s['atFlat1u']:+.2f}u — the same "
             f"picks with stake size taken out._")
    if s.get("noEdgeUnderKelly"):
        n = s["noEdgeUnderKelly"]
        L.append(f"_{n} night{'s' if n != 1 else ''} excluded: the price "
                 f"moved past the point where the rule stakes anything._")
    L.append("")
    L.append("_1 unit = 1% of your bankroll, so these numbers mean the same "
             "on any bank. Nothing compounds._")
    return "\n".join(L)


# ---------------------------------------------------------------------------
# THE SCHEDULER -- which broadcast is due right now
# ---------------------------------------------------------------------------
#
# MODE, and why the default is not "live". DISCORD_BROADCASTS:
#     off      do nothing
#     preview  evaluate every trigger and LOG the exact message, send
#              nothing  <-- DEFAULT
#     live     actually post
# A broadcast that has never been seen is not finished. This repo has
# shipped code that never fired more than once (tools/fetch_odds_api.py,
# the Healthchecks ping), and the failure mode here is worse because it
# is in front of paying subscribers. Preview runs the whole path -- real
# slate, real triggers, real rendering -- into the Railway logs first.
#
# DEDUPE KEYS ARE PER-SLATE AND SUPABASE-BACKED. Railway redeploys
# mid-evening (it did on 2026-08-06, twice). An in-process "already
# sent" set would re-post the entire board after every restart, so the
# key must be derived only from the slate, never from process state.

def _mode() -> str:
    m = (os.environ.get("DISCORD_BROADCASTS", "") or "preview").strip().lower()
    return m if m in ("off", "preview", "live") else "preview"


def _terminal(r: dict) -> bool:
    """Has this row reached a state that will not change tonight?"""
    return (r.get("graded_result") or "").strip().upper() in (
        "WIN", "LOSS", "PASS", "POSTPONED", "SUSPENDED", "CANCELLED", "VOID")


def due_broadcasts(date_iso: str, rows: list[dict],
                   now: datetime) -> list[tuple[str, str, str]]:
    """[(event_type, event_key, body)] for everything due at `now` (ET).

    Order matters: FINAL RESULTS must be recorded before THE LEDGER, so
    the record message can never appear above the results it summarises.
    """
    out: list[tuple[str, str, str]] = []
    if not rows:
        return out

    fp = first_pitch_of_slate(rows, date_iso)

    # 1 -- THE BOARD, at T-60 before the FIRST game. Also gated on the
    # slate having actually started forming: a board posted before the
    # predictor has run would be an empty table with our name on it.
    if fp is not None and now >= fp - timedelta(minutes=LOCK_MINUTES_PREGAME):
        if any((r.get("pick_strength") or "").strip() for r in rows):
            # Pass `now` THROUGH. build_board is time-aware (it refuses
            # to list games that already started) and defaults to
            # wall-clock, so omitting it here made the trigger and the
            # CONTENT read two different clocks. In production they
            # coincide and nothing looked wrong, which is exactly why
            # this is worth being explicit about: it made `--at` render a
            # board for the wrong moment, so every dry run of a past or
            # future trigger silently rehearsed the wrong message.
            out.append(("discord_board", f"board:{date_iso}",
                        build_board(date_iso, rows, now)))

    # 2 -- THE No.1 PLAY, at ITS OWN lock (60 min before ITS game), which
    # is a different time from (1) unless the No.1 IS the first game.
    # Keyed on the GAME, not just the date: if the No.1 flips during the
    # afternoon the new game gets its own announcement rather than being
    # silently swallowed by an already-used date key.
    #
    # A REFUSED No.1 GETS THE OTHER MESSAGE, NOT THIS ONE (2026-08-10).
    # `top_pick` ranks by conviction and never asks whether money went
    # down, so on a night quarter-Kelly declined it this fired anyway and
    # published a padlocked "TONIGHT'S №1 PLAY" carrying a price limit for
    # a bet the system had refused. Routing it here rather than softening
    # the message is the operator's call: a message headlined as the
    # night's play should only exist when there is one.
    #
    # SEPARATE EVENT TYPE AND SEPARATE KEY, DELIBERATELY. Reusing
    # `toppick:` would let one shape suppress the other through the 24h
    # dedupe -- a No.1 that locks, is announced, then has its price move
    # would find the no-play notice already "sent". They are different
    # claims about the night and each must be able to reach the channel.
    # `discord_noplay` is registered in tracker._DEDUP_WINDOW_M; an
    # unregistered type inherits a 5-MINUTE window and would republish
    # roughly twelve times an hour (the 2026-08-06 board incident).
    tp = top_pick(rows)
    if tp is not None:
        st = game_start_et(date_iso, tp.get("game_time_et", ""))
        if st is not None and now >= st - timedelta(minutes=LOCK_MINUTES_PREGAME):
            gid = (tp.get("game_pk") or
                   f"{tp.get('away_team','')}@{tp.get('home_team','')}")
            if is_refused(tp):
                out.append(("discord_noplay", f"noplay:{date_iso}:{gid}",
                            build_no_play(date_iso, tp, rows)))
            else:
                out.append(("discord_toppick", f"toppick:{date_iso}:{gid}",
                            build_top_pick(date_iso, tp, rows)))

    # 2b -- THE No.1 SETTLED, the moment that ONE row grades. Ordered
    # BEFORE final so that on a night whose No.1 is also the last game,
    # the headline lands above the summary rather than under it.
    #
    # No time backstop is needed and none is wanted: unlike FINAL
    # RESULTS this makes no claim about the rest of the slate, so it
    # cannot say "everything is complete" while games are pending. Its
    # only gate is that this row reached a terminal state, which is the
    # event being announced.
    if tp is not None and _terminal(tp):
        gid = (tp.get("game_pk") or
               f"{tp.get('away_team','')}@{tp.get('home_team','')}")
        out.append(("discord_settled", f"settled:{date_iso}:{gid}",
                    build_top_pick_settled(date_iso, tp)))

    # 3 -- FINAL RESULTS, once every row on the board is terminal.
    # NOT when the last game ends: every pick is decided in the 1st, so
    # results are final hours earlier (2026-08-05: last row graded 21:57
    # ET, last out ~00:45 ET).
    #
    # THE TIME BACKSTOP IS NOT REDUNDANT. "All rows terminal" is normally
    # reached only after play, but it is also trivially true for a
    # degenerate slate -- every game POSTPONED, or a stale/re-read board.
    # Replaying 2026-08-05 in the simulator, FINAL and LEDGER came due at
    # 11:00 AM, three hours before first pitch, because the historical
    # rows were already graded. In production that shape means "results
    # before the games", which is the single most damaging thing this
    # surface could publish. So it must ALSO be past the first pitch plus
    # one inning (~20 min) before results can be announced.
    # THE BACKSTOP ANCHORS TO THE LAST GAME, NOT THE FIRST. It was
    # written against `fp` (first pitch), which is only a real guard on a
    # one-game card. Simulating a full slate with every row graded, FINAL
    # RESULTS and THE LEDGER both came due at 12:55 PM -- twenty minutes
    # after the FIRST pitch of an 11-game card whose last game started at
    # 9:40 PM -- and published "Every first inning on the board is
    # complete" over ten games that had not started.
    #
    # In normal operation the `all(_terminal)` test hides this, because
    # rows only grade after their own first inning. It bites exactly when
    # that test goes trivially true for the wrong reason: a mass
    # postponement (POSTPONED counts as terminal), a stale re-read of a
    # finished slate, or any grader bug that fills the column early --
    # which is the same shape as the 2026-08-05 replay that motivated the
    # backstop in the first place. The original fix was right in kind and
    # one game short in degree.
    lp = last_pitch_of_slate(rows, date_iso)
    if (rows and lp is not None
            and now >= lp + timedelta(minutes=20)
            and all(_terminal(r) for r in rows)):
        out.append(("discord_final", f"final:{date_iso}",
                    build_final_results(date_iso, rows)))

        # 4 -- THE LEDGER, immediately after. Separate message, separate
        # key, so a failure of one cannot suppress the other.
        led = build_ledger(date_iso)
        if led:
            out.append(("discord_ledger", f"ledger:{date_iso}", led))

    return out


def run_broadcasts(date_iso: str | None = None,
                   now: datetime | None = None,
                   mode: str | None = None) -> int:
    """Evaluate and dispatch. Returns how many were sent (or previewed).

    NEVER RAISES. This runs inside workers/predictor_loop.py, in the
    same process as predict / grade / odds / lock.
    """
    try:
        mode = (mode or _mode())
        if mode == "off":
            return 0
        now = now or datetime.now(ET)
        date_iso = date_iso or now.strftime("%Y-%m-%d")
        rows = load_slate(date_iso)
        due = due_broadcasts(date_iso, rows, now)
        if not due:
            return 0

        import discord_notify
        sent = 0
        for event_type, event_key, body in due:
            if not body:
                continue
            ok = discord_notify.send(
                body, event_type=event_type, event_key=event_key,
                preview=(mode == "preview"),
            )
            if ok:
                sent += 1
                print(f"  [discord] {mode}: {event_type} ({event_key})",
                      flush=True)
        return sent
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        print(f"  [discord] broadcast run failed ({exc!r})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Discord broadcast preview / send")
    ap.add_argument("--date", help="Slate date (default: today ET)")
    ap.add_argument("--at", help="Pretend it is this ET time, e.g. '18:30'")
    ap.add_argument("--mode", choices=["off", "preview", "live"],
                    help="Override DISCORD_BROADCASTS")
    ap.add_argument("--which",
                    choices=["board", "toppick", "noplay", "settled",
                             "final", "ledger"],
                    help="Render one message regardless of its trigger")
    ap.add_argument("--resend",
                    choices=["board", "toppick", "noplay", "settled",
                             "final", "ledger"],
                    help="Deliberately re-publish one broadcast, bypassing "
                         "the 24h dedupe. For replacing a post you have "
                         "already retracted -- never for routine sending.")
    ap.add_argument("--yes", action="store_true",
                    help="Required by --resend: confirms you mean to post "
                         "to the live subscriber channel")
    a = ap.parse_args()

    d = a.date or datetime.now(ET).strftime("%Y-%m-%d")
    slate = load_slate(d)

    if a.resend:
        # A human replacing a retracted post. The scheduler never reaches
        # this branch; run_broadcasts() has no way to set force=True.
        tp = top_pick(slate)
        body = {
            "board":   lambda: build_board(d, slate),
            "toppick": lambda: build_top_pick(d, tp, slate) if tp else "",
            "noplay":  lambda: build_no_play(d, tp, slate) if tp else "",
            "settled": lambda: build_top_pick_settled(d, tp) if tp else "",
            "final":   lambda: build_final_results(d, slate),
            "ledger":  lambda: build_ledger(d) or "",
        }[a.resend]()
        key = {
            "board":   f"board:{d}",
            "toppick": f"toppick:{d}:{tp.get('game_pk') if tp else '?'}",
            "noplay":  f"noplay:{d}:{tp.get('game_pk') if tp else '?'}",
            "settled": f"settled:{d}:{tp.get('game_pk') if tp else '?'}",
            "final":   f"final:{d}",
            "ledger":  f"ledger:{d}",
        }[a.resend]
        event_type = f"discord_{a.resend}"
        if not body:
            print(f"[discord] nothing to send for {a.resend}")
            raise SystemExit(1)
        if not a.yes:
            print(f"=== would re-send {event_type} / {key} "
                  f"({len(body)} chars) ===\n")
            print(body)
            print("\n=== re-run with --yes to publish to the LIVE channel ===")
            raise SystemExit(0)
        import discord_notify
        ok = discord_notify.send(body, event_type=event_type, event_key=key,
                                 force=True)
        print(f"[discord] re-sent {event_type}: delivered={ok}")
        raise SystemExit(0 if ok else 1)

    if a.which:
        body = {
            "board":   lambda: build_board(d, slate),
            "toppick": lambda: (build_top_pick(d, top_pick(slate), slate)
                                if top_pick(slate) else "(no No.1 play)"),
            "noplay":  lambda: (build_no_play(d, top_pick(slate), slate)
                                if top_pick(slate) else "(no No.1 play)"),
            "settled": lambda: (build_top_pick_settled(d, top_pick(slate))
                                if top_pick(slate) else "(no No.1 play)"),
            "final":   lambda: build_final_results(d, slate),
            "ledger":  lambda: build_ledger(d) or "(no ledger)",
        }[a.which]()
        print(body)
        raise SystemExit(0)

    when = datetime.now(ET)
    if a.at:
        hh, mm = (int(x) for x in a.at.split(":"))
        when = when.replace(hour=hh, minute=mm, second=0, microsecond=0)
    print(f"[discord] slate {d}, {len(slate)} games, evaluating at "
          f"{when.strftime('%I:%M %p ET').lstrip('0')}, mode={a.mode or _mode()}")
    n = run_broadcasts(d, when, a.mode)
    print(f"[discord] {n} broadcast(s) due")
