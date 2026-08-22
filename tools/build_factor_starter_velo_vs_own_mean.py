"""
FACTOR: starter_velo_vs_own_mean

For each starting pitcher, season-to-date and STRICTLY BEFORE the game date D:

    value = mean(release_speed | inning == 1, pitch_type in {FF,SI,FC})
          - mean(release_speed | any inning,  pitch_type in {FF,SI,FC})

Negative => he throws slower in the first inning than he does across the
whole game (a cold-start penalty).  Fastballs only, so a change in pitch
MIX cannot masquerade as a change in velocity.

Emit EMPTY STRING unless the pitcher has >= MIN_I1_FB first-inning fastballs
accumulated strictly before D within the same season.

BUILD ONLY.  No evaluation of any kind happens in this file.
"""

import csv
import glob
import gzip
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

ROOT = r"C:/Users/Pinellas Liquidation/MLB-first-inning"
STATCAST_GLOB = os.path.join(ROOT, "data/cache/statcast_zone/*.csv.gz")
OUT_PATH = os.path.join(ROOT, "data/candidates/factor_starter_velo_vs_own_mean.csv")

GAME_FILES = [
    os.path.join(ROOT, "data/picks_2026.csv"),
    os.path.join(ROOT, "data/backtests/backtest_2024-04-01_to_2024-09-30_truepit_pit.csv"),
    os.path.join(ROOT, "data/backtests/backtest_2025-04-01_to_2025-09-30_truepit_pit.csv"),
]

FASTBALLS = {"FF", "SI", "FC"}
MIN_I1_FB = 50


