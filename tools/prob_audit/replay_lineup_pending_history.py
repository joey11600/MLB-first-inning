"""Definitive reachability test for the classifyTentative rounding claim.

classifyTentative only runs when pickStrength == 'LINEUP PENDING'.  The
final-state picks_2026.csv keeps only 40 such rows, because the strength
is overwritten once MLB posts the lineup.  But every cron tick commits
picks_2026.csv, so git history preserves the LINEUP PENDING states WITH
full-precision nrfi_prob.

This walks every historical blob of data/picks_2026.csv, collects every
(date, game_pk) that was ever LINEUP PENDING together with the
nrfi_prob / lambda_lr_total it carried AT THAT MOMENT, and asks whether
the 1-decimal rounding the dashboard applies would have flipped the
tentative verdict actually shown on screen.

ANALYSIS ONLY -- reads git objects, writes nothing.
"""
import csv, io, math, os, subprocess, sys, collections

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(ROOT)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from verify_rounding_claim import classify_tentative, dash_p, TH  # noqa

PATH = "data/picks_2026.csv"


def run(args, **kw):
    return subprocess.run(args, capture_output=True, cwd=ROOT, **kw)


# --- collect the distinct blob ids this file ever had --------------------
log = run(["git", "log", "--format=%H", "--follow", "--", PATH]).stdout.decode().split()
print(f"commits touching {PATH}: {len(log)}")

blobs = []
seen = set()
CH = 200
for i in range(0, len(log), CH):
    chunk = log[i:i + CH]
    spec = "\n".join(f"{c}:{PATH}" for c in chunk).encode()
    out = run(["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
              input=spec).stdout.decode()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "blob" and parts[0] not in seen:
            seen.add(parts[0])
            blobs.append(parts[0])
print(f"distinct blob revisions: {len(blobs)}")


def f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# key -> set of (p_full, lambda) states observed while LINEUP PENDING
states = collections.defaultdict(set)
meta = {}

BS = 100
for i in range(0, len(blobs), BS):
    chunk = blobs[i:i + BS]
    spec = "\n".join(chunk).encode()
    proc = subprocess.run(["git", "cat-file", "--batch"], input=spec,
                          capture_output=True, cwd=ROOT)
    buf = proc.stdout
    pos = 0
    for _ in chunk:
        nl = buf.index(b"\n", pos)
        hdr = buf[pos:nl].decode().split()
        size = int(hdr[2])
        body = buf[nl + 1:nl + 1 + size]
        pos = nl + 1 + size + 1
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            continue
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("pick_strength") != "LINEUP PENDING":
                continue
            p = f(r.get("nrfi_prob"))
            if p is None:
                continue
            lam = f(r.get("lambda_lr_total"))
            key = (r.get("date"), r.get("game_pk") or f"{r.get('away_team')}@{r.get('home_team')}")
            states[key].add((p, lam))
            meta[key] = (r.get("date"), r.get("away_team"), r.get("home_team"))
    if (i // BS) % 5 == 0:
        print(f"  ...{i + len(chunk)}/{len(blobs)} blobs, {len(states)} pending games so far",
              flush=True)

total_states = sum(len(v) for v in states.values())
print(f"\ndistinct games ever LINEUP PENDING: {len(states)}")
print(f"distinct (game, probability) pending states replayed: {total_states}")

flips = []
near = 0
for key, sset in states.items():
    for p, lam in sset:
        a = classify_tentative(p, lam)
        b = classify_tentative(dash_p(p), lam)
        # how close to any boundary
        for bnd in (TH["strongYrfiP"], TH["passLoP"], TH["leanNrfiP"]):
            if 0 <= bnd - p <= 0.0005:
                near += 1
                break
        if a != b:
            flips.append((meta[key], p, lam, a, b))

print(f"pending states sitting within 0.0005 BELOW a boundary: {near}")
print(f"\nDISPLAYED-VERDICT FLIPS caused by the 1-decimal rounding: {len(flips)}")
for m, p, lam, a, b in sorted(flips):
    print(f"  {m[0]} {m[1]:>3}@{m[2]:<3} p={p:.6f} lam={lam} "
          f"full={a[0]} {a[1]}   shown={b[0]} {b[1]}")
