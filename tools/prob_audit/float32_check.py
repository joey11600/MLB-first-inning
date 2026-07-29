import struct, math, csv, collections
def f32(x): return struct.unpack('f', struct.pack('f', x))[0]
def js_round(x): return math.floor(x+0.5)
def js_pct(p): return js_round(p*1000)/10.0

# does float4 storage round-trip to the same decimal literal?
rows = list(csv.DictReader(open("data/picks_2026.csv", encoding="utf-8")))
mismatch = 0
bad32 = 0; n=0
sums = collections.Counter()
for r in rows:
    ns, ys = r.get("nrfi_prob",""), r.get("yrfi_prob","")
    if not ns or not ys: continue
    n+=1
    pn, py = float(ns), float(ys)
    # postgres float4 shortest round-trip output == repr of the f32 value
    rn, ry = f32(pn), f32(py)
    if repr(float(f"{rn:.9g}")) != repr(pn): mismatch += 1
    a,b = js_pct(rn), js_pct(ry)
    s = round(a+b,4)
    if abs(s-100.0)>1e-9:
        bad32 += 1; sums[s]+=1
print("rows:", n)
print("float32 storage changes the shortest decimal literal on:", mismatch, "rows")
print("sum!=100 using raw float32 bits:", bad32, dict(sums))
print()
print("sanity on the claimed example:")
for p in (0.5925, 0.4075):
    print(f"  p={p!r} f32={f32(p)!r}  p*1000={p*1000!r}  js_pct={js_pct(p)}  js_pct(f32)={js_pct(f32(p))}")
