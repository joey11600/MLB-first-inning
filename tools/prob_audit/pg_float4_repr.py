import struct, csv, math, collections
def f32(x): return struct.unpack('f', struct.pack('f', x))[0]
def shortest_f4(v):
    # emulate PG12+ float4 output: shortest decimal that round-trips through float4
    for prec in range(1, 10):
        s = f"{v:.{prec}g}"
        if f32(float(s)) == v: return s
    return repr(v)
def js_round(x): return math.floor(x+0.5)
def js_pct(p): return js_round(p*1000)/10.0

rows = list(csv.DictReader(open("data/picks_2026.csv", encoding="utf-8")))
changed = 0; n=0; bad=0; sums=collections.Counter(); ex=[]
for r in rows:
    ns, ys = r.get("nrfi_prob",""), r.get("yrfi_prob","")
    if not ns or not ys: continue
    n+=1
    pn, py = float(ns), float(ys)
    sn, sy = shortest_f4(f32(pn)), shortest_f4(f32(py))
    jn, jy = float(sn), float(sy)   # what JS JSON.parse yields
    if jn != pn or jy != py: changed += 1
    a,b = js_pct(jn), js_pct(jy)
    s = round(a+b,4)
    if abs(s-100.0)>1e-9:
        bad+=1; sums[s]+=1
        if len(ex)<5: ex.append((r.get("date"), r.get("game_pk"), sn, sy, a, b, s))
print("rows:", n)
print("rows where PG float4 round-trip changes the JS-parsed value:", changed)
print("sum!=100 on the true Supabase wire path:", bad, f"({100*bad/n:.1f}%)", dict(sums))
for e in ex: print("  ", e)
print()
print("example wire strings:", shortest_f4(f32(0.5925)), shortest_f4(f32(0.4075)))
