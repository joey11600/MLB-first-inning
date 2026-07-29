import csv, glob, os, collections
files = sorted(glob.glob("data/boards/*.csv"))
print("board csv files:", len(files))
bad = []
tot = 0
for f in files:
    try:
        rows = list(csv.DictReader(open(f, encoding="utf-8")))
    except Exception as e:
        continue
    for r in rows:
        n, y = r.get("nrfi_pct"), r.get("yrfi_pct")
        if not n or not y: continue
        tot += 1
        s = round(float(n)+float(y), 4)
        if abs(s-100.0) > 1e-9:
            bad.append((os.path.basename(f), r.get("game_pk"), n, y, s))
print("rows:", tot, " sum!=100:", len(bad), f"({100*len(bad)/max(tot,1):.1f}%)")
print("sums:", dict(collections.Counter(x[4] for x in bad)))
for x in bad[:8]: print("  ", x)
