import csv, math, collections

PATH = "data/picks_2026.csv"

def js_round(x):
    # JS Math.round: round half toward +Infinity
    return math.floor(x + 0.5)

def js_pct(p):
    # Math.round(p*1000)/10
    return js_round(p*1000)/10.0

def py_fmt1(p):
    # predictor: f"{p*100:.1f}"
    return float(f"{p*100:.1f}")

rows = list(csv.DictReader(open(PATH, encoding="utf-8")))
print("total rows:", len(rows))

sup_bad = []
csv_bad = []
sum_not_one = []
n = 0
for r in rows:
    ns, ys = r.get("nrfi_prob",""), r.get("yrfi_prob","")
    if not ns or not ys:
        continue
    n += 1
    pn, py = float(ns), float(ys)
    if abs(pn+py-1.0) > 1e-9:
        sum_not_one.append((r.get("date"), r.get("game_pk"), ns, ys, pn+py))
    a, b = js_pct(pn), js_pct(py)
    if abs(a+b-100.0) > 1e-9:
        sup_bad.append((r.get("date"), r.get("game_pk"), ns, ys, a, b, round(a+b,4)))
    c, d = py_fmt1(pn), py_fmt1(py)
    if abs(c+d-100.0) > 1e-9:
        csv_bad.append((r.get("date"), r.get("game_pk"), ns, ys, c, d, round(c+d,4)))

print("rows with both probs:", n)
print("stored pn+py != 1 exactly:", len(sum_not_one))
for x in sum_not_one[:5]: print("   ", x)
print()
print("SUPABASE path (JS Math.round) sum!=100:", len(sup_bad), f"({100*len(sup_bad)/n:.1f}%)")
cnt = collections.Counter(x[6] for x in sup_bad)
print("   sums seen:", dict(cnt))
for x in sup_bad[:6]: print("   ", x)
print()
print("PREDICTOR/CSV path (py .1f) sum!=100:", len(csv_bad), f"({100*len(csv_bad)/n:.1f}%)")
cnt2 = collections.Counter(x[6] for x in csv_bad)
print("   sums seen:", dict(cnt2))
for x in csv_bad[:6]: print("   ", x)
