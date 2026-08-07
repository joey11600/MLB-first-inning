#!/usr/bin/env python3
"""
parity_fixtures.py -- the ONE generator for the dashboard parity fixtures,
and the check that stops them silently ageing.

THE HOLE THIS CLOSES.
`dashboard/scripts/check-kelly-parity.mjs` compiles the real TypeScript and
compares it to a fixture -- but the fixture is a STATIC file, and Python is
never invoked. So:

    change tracker.kelly_stake_units
      -> the fixture does not move   (it is a committed file)
      -> the TypeScript does not move
      -> the guard compares them to each other and prints "ok"

...while the number that stakes the real bet has walked away from the number
published to subscribers. The guard proves the dashboard still matches THE
FIXTURE, not today's tracker. That is precisely the incident it was built for
(2026-08-06: Discord "4 units", board "STAKE 3.00u"), one level up.

WHY IT IS NOT SIMPLY REGENERATED INSIDE THE VERCEL BUILD.
Vercel's Node builder is not guaranteed to have a Python with this repo's
dependencies importable, and a guard that cannot run is a guard that gets
deleted. So the loop is closed across two places, each doing the half it can:

    Vercel prebuild  check-kelly-parity.mjs   TypeScript  vs  fixture
    GitHub Actions   parity_fixtures.py --check  fixture  vs  Python

Neither alone is sufficient; together they pin TypeScript to Python. If you
ever move the Vercel build to an image with Python, this file's --check can
move into prebuild and the split disappears.

USAGE
    python tools/parity_fixtures.py --check      # CI: fail if drifted
    python tools/parity_fixtures.py --write      # after a DELIBERATE rule change

A --check failure is almost never fixed by --write. It means someone changed a
money rule in Python; the correct response is to decide whether that change was
intended, and only then regenerate and re-verify the dashboard against it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

KELLY_FIXTURE = ROOT / "dashboard" / "scripts" / "kelly-parity-fixture.json"
PASS_FIXTURE = ROOT / "dashboard" / "scripts" / "pass-price-fixture.json"

# The price grid. Kept here rather than in the .mjs so there is exactly one
# definition of "which cases matter".
_PRICES = [a for a in range(-400, -99, 5)] + [a for a in range(100, 401, 5)]


def _kelly_cases() -> list[dict]:
    """Boundary-dense, because that is where the two implementations parted.

    The first sweep keeps only (p, price) pairs whose RAW quarter-Kelly sits
    within a hair of a whole-unit rounding edge -- the exact band where
    round-once vs round-twice disagree -- plus everything under 0.6u, which is
    where KELLY_ROUNDED_FLOOR decides between a 0.5u bet and no bet at all.
    The second sweep adds a broad regular sample so the fixture is not made
    only of edges.
    """
    import tracker

    cases: list[dict] = []
    seen: set[tuple[float, int]] = set()

    for a in _PRICES:
        p = 0.20
        while p <= 0.85 + 1e-12:
            f = tracker.kelly_fraction_of_bankroll(p, str(a) if a < 0 else f"+{a}")
            if f:
                raw = min(f * tracker.KELLY_FRACTION, tracker.KELLY_MAX_STAKE_FRAC) * 100
                frac = abs(raw - round(raw))
                if abs(frac - 0.5) < 0.01 or raw < 0.6:
                    key = (round(p, 4), a)
                    if key not in seen:
                        seen.add(key)
                        v = tracker.kelly_stake_units(p, str(a) if a < 0 else f"+{a}")
                        cases.append({"p": round(p, 4), "american": a,
                                      "expected": 0.0 if v is None else v})
            p = round(p + 0.0001, 4)

    for a in _PRICES:
        for i in range(20):
            p = round(0.25 + i * 0.03, 4)
            v = tracker.kelly_stake_units(p, str(a) if a < 0 else f"+{a}")
            cases.append({"p": p, "american": a,
                          "expected": 0.0 if v is None else v})
    return cases


def _pass_cases() -> list[dict]:
    """The published "don't take worse than" limit, across the realistic band."""
    import discord_broadcasts as B

    out: list[dict] = []
    p = 0.50
    while p <= 0.80 + 1e-12:
        v = B.pass_price(round(p, 4))
        if v is not None:
            out.append({"p": round(p, 4), "passAt": v})
        p = round(p + 0.0025, 4)
    return out


_SPECS = (
    (KELLY_FIXTURE, _kelly_cases,
     "generated from tracker.kelly_stake_units by tools/parity_fixtures.py"),
    (PASS_FIXTURE, _pass_cases,
     "generated from discord_broadcasts.pass_price by tools/parity_fixtures.py"),
)


def _write() -> int:
    for path, build, note in _SPECS:
        cases = build()
        path.write_text(json.dumps({"note": note, "cases": cases}, indent=0) + "\n",
                        encoding="utf-8")
        print(f"  wrote {path.name}: {len(cases)} cases")
    return 0


def _check() -> int:
    bad = 0
    for path, build, _note in _SPECS:
        if not path.exists():
            print(f"[parity-fixtures] MISSING {path.name}", file=sys.stderr)
            bad += 1
            continue
        committed = json.loads(path.read_text(encoding="utf-8"))["cases"]
        fresh = build()

        # Compare VALUES, not bytes: formatting churn is not drift.
        ckey = {(c["p"], c.get("american", "passAt")): c.get("expected", c.get("passAt"))
                for c in committed}
        fkey = {(c["p"], c.get("american", "passAt")): c.get("expected", c.get("passAt"))
                for c in fresh}

        if ckey == fkey:
            print(f"  {path.name}: ok ({len(fresh)} cases match Python)")
            continue

        bad += 1
        only_c = set(ckey) - set(fkey)
        only_f = set(fkey) - set(ckey)
        diff = [(k, ckey[k], fkey[k]) for k in (set(ckey) & set(fkey))
                if ckey[k] != fkey[k]]
        print(f"\n[parity-fixtures] DRIFT in {path.name}", file=sys.stderr)
        print(f"  cases only in the committed fixture : {len(only_c)}", file=sys.stderr)
        print(f"  cases only in freshly-generated     : {len(only_f)}", file=sys.stderr)
        print(f"  cases whose VALUE changed           : {len(diff)}", file=sys.stderr)
        for k, was, now in diff[:10]:
            print(f"     p={k[0]} price={k[1]}   fixture={was}   python now={now}",
                  file=sys.stderr)

    if bad:
        print(
            "\n  A money rule in Python no longer matches the fixture the\n"
            "  dashboard is checked against. The dashboard is therefore still\n"
            "  publishing the OLD number while the ledger stakes the new one --\n"
            "  which is the 3u-vs-4u incident of 2026-08-06.\n\n"
            "  Do NOT reflexively run --write. Decide first whether the Python\n"
            "  change was intended. If it was, regenerate AND re-verify the\n"
            "  dashboard against it (npm run build runs check-kelly-parity).\n",
            file=sys.stderr)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true",
                   help="fail if the committed fixtures no longer match Python")
    g.add_argument("--write", action="store_true",
                   help="regenerate (only after a DELIBERATE rule change)")
    a = ap.parse_args()
    return _check() if a.check else _write()


if __name__ == "__main__":
    raise SystemExit(main())
