"""Compare CSV vs Supabase for ALL STRONG rows.  Lists rows where
graded_result, profit_loss_units, or units_risked differ."""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db.supabase_writer import _get_client


def _norm_pl(v):
    """Normalize PL representation across CSV (str) and Supabase (numeric)."""
    if v is None or v == "":
        return None
    try:
        return round(float(v), 4)
    except (ValueError, TypeError):
        return None


def main():
    client = _get_client()
    if client is None:
        print("Supabase env vars not set", file=sys.stderr); return 1
    sb_rows = (client.table("picks_2026")
                      .select("date, game_pk, away_team, home_team, "
                              "pick_side, pick_strength, "
                              "graded_result, profit_loss_units, units_risked, bet_placed")
                      .execute().data) or []
    sb = {(r["date"], str(r["game_pk"])): r for r in sb_rows}

    with open(ROOT / "data" / "picks_2026.csv", encoding="utf-8") as f:
        csv_rows = list(csv.DictReader(f))

    diff = []
    for r in csv_rows:
        if (r.get("pick_strength") or "").strip().upper() != "STRONG":
            continue
        key = (r["date"], str(r["game_pk"]))
        s = sb.get(key)
        if s is None: continue
        csv_g = (r.get("graded_result") or "").strip().upper()
        sb_g = ((s.get("graded_result") or "")).strip().upper() if s.get("graded_result") else ""
        csv_pl = _norm_pl(r.get("profit_loss_units"))
        sb_pl = _norm_pl(s.get("profit_loss_units"))
        csv_bp = (r.get("bet_placed") or "").strip().upper()
        sb_bp = (s.get("bet_placed") or "").strip().upper() if s.get("bet_placed") else ""
        if csv_g != sb_g or csv_pl != sb_pl or csv_bp != sb_bp:
            diff.append((r, s))

    print(f"CSV STRONG rows compared: {sum(1 for r in csv_rows if (r.get('pick_strength') or '').strip().upper() == 'STRONG')}")
    print(f"Differing rows: {len(diff)}")
    for r, s in diff:
        print(f"  {r['date']} {r['away_team']}@{r['home_team']:<3} {r['pick_side']:<5}")
        print(f"    CSV: grade={r['graded_result']!r:<10} bet={r['bet_placed']!r:<3} pl={r['profit_loss_units']!r}")
        print(f"     SB: grade={s.get('graded_result')!r:<10} bet={s.get('bet_placed')!r:<3} pl={s.get('profit_loss_units')!r}")


if __name__ == "__main__":
    sys.exit(main() or 0)