# ---------------------------------------------------------------- game rows
def load_games():
    """game_pk -> (date, away_pid, home_pid), duplicates resolved to EARLIEST date."""
    pid_cache = json.load(open(os.path.join(ROOT, "data/pitcher_id_cache.json")))

    # name -> id, majority vote, built only from games whose ids we already know.
    name_votes = defaultdict(Counter)
    for path in GAME_FILES[1:]:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                v = pid_cache.get(str(row["game_pk"]))
                if v:
                    name_votes[row["away_pitcher"].strip()][v[0]] += 1
                    name_votes[row["home_pitcher"].strip()][v[1]] += 1
    name2id = {n: c.most_common(1)[0][0] for n, c in name_votes.items()}

    stats = Counter()
    best = {}  # game_pk -> (date, away_pid, home_pid, source)

    def offer(game_pk, date, apid, hpid, source):
        game_pk = str(game_pk).strip()
        prev = best.get(game_pk)
        # DUPLICATE-GAME_PK TRAP: a postponed game reappears under the same
        # game_pk on its makeup date.  Always keep the EARLIEST date.
        if prev is None or date < prev[0]:
            best[game_pk] = (date, apid, hpid, source)

    # --- 2026: picks ledger carries explicit pitcher ids
    with open(GAME_FILES[0], newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            a, h = row["away_pitcher_id"].strip(), row["home_pitcher_id"].strip()
            if not a or not h:
                stats["picks_no_pid"] += 1
                a = a or None
                h = h or None
            offer(row["game_pk"], row["date"].strip(),
                  int(a) if a else None, int(h) if h else None, "picks_2026")

    # --- 2024 / 2025 backtests: ids come from the per-game cache, names as fallback
    for path in GAME_FILES[1:]:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                gp = str(row["game_pk"]).strip()
                v = pid_cache.get(gp)
                if v:
                    apid, hpid = int(v[0]), int(v[1])
                    stats["bt_from_cache"] += 1
                    src = "pitcher_id_cache"
                else:
                    apid = name2id.get(row["away_pitcher"].strip())
                    hpid = name2id.get(row["home_pitcher"].strip())
                    stats["bt_from_name"] += 1
                    if apid is None or hpid is None:
                        stats["bt_name_unresolved"] += 1
                    src = "name_majority"
                offer(gp, row["date"].strip(), apid, hpid, src)

    print("[games] " + str(dict(stats)))
    return best


# ------------------------------------------------------------- statcast pass
def main():
    games = load_games()
    by_date = defaultdict(list)
    for gp, (date, apid, hpid, src) in games.items():
        by_date[date].append((gp, apid, hpid))
    print("[games] distinct game_pk: %d  dates: %d  range %s .. %s"
          % (len(games), len(by_date), min(by_date), max(by_date)))

    sc_files = {os.path.basename(p)[:10]: p for p in sorted(glob.glob(STATCAST_GLOB))}
    print("[statcast] %d day files, %s .. %s"
          % (len(sc_files), min(sc_files), max(sc_files)))

    all_dates = sorted(set(by_date) | set(sc_files))

    # per-season accumulators, reset at the season boundary so nothing pools
    # across a year (and therefore nothing can pool forward in time either).
    i1_n, i1_sum = defaultdict(int), defaultdict(float)
    all_n, all_sum = defaultdict(int), defaultdict(float)
    season = None

    diag = Counter()
    out_rows = []

    for date in all_dates:
        yr = date[:4]
        if yr != season:
            i1_n, i1_sum = defaultdict(int), defaultdict(float)
            all_n, all_sum = defaultdict(int), defaultdict(float)
            season = yr
            print("[season] reset at %s" % date)

        # ---- 1. EMIT using state built strictly from dates BEFORE `date`
        for gp, apid, hpid in sorted(by_date.get(date, [])):
            vals = []
            for pid in (apid, hpid):
                if pid is None:
                    vals.append("")
                    diag["no_pitcher_id"] += 1
                    continue
                n1 = i1_n[pid]
                if n1 < MIN_I1_FB or all_n[pid] <= 0:
                    vals.append("")
                    diag["below_min_sample"] += 1
                    continue
                v = (i1_sum[pid] / n1) - (all_sum[pid] / all_n[pid])
                vals.append("%.4f" % v)
                diag["emitted"] += 1
            out_rows.append((date, gp, vals[0], vals[1]))

        # ---- 2. INGEST this date's pitches (only visible to LATER dates)
        path = sc_files.get(date)
        if not path:
            continue
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            r = csv.reader(fh)
            hdr = next(r)
            ix = {h: i for i, h in enumerate(hdr)}
            c_inn, c_pit, c_typ, c_spd = ix["inning"], ix["pitcher"], ix["pitch_type"], ix["release_speed"]
            for row in r:
                diag["pitches_seen"] += 1
                if row[c_typ] not in FASTBALLS:
                    continue
                spd = row[c_spd]
                if not spd:
                    diag["fb_no_speed"] += 1
                    continue
                try:
                    spd = float(spd)
                except ValueError:
                    diag["fb_bad_speed"] += 1
                    continue
                if not (50.0 <= spd <= 110.0):
                    diag["fb_speed_out_of_range"] += 1
                    continue
                try:
                    pid = int(row[c_pit])
                except ValueError:
                    diag["pitch_no_pitcher"] += 1
                    continue
                all_n[pid] += 1
                all_sum[pid] += spd
                diag["fb_used"] += 1
                if row[c_inn] == "1":
                    i1_n[pid] += 1
                    i1_sum[pid] += spd
                    diag["fb_i1_used"] += 1

    # ------------------------------------------------------------- write out
    out_rows.sort(key=lambda t: (t[0], int(t[1])))
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["date", "game_pk", "away_value", "home_value"])
        w.writerows(out_rows)

    # -------------------------------------------------------------- coverage
    filled = [float(v) for _, _, a, h in out_rows for v in (a, h) if v != ""]
    slots = 2 * len(out_rows)
    games_any = sum(1 for _, _, a, h in out_rows if a != "" or h != "")
    games_both = sum(1 for _, _, a, h in out_rows if a != "" and h != "")

    print("\n[diag] " + json.dumps(dict(diag), indent=1, sort_keys=True))
    print("\n===== COVERAGE =====")
    print("rows (games)            : %d" % len(out_rows))
    print("pitcher slots           : %d" % slots)
    print("slots filled            : %d  (%.2f%%)" % (len(filled), 100.0 * len(filled) / slots))
    print("games with >=1 value    : %d  (%.2f%%)" % (games_any, 100.0 * games_any / len(out_rows)))
    print("games with BOTH values  : %d  (%.2f%%)" % (games_both, 100.0 * games_both / len(out_rows)))
    print("distinct values         : %d" % len(set("%.4f" % v for v in filled)))
    print("stdev                   : %.6f" % statistics.pstdev(filled))
    print("mean                    : %.6f" % statistics.fmean(filled))
    print("min / p05 / med / p95 / max : %.3f / %.3f / %.3f / %.3f / %.3f" % (
        min(filled),
        sorted(filled)[int(0.05 * len(filled))],
        statistics.median(filled),
        sorted(filled)[int(0.95 * len(filled))],
        max(filled)))
    neg = sum(1 for v in filled if v < 0)
    print("share negative          : %.2f%%" % (100.0 * neg / len(filled)))

    # per-season coverage
    per = defaultdict(lambda: [0, 0])
    for d, _, a, h in out_rows:
        per[d[:4]][0] += 2
        per[d[:4]][1] += (a != "") + (h != "")
    for yr in sorted(per):
        s, f = per[yr]
        print("  %s: %d/%d slots filled (%.1f%%)" % (yr, f, s, 100.0 * f / s))

    # per-month coverage, to show the season-to-date warm-up explicitly
    perm = defaultdict(lambda: [0, 0])
    for d, _, a, h in out_rows:
        perm[d[:7]][0] += 2
        perm[d[:7]][1] += (a != "") + (h != "")
    print("\n[monthly warm-up]")
    for m in sorted(perm):
        s, f = perm[m]
        print("  %s: %5d/%5d (%.1f%%)" % (m, f, s, 100.0 * f / s))

    print("\nwrote %s" % OUT_PATH)


if __name__ == "__main__":
    main()
