"""One-shot: recompute profit_loss_units for today's graded picks against
their actual market_*_odds in Supabase.  Run via `railway run` so the
SUPABASE creds are in env.  Cleans up rows where a stale-CSV cycle
mirrored the -110 fallback over the worker's correct real-odds value."""
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


def main(date_iso: str | None = None) -> int:
    if not date_iso:
        date_iso = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    client = _get_client()
    if client is None:
        print("Supabase env vars not set; cannot recover.", file=sys.stderr)
        return 1
    res = (client.table("picks_2026")
                  .select(
                      "game_pk, away_team, home_team, pick_side, "
                      "graded_result, market_nrfi_odds, market_yrfi_odds, "
                      "units_risked, profit_loss_units"
                  )
                  .eq("date", date_iso)
                  .execute())
    fixes = []
    print(f"Scanning {len(res.data)} rows for {date_iso}")
    for r in res.data:
        grade = r.get("graded_result")
        side = r.get("pick_side")
        if grade not in ("WIN", "LOSS") or side not in ("NRFI", "YRFI"):
            continue
        units = float(r["units_risked"]) if r.get("units_risked") else 1.0
        odds_col = "market_nrfi_odds" if side == "NRFI" else "market_yrfi_odds"
        odds = r.get(odds_col)
        if grade == "LOSS":
            correct = round(-units, 3)
        else:
            ppu = payout_per_unit(odds)
            if ppu is None:
                ppu = 100.0 / 110.0
            correct = round(units * ppu, 3)
        current_raw = r.get("profit_loss_units")
        current = float(current_raw) if current_raw is not None else None
        match = "OK" if current == correct else "FIX"
        print(f"  {match} {r['away_team']}@{r['home_team']} {side} {grade} "
              f"odds={odds} cur={current} correct={correct}")
        if current != correct:
            fixes.append((r["game_pk"], correct))
    print(f"\n{len(fixes)} row(s) need fixing")
    for game_pk, pl in fixes:
        (client.table("picks_2026")
                .update({"profit_loss_units": pl})
                .eq("date", date_iso)
                .eq("game_pk", game_pk)
                .execute())
        print(f"  patched game_pk={game_pk} -> {pl}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
