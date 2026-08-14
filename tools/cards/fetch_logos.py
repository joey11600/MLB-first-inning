"""Vendor the 30 club logos into `tools/cards/logos/` as PNG.

Run once (and again only if a club rebrands):

    python tools/cards/fetch_logos.py

WHY VENDOR RATHER THAN FETCH AT RENDER TIME. The card renderer runs on the
hourly cron, and a CDN hiccup at render time would either kill the run or --
worse -- publish a card with a hole where a logo should be. Fonts, plates and
the brand mark are all already vendored for exactly this reason; logos join
them. 30 files at ~13KB is ~0.4MB, which is nothing next to the 17MB of PNG
plates that had to become JPEG because the cron checks the repo out hourly.

WHY THIS SOURCE. `midfield.mlbstatic.com` is MLB's own asset CDN and is
already the headshot source in `dashboard/components/GameDetails.tsx`, so the
card and the dashboard pull club art from the same place. The `/spots/480`
variant is natively RGBA (the 120/240/360 variants are palette-mode), so the
transparency composites onto a dark plate without a matte ring.

The abbreviation -> MLB id map is PARSED FROM `mlb_first_inning_predictor.py`
rather than copied, for the same reason `team_names()` parses the dashboard's
map: a second hand-maintained copy drifts the first time a franchise moves.
"""
from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent
LOGOS = HERE / "logos"

SIZE = 480
UA = {"User-Agent": "Mozilla/5.0 (nrfi-terminal card renderer)"}


def abbr_to_id() -> dict[str, int]:
    """Invert TEAM_ID_TO_ABBR from the predictor, without importing it.

    Importing the predictor drags in pandas/requests and a network-touching
    module just to read a literal, so this reads the literal.
    """
    src = (REPO / "mlb_first_inning_predictor.py").read_text(encoding="utf-8")
    block = re.search(
        r"TEAM_ID_TO_ABBR:\s*dict\[int,\s*str\]\s*=\s*\{(.*?)\}", src, re.S)
    if not block:
        raise SystemExit("could not find TEAM_ID_TO_ABBR in the predictor")
    pairs = re.findall(r"(\d+):\s*\"([A-Z]+)\"", block.group(1))
    if len(pairs) < 30:
        raise SystemExit(f"expected 30 clubs, parsed {len(pairs)}")
    return {ab: int(i) for i, ab in pairs}


def main() -> int:
    LOGOS.mkdir(parents=True, exist_ok=True)
    teams = abbr_to_id()
    ok = failed = 0
    for abbr, tid in sorted(teams.items()):
        url = f"https://midfield.mlbstatic.com/v1/team/{tid}/spots/{SIZE}"
        dest = LOGOS / f"{abbr}.png"
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(url, headers=UA), timeout=30) as r:
                body = r.read()
            if not body.startswith(b"\x89PNG"):
                raise ValueError("response was not a PNG")
            dest.write_bytes(body)
            print(f"  {abbr:4} id={tid:<4} {len(body):>6}B -> {dest.name}")
            ok += 1
        except Exception as exc:                       # noqa: BLE001
            print(f"  {abbr:4} id={tid:<4} FAILED: {exc}", file=sys.stderr)
            failed += 1
    print(f"\n{ok} saved, {failed} failed -> {LOGOS}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
