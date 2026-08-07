#!/usr/bin/env python3
"""
discord_broadcasts.py -- the four subscriber messages, and the rules
that keep them honest.

    1. THE BOARD        slate T-60   every game, model probability, ladders
    2. THE No.1 PLAY    its own lock the one bet, priced, with its ladder
    3. FINAL RESULTS    all graded    how every pick did
    4. THE No.1 LEDGER  after (3)     record + units at quarter-Kelly

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
DASH = "https://nrfi-terminal.vercel.app"

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
    same price. Neither was a formula bug -- both implementations are
    identical. The raw quarter-Kelly stake was **3.4975 units**, sitting
    a hair under the 3.5 rounding boundary, and the two surfaces fed it
    probabilities of different PRECISION:

        dashboard  yrfiPct 63.4   (1 decimal)  -> 3.4975 -> 3u
        discord    0.6343         (full)       -> 3.5151 -> 4u

    A difference of 0.0003 in the input moved the published stake by a
    whole unit. Any surface that recomputes will keep landing on the
    wrong side of some boundary, forever, because rounding boundaries
    are dense. The only stable answer is for every surface to PRINT THE
    SAME STORED NUMBER -- the one the system actually staked.

    Recomputation stays as a fallback ONLY for a row the ledger has not
    priced yet (so a pre-lock board can still show an indicative size),
    and that path is the one place a boundary disagreement can still
    appear. It is bounded to rows carrying no committed stake.
    """
    booked = _f(row.get("units_risked"))
    if booked is not None and booked > 0:
        return booked

    p, _ = published_probability(row)
    odds = side_price(row)
    if p is None or odds is None:
        return None
    tracker = _tracker()
    if tracker is None:
        return None
    try:
        return tracker.kelly_stake_units(p, str(int(odds)))
    except (Exception, SystemExit):  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# the price ladder -- book-free, so it works at ANY sportsbook
# ---------------------------------------------------------------------------

def price_ladder(p: float, max_rungs: int = 6) -> list[tuple[int, float]]:
    """[(american_price, units)] walking the price worse until the stake
    falls under a unit, then the pass line.

    Mirrors dashboard/lib/price-ladder.ts. This is THE thing to publish
    to subscribers: it is computed from the model probability alone, so a
    reader on any book can look up their own number. That is why the
    board can be useful even on the 85% of games with no captured price.
    """
    tracker = _tracker()
    if tracker is None:
        return []
    out: list[tuple[int, float]] = []
    seen: set[float] = set()
    o = -100
    while o > -600 and len(out) < max_rungs:
        u = tracker.kelly_stake_units(p, str(o))
        if u is None or u < 1.0:
            break
        if u not in seen:
            out.append((o, u))
            seen.add(u)
        o -= 5
    return out


def pass_price(p: float) -> int | None:
    """Worst price still worth a full unit; below this, no bet."""
    tracker = _tracker()
    if tracker is None:
        return None
    o = -100
    last = None
    while o > -600:
        u = tracker.kelly_stake_units(p, str(o))
        if u is None or u < 1.0:
            return last
        last = o
        o -= 5
    return last


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


def is_strong(row: dict) -> bool:
    return (row.get("pick_strength") or "").strip().upper() == "STRONG"


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


