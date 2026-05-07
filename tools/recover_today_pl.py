"""Recompute profit_loss_units for graded picks against their actual
market_*_odds in Supabase.  Run via `railway run` so SUPABASE creds are
in env.  Cleans up rows where a stale-CSV cycle mirrored the -110
fallback over the worker's correct real-odds value.

Usage:
  python tools/recover_today_pl.py                # ET today
  python tools/recover_today_pl.py 2026-05-04     # specific date
  python tools/recover_today_pl.py --all          # every date with graded rows
"""
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, ".")
from db.supabase_writer import _get_client


def payout_per_unit(odds_str):
    if not odds_str:
        return None
    try:
        o = float(str(odds_str).strip())
    except (ValueError, TypeError):
        return None
    if o == 0:
        return None
    return o / 100.0 if o > 0 else 100.0 / abs(o)


def correct_pl(row: dict) -> float | None:
    grade = row.get("graded_result")
    side = row.get("pick_side")
    if grade not in ("WIN", "LOSS") or side not in ("NRFI", "YRFI"):
        return None
    units_raw = row.get("units_risked")
    units = float(units_raw) if units_raw not in (None, "") else 1.0
    if units <= 0:
        return None
    if grade == "LOSS":
        return round(-units, 3)
    odds_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
    ppu = payout_per_unit(row.get(odds_col))
    if ppu is None:
        ppu = 100.0 / 110.0
    return round(units * ppu, 3)


def scan_and_fix(client, date_iso: str) -> tuple[int, int, float]:
    """Returns (n_scanned, n_fixed, net_delta_units)."""
    res = (client.table("picks_2026")
                  .select(
                      "game_pk, away_team, home_team, pick_side, "
                      "graded_result, market_nrfi_odds, market_yrfi_odds, "
                      "units_risked, profit_loss_units"
                  )
                  .eq("date", date_iso)
                  .execute())
    fixes = []
    delta = 0.0
    for r in res.data:
        c = correct_pl(r)
        if c is None:
            continue
        cur_raw = r.get("profit_loss_units")
        cur = float(cur_raw) if cur_raw is not None else None
        if cur != c:
            odds_col = "market_nrfi_odds" if r["pick_side"] == "NRFI" else "market_yrfi_odds"
            print(f"  FIX {date_iso} {r['away_team']}@{r['home_team']:<3} "
                  f"{r['pick_side']:<5} {r['graded_result']:<5} "
                  f"odds={r.get(odds_col)!r:<7} cur={cur!r:<7} -> {c}")
            fixes.append((r["game_pk"], c))
            delta += (c - (cur or 0.0))
    for gp, pl in fixes:
        (client.table("picks_2026")
                .update({"profit_loss_units": pl})
                .eq("date", date_iso)
                .eq("game_pk", gp)
                .execute())
    return len(res.data), len(fixes), delta


def list_graded_dates(client) -> list[str]:
    """Distinct dates that have at least one graded row."""
    res = (client.table("picks_2026")
                  .select("date")
                  .in_("graded_result", ["WIN", "LOSS"])
                  .execute())
    return sorted({r["date"] for r in res.data if r.get("date")})


def main(arg: str | None) -> int:
    client = _get_client()
    if client is None:
        print("Supabase env vars not set; cannot recover.", file=sys.stderr)
        return 1

    if arg == "--all":
        dates = list_graded_dates(client)
        print(f"Scanning {len(dates)} graded date(s)...")
    elif arg:
        dates = [arg]
    else:
        dates = [datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")]

    total_scanned = total_fixed = 0
    total_delta = 0.0
    for d in dates:
        scanned, fixed, delta = scan_and_fix(client, d)
        total_scanned += scanned
        total_fixed += fixed
        total_delta += delta
        if fixed:
            print(f"  {d}: scanned {scanned}, fixed {fixed}, "
                  f"net delta {delta:+.3f}u")
    print(f"\nTotal: scanned {total_scanned}, fixed {total_fixed}, "
          f"net delta {total_delta:+.3f}u")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