def build_board(date_iso: str, rows: list[dict]) -> str:
    """BROADCAST 1 -- the whole slate, an hour before the first game."""
    fp = first_pitch_of_slate(rows, date_iso)
    plays  = [r for r in rows if is_strong(r)]
    leans  = [r for r in rows if (r.get("pick_strength") or "").upper() == "LEAN"]
    others = [r for r in rows if r not in plays and r not in leans]

    L: list[str] = []
    L.append(f"# THE BOARD · {_long_date(date_iso)}")
    L.append(f"{len(rows)} games · first pitch {_hm(fp) if fp else '--'} ET")

    if plays:
        L.append("")
        L.append("## " + ("THE PLAY" if len(plays) == 1 else f"THE PLAYS ({len(plays)})"))
        for r in plays:
            p, _ = published_probability(r)
            side = (r.get("pick_side") or "").upper()
            st = game_start_et(date_iso, r.get("game_time_et", ""))
            L.append("")
            L.append(f"**{(r.get('away_team') or '').upper()} @ "
                     f"{(r.get('home_team') or '').upper()}** · "
                     f"{_hm(st) if st else '--'} ET · **{side}** — {_side_words(side)}")
            if p is not None:
                odds = side_price(r)
                if odds is not None:
                    L.append(f"Model **{p*100:.1f}%** · price {fmt_odds(odds)} "
                             f"needs {american_to_implied(odds)*100:.1f}%")
                else:
                    L.append(f"Model **{p*100:.1f}%** · price not captured yet")
                stake = stake_for(r)
                pa = pass_price(p)
                if stake:
                    line = f"**Stake {stake:.0f}u**"
                    if pa is not None:
                        line += f" · don't take worse than **{fmt_odds(pa)}**"
                    L.append(line)
    else:
        L.append("")
        L.append("## NO PLAY TONIGHT")
        L.append("The model looked at every game and declined them all. "
                 "A quiet night is a correct outcome, not a missing message.")

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

    priced = sum(1 for r in rows if side_price(r) is not None)
    L.append("")
    L.append(f"_Prices captured for {priced} of {len(rows)} games so far — "
             f"most books post first-inning lines close to game time. "
             f"The ladder above needs no price: look up your own book's "
             f"number and bet the matching stake._")
    L.append(f"_1 unit = 1% of your bankroll. <{DASH}/>_")
    return "\n".join(L)


def build_top_pick(date_iso: str, r: dict) -> str:
    """BROADCAST 2 -- fires at the No.1 pick's own lock."""
    side = (r.get("pick_side") or "").upper()
    st = game_start_et(date_iso, r.get("game_time_et", ""))
    p, reconciled = published_probability(r)
    odds = side_price(r)
    stake = stake_for(r)

    L: list[str] = []
    L.append("# 🔒 TONIGHT'S No.1 PLAY")
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
            L.append(f"### Stake {stake:.0f} units")
        pa = pass_price(p)
        if pa is not None:
            L.append(f"Don't take worse than **{fmt_odds(pa)}**.")
    L.append("_1 unit = 1% of your bankroll._")
    L.append(f"<{DASH}/brief>")
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
        L.append("## The No.1 play")
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
    L.append("# THE No.1 PICK — RUNNING RECORD")
    L.append(f"_Every night's top play since {plc.CURRENT_SYSTEM_FROM}, "
             f"when the live model was fit._")
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
    L.append(f"<{DASH}/history>")
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
            out.append(("discord_board", f"board:{date_iso}",
                        build_board(date_iso, rows)))

    # 2 -- THE No.1 PLAY, at ITS OWN lock (60 min before ITS game), which
    # is a different time from (1) unless the No.1 IS the first game.
    # Keyed on the GAME, not just the date: if the No.1 flips during the
    # afternoon the new game gets its own announcement rather than being
    # silently swallowed by an already-used date key.
    tp = top_pick(rows)
    if tp is not None:
        st = game_start_et(date_iso, tp.get("game_time_et", ""))
        if st is not None and now >= st - timedelta(minutes=LOCK_MINUTES_PREGAME):
            gid = (tp.get("game_pk") or
                   f"{tp.get('away_team','')}@{tp.get('home_team','')}")
            out.append(("discord_toppick", f"toppick:{date_iso}:{gid}",
                        build_top_pick(date_iso, tp)))

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
    if (rows and fp is not None
            and now >= fp + timedelta(minutes=20)
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
    ap.add_argument("--which", choices=["board", "toppick", "final", "ledger"],
                    help="Render one message regardless of its trigger")
    a = ap.parse_args()

    d = a.date or datetime.now(ET).strftime("%Y-%m-%d")
    slate = load_slate(d)

    if a.which:
        body = {
            "board":   lambda: build_board(d, slate),
            "toppick": lambda: (build_top_pick(d, top_pick(slate))
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
