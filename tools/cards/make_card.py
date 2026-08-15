"""Backfist Bets — square social cards.

The backdrop is generated art and carries NO text. Every character on the card
is drawn here from the ledger, so a published figure cannot drift from the
dashboard's. Which game is the No.1 comes from `tracker._row_is_nights_top_pick`
— the same gate the Telegram and Discord alerts use — so the card cannot crown a
different game than the alerts did.

    python make_card.py --date 2026-08-12
    python make_card.py --date 2026-08-12 --plate leather
"""
from __future__ import annotations

import argparse
import ast
import csv
import json
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

HERE = Path(__file__).parent
FONTS = HERE / "fonts"
PLATES = HERE / "plates"
BRAND = HERE / "brand" / "backfist_trim.png"
LOGO_ASPECT = 0.891

REPO = HERE.parent.parent          # tools/cards/ -> repo root
OUTDIR = Path.home() / "Desktop" / "Backfist Cards"

SIZE = 1080
M = 76


# ---- brand -------------------------------------------------------------------
# Sampled from the logo file: green #007030, gold #E0C060. The green is too dark
# to read on a black plate, so the dark theme lifts it — same hue, more light.
DARK = {
    "ink": (247, 244, 237), "dim": (203, 196, 183),
    "green": (28, 202, 104), "gold": (224, 192, 96), "loss": (240, 112, 96),
    "rule": (255, 255, 255), "tag_ink": (18, 17, 14), "veil": (18, 17, 14),
    "lo": 0.24, "hi": 0.94, "gamma": 1.55,
    # Disc behind a headshot. MLB's portraits are transparent CUTOUTS, so
    # without a plate behind them you get a head floating on the leather.
    "spot": (58, 54, 47, 232),
}
LIGHT = {
    "ink": (33, 30, 26), "dim": (92, 84, 74),
    "green": (0, 112, 48), "gold": (138, 104, 20), "loss": (154, 27, 18),
    "rule": (33, 30, 26), "tag_ink": (251, 248, 241), "veil": (251, 249, 244),
    "lo": 0.10, "hi": 0.74, "gamma": 1.5,
    "spot": (226, 219, 205, 236),
}


# ---- fonts -------------------------------------------------------------------
# Archivo's variation axes are (Weight, Width) IN THAT ORDER. Passing them the
# other way round silently set Weight=100 — the thinnest instance there is — and
# every label on the card rendered hairline. Measured, not guessed: at 60px the
# right order draws 2x the ink.
def _archivo(size: int, weight: int = 700, width: int = 100):
    f = ImageFont.truetype(str(FONTS / "Archivo.ttf"), size)
    f.set_variation_by_axes([weight, width])
    return f


def _mono(size: int, weight: int = 700):
    f = ImageFont.truetype(str(FONTS / "JetBrainsMono.ttf"), size)
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def _black(size: int):
    return ImageFont.truetype(str(FONTS / "ArchivoBlack.ttf"), size)


# ---- text helpers ------------------------------------------------------------
def tracked(dr, xy, text, font, fill, track=0.0):
    """PIL has no letter-spacing, so step glyph by glyph. `track` is in em."""
    step = font.size * track
    x, y = xy
    for c in text:
        dr.text((x, y), c, font=font, fill=fill)
        x += dr.textlength(c, font=font) + step


def measure(dr, text, font, track=0.0):
    w = sum(dr.textlength(c, font=font) for c in text)
    return w + font.size * track * max(len(text) - 1, 0)


def ink_at(dr, x, y_top, text, font, fill, track=0.0):
    """Draw so visible ink starts at y_top regardless of font bearing."""
    b = dr.textbbox((0, 0), text, font=font)
    tracked(dr, (x - b[0], y_top - b[1]), text, font, fill, track)
    return b[3] - b[1]


def wrap(dr, text, font, max_w, track=0.0):
    lines, cur = [], ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if measure(dr, trial, font, track) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def fit_block(dr, text, font_fn, max_w, max_h, track=0.0, lead=1.06, hi=150, lo=28):
    """Largest size at which the wrapped block fits the box."""
    best, best_lines = font_fn(lo), [text]
    while lo <= hi:
        mid = (lo + hi) // 2
        f = font_fn(mid)
        lines = wrap(dr, text, f, max_w, track)
        h = len(lines) * f.size * lead
        if h <= max_h and all(measure(dr, ln, f, track) <= max_w for ln in lines):
            best, best_lines, lo = f, lines, mid + 1
        else:
            hi = mid - 1
    return best, best_lines


# ---- plate -------------------------------------------------------------------
def autocrop(im, tol=14):
    """Trim the uniform frame the generator likes to draw around the art."""
    px = im.convert("RGB")
    w, h = px.size
    ref = px.getpixel((1, 1))

    def close(p):
        return max(abs(a - b) for a, b in zip(p, ref)) <= tol

    def row(y):
        return all(close(px.getpixel((x, y))) for x in range(0, w, max(w // 64, 1)))

    def col(x):
        return all(close(px.getpixel((x, y))) for y in range(0, h, max(h // 64, 1)))

    t = 0
    while t < h // 8 and row(t):
        t += 1
    b = h - 1
    while b > h - h // 8 and row(b):
        b -= 1
    l = 0
    while l < w // 8 and col(l):
        l += 1
    r = w - 1
    while r > w - w // 8 and col(r):
        r -= 1
    if t or l or b != h - 1 or r != w - 1:
        im = im.crop((l + 3, t + 3, r - 3, b - 3))
    return im


def scrim(path):
    img = autocrop(Image.open(path)).convert("RGB").resize((SIZE, SIZE), Image.LANCZOS)
    img = ImageEnhance.Color(img).enhance(1.06)
    img = ImageEnhance.Contrast(img).enhance(1.10)
    from PIL import ImageStat
    mean = sum(ImageStat.Stat(img).mean) / 3
    T = LIGHT if mean > 138 else DARK
    veil = Image.new("L", (1, SIZE))
    for y in range(SIZE):
        t = y / SIZE
        veil.putpixel((0, y), int(255 * min(T["hi"],
                                            T["lo"] + (T["hi"] - T["lo"]) * t ** T["gamma"])))
    return Image.composite(Image.new("RGB", (SIZE, SIZE), T["veil"]),
                           img, veil.resize((SIZE, SIZE))), T


# ---- club art ----------------------------------------------------------------
LOGOS = HERE / "logos"


def club_logo(abbr: str, px: int):
    """A vendored club mark, or None if there is no art for that code.

    Missing art degrades the column to name-only rather than raising: a
    relocation or a brand-new franchise code should cost the card a logo,
    not the whole night's render. Refresh with `fetch_logos.py`.
    """
    p = LOGOS / f"{(abbr or '').upper()}.png"
    if not p.exists():
        return None
    return Image.open(p).convert("RGBA").resize((px, px), Image.LANCZOS)


def _shrink(dr, text, font_fn, max_w, start, floor=16):
    """Largest size at or below `start` at which `text` fits one line."""
    f = font_fn(start)
    while f.size > floor and measure(dr, text, f) > max_w:
        f = font_fn(f.size - 1)
    return f


def _shrink_all(dr, texts, font_fn, max_w, start, floor=12, track=0.0):
    """Largest size at which EVERY string fits `max_w` — one size for the row.

    Sizing the figures as a set rather than individually is the point: a row
    where "MARKET" is bigger than "OUR MODEL" because it happens to be
    shorter reads as a mistake, not as emphasis.
    """
    f = font_fn(start)
    while f.size > floor and any(measure(dr, t, f, track) > max_w for t in texts):
        f = font_fn(f.size - 1)
    return f


# Headshots are FETCHED, not vendored — unlike the 30 club marks. There are
# ~750 active players and the eight faces on a card change nightly, so
# vendoring them is not a fixed cost the way the clubs are.
#
# The cache deliberately lives OUTSIDE the repo. The cron commits `data/`
# every tick, so a cache under data/ would be committed with it and the repo
# would grow a face at a time.  Ephemeral on CI (~8 fetches a run, ~20KB
# each); persistent locally, which is what makes re-rendering a night free.
HEADSHOTS = Path(tempfile.gettempdir()) / "backfist_headshots"
_HS_UA = {"User-Agent": "Mozilla/5.0 (nrfi-terminal card renderer)"}


def headshot(pid, px: int):
    """A player's cutout portrait at `px` square, or None.

    NEVER RAISES. A face that will not load costs the card a portrait and
    nothing else — the caller still draws the disc, so the two club columns
    stay aligned whether or not MLB served the image. That matters more than
    the portrait: an hourly cron must not fail on a CDN hiccup.
    """
    try:
        pid = int(str(pid).strip())
    except (TypeError, ValueError):
        return None
    src = HEADSHOTS / f"{pid}.png"
    if not src.exists():
        try:
            HEADSHOTS.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(
                f"https://midfield.mlbstatic.com/v1/people/{pid}/spots/240",
                headers=_HS_UA)
            with urllib.request.urlopen(req, timeout=15) as r:
                body = r.read()
            if not body.startswith(b"\x89PNG"):
                return None
            src.write_bytes(body)
        except Exception:                              # noqa: BLE001
            return None
    try:
        return Image.open(src).convert("RGBA").resize((px, px), Image.LANCZOS)
    except Exception:                                  # noqa: BLE001
        return None


def portrait(img, dr, x, y, px, pid, T):
    """Disc, then the cutout on top of it, then a hairline ring.

    Drawn in that order because the portraits are transparent cutouts: paste
    one straight onto the plate and you get a head floating on the leather
    with no ground under it.
    """
    disc = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse([0, 0, px - 1, px - 1], fill=T["spot"])
    face = headshot(pid, px)
    if face is not None:
        disc.alpha_composite(face)
    mask = Image.new("L", (px, px), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, px - 1, px - 1], fill=255)
    img.paste(disc, (x, y), mask)
    dr.ellipse([x, y, x + px - 1, y + px - 1], outline=T["rule"], width=1)


# ---- the card ----------------------------------------------------------------
def render(plate: Path, d: dict, out: Path) -> Path:
    """1080x1080, one column per club.

    2026-08-14 REBUILD. The card used to spend its middle third on a
    three-line display headline and nothing else; the operator wanted the
    clubs' marks, both starters and both top-threes on the same card. Those
    do not fit beside a 110px headline, so the headline is now ONE line at
    roughly half the size and the reclaimed band (y 430-800) carries two
    club columns. Everything drawn here still comes from the ledger row --
    only the layout changed, not where a number comes from.
    """
    img, T = scrim(plate)
    dr = ImageDraw.Draw(img)
    COL, RIGHT = SIZE - M * 2, SIZE - M
    GUT = 48
    CW = (COL - GUT) // 2                      # 440 — one column per club
    CX = (M, M + CW + GUT)                     # 76, 564

    # ---- header — the logo IS the wordmark, so there is no text mark
    logo_h, logo_y = 126, 44
    logo = Image.open(BRAND).convert("RGBA").resize(
        (round(logo_h * LOGO_ASPECT), logo_h), Image.LANCZOS)
    img.paste(logo, (M, logo_y), logo)

    f_tag = _archivo(22, 800)
    tag = d["tag"].upper()
    tb = dr.textbbox((0, 0), tag, font=f_tag)
    bw = measure(dr, tag, f_tag, 0.14) + 42
    bh = (tb[3] - tb[1]) + 26
    bx, by = RIGHT - bw, logo_y + (logo_h - bh) // 2
    dr.rectangle([bx, by, bx + bw, by + bh], fill=d.get("tag_fill") or T["gold"])
    ink_at(dr, bx + 21, by + 13, tag, f_tag, T["tag_ink"], 0.14)

    dr.line([(M, 200), (RIGHT, 200)], fill=T["rule"], width=3)

    # ---- the bet, in plain words. The eyebrow now carries the date and
    #      first pitch too, because the old standalone "when" line cost 64px
    #      that the club columns needed and it reads fine as one gold rule.
    ink_at(dr, M, 226, d["eyebrow"].upper(), _archivo(20, 800), T["gold"], 0.16)
    # max_h is ONE line's worth on purpose. fit_block maximises FONT SIZE, not
    # line count, so a 118px box let it prefer 57px-on-two-lines over
    # 50px-on-one — which orphaned "1ST" on a line of its own. Capping the box
    # at a single line makes the one-line option the only tall one available.
    f_hero, lines = fit_block(dr, d["headline"].upper(), _black,
                              COL, 76, -0.015, 1.02, hi=74)
    y = 266
    for ln in lines:
        y += ink_at(dr, M, y, ln, f_hero, T["ink"], -0.015) + f_hero.size * 0.16

    dr.line([(M, 376), (RIGHT, 376)], fill=T["rule"], width=2)

    # ---- the two clubs, a column each: mark, starter, top of the order.
    #      A hairline down the gutter keeps the eye in one club's column;
    #      without it the two pitcher names read as one wide row.
    dr.line([(M + CW + GUT // 2, 398), (M + CW + GUT // 2, 786)],
            fill=T["rule"], width=1)

    for i, side in enumerate(("away", "home")):
        x = CX[i]
        c = d["clubs"][side]

        art = club_logo(c["abbr"], 82)
        nx = x
        if art is not None:
            img.paste(art, (x, 400), art)
            nx = x + 82 + 16
        f_club = _shrink(dr, c["club"], lambda s: _archivo(s, 800),
                         x + CW - nx, 27)
        cb = dr.textbbox((0, 0), c["club"], font=f_club)
        ink_at(dr, nx, 400 + (82 - (cb[3] - cb[1])) // 2, c["club"], f_club, T["ink"])

        # The starter: portrait left, name and line to its right.
        ink_at(dr, x, 508, "Starting pitcher".upper(), _archivo(15, 700),
               T["dim"], 0.16)
        portrait(img, dr, x, 536, 76, c["pitcher_id"], T)
        px_ = x + 76 + 16
        f_p = _shrink(dr, c["pitcher"], lambda s: _archivo(s, 800),
                      x + CW - px_, 27)
        ink_at(dr, px_, 548, c["pitcher"], f_p, T["ink"])
        ink_at(dr, px_, 586, c["pline"], _mono(17, 600), T["dim"])

        ink_at(dr, x, 640, c["bats_head"], _archivo(15, 700), T["dim"], 0.16)
        f_s = _mono(19, 600)
        for n, (nm, stat, bid) in enumerate(c["bats"]):
            by_ = 672 + n * 40
            ink_at(dr, x, by_ + 6, f"{n + 1}", _mono(17, 600), T["dim"])
            portrait(img, dr, x + 22, by_ - 6, 34, bid, T)
            nx_ = x + 22 + 34 + 12
            nf = _shrink(dr, nm, lambda s: _archivo(s, 700),
                         x + CW - nx_ - (measure(dr, stat, f_s) + 14 if stat else 0),
                         23)
            ink_at(dr, nx_, by_, nm, nf, T["ink"])
            if stat:
                ink_at(dr, x + CW - measure(dr, stat, f_s), by_ + 3, stat,
                       f_s, T["dim"])
        if not c["bats"]:
            # Lineups are posted ~94% of the time on a bet row. The other 6%
            # gets the composite the MODEL actually used, labelled as such —
            # never a guessed nine.
            ink_at(dr, x, 678, c["bats_note"], _archivo(19, 700), T["dim"])

    # ---- the numbers.
    #
    # COLUMNS ARE SIZED TO THEIR CONTENT, NOT CUT INTO EQUAL SLICES. Five
    # figures of very different lengths ("+12.3%" against "7.0u") in five
    # equal columns puts a fat gap after the short ones and almost none after
    # the long ones, and the row reads as badly kerned. Measuring each pair
    # and spending what is left over as ONE gutter width makes the rhythm
    # even, and it is what stops the row breaking if a sixth figure is ever
    # added — the type shrinks until the row genuinely fits.
    dr.line([(M, 812), (RIGHT, 812)], fill=T["rule"], width=2)
    stats, n = d["stats"], max(len(d["stats"]), 1)
    MIN_GUT = 24
    f_k = _archivo(20, 800)

    def _cols(fv):
        return [max(measure(dr, k.upper(), f_k, 0.14), measure(dr, v, fv))
                for k, v, _ in stats]

    f_v = _mono(46, 700)
    while f_v.size > 24 and sum(_cols(f_v)) + MIN_GUT * (n - 1) > COL:
        f_v = _mono(f_v.size - 1, 700)
    w = _cols(f_v)
    gut = (COL - sum(w)) / (n - 1) if n > 1 else 0

    x = float(M)
    for (k, v, kind), cwi in zip(stats, w):
        ink_at(dr, x, 842, k.upper(), f_k, T["dim"], 0.14)
        # Only the edge takes a tone colour. It is the one figure that says
        # whether the bet is any good; the sign carries the meaning and the
        # hue only reinforces it, per the palette rule.
        ink_at(dr, x, 882, v, f_v,
               T["green"] if kind == "up" else T["loss"] if kind == "down" else T["ink"])
        x += cwi + gut

    ink_at(dr, M, 976, d["footer"].upper(), _archivo(20, 700), T["dim"], 0.12)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return out


# ---- ledger ------------------------------------------------------------------
def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def team_names() -> dict[str, str]:
    """Club names, PARSED FROM the dashboard's own map rather than copied.

    `dashboard/lib/team-names.ts` already exists for exactly this reason ("TB
    is not a word") and a second hand-maintained copy would drift the first
    time a franchise moves. Club rather than city, because both Chicago teams
    share a city and only the club name tells them apart.
    """
    import re
    src = (REPO / "dashboard" / "lib" / "team-names.ts").read_text(encoding="utf-8")
    out = {}
    for ab, club in re.findall(
            r'(\w+):\s*\{\s*city:\s*"[^"]*",\s*club:\s*"([^"]+)"', src):
        out[ab] = re.sub(r"^the\s+", "", club).upper()
    return out


def _lineup(raw) -> list[dict]:
    """The stored top-three, or [] if it was never posted.

    `*_lineup_json` holds exactly the three hitters the model priced, so this
    is not a nine that needs slicing.

    THREE ENCODINGS, because the two ledger sources disagree. The CSV stores
    real JSON; Supabase hands back the PYTHON REPR of the same list —
    `[{'ab': 395, ...}]`, single-quoted — which `json.loads` rejects. That
    rejection was silent: every card rendered off Supabase claimed "lineup
    not posted" while the hitters sat right there in the column. `ast` reads
    the repr form and, being literal-only, cannot execute anything. A list
    that arrives already deserialised is passed straight through.

    Anything still unparseable degrades to "no lineup" for the same reason a
    missing price stays missing — the card would rather say nothing than say
    something invented.
    """
    if isinstance(raw, list):
        return [b for b in raw if isinstance(b, dict)]
    s = (raw or "").strip()
    if not s:
        return []
    for parse in (json.loads, ast.literal_eval):
        try:
            v = parse(s)
        except Exception:
            continue
        if isinstance(v, list):
            return [b for b in v if isinstance(b, dict)]
    return []


def _three(v) -> str:
    """.371, the way a baseball card writes a rate — no leading zero."""
    return f"{v:.3f}".lstrip("0") if isinstance(v, (int, float)) else ""


def _club_block(row: dict, pre: str, abbr: str, club: str) -> dict:
    """Everything the card prints for one club, straight off the ledger row.

    Nothing here is fetched or recomputed: the starter, his line and the top
    three are columns the predictor already wrote when it priced the game, so
    a card can never disagree with the row that produced the bet.
    """
    hand = (row.get(f"{pre}_pitcher_throws_hand") or "").strip().upper()
    era, whip = _f(row.get(f"{pre}_era")), _f(row.get(f"{pre}_whip"))
    bits = ([f"{hand}HP"] if hand in ("L", "R") else [])
    if era is not None:
        bits.append(f"{era:.2f} ERA")
    if whip is not None:
        bits.append(f"{whip:.2f} WHIP")

    bats = [(nm, _three(b.get("obp")), b.get("id"))
            for b in _lineup(row.get(f"{pre}_lineup_json"))
            if (nm := (b.get("name") or "").strip())]

    note = ""
    if not bats:
        c_obp = _f(row.get(f"{pre}_top3c_obp"))
        note = ("Lineup not posted · " + _three(c_obp) + " OBP"
                if c_obp is not None else "Lineup not posted")

    return {
        "abbr": abbr,
        "club": club,
        "pitcher": (row.get(f"{pre}_pitcher") or "").strip() or "TBD",
        "pitcher_id": row.get(f"{pre}_pitcher_id"),
        "pline": " · ".join(bits),
        "bats_head": "Top of the order".upper(),
        "bats": bats[:3],
        "bats_note": note,
    }


# Columns the Supabase mirror does not carry, but the CSV does. The mirror has
# 106 columns to the CSV's 117 and the starter's throwing hand is one of the
# eleven missing, so a card rendered from Supabase — which is the DEFAULT
# source — printed "3.67 ERA" where it meant "LHP · 3.67 ERA".
#
# Deliberately an allowlist of DISPLAY-ONLY columns, and read-only. Nothing
# named here can reach a price, a stake, a probability or a graded result, so
# the worst a stale CSV can do is cost the card a two-letter label. Widening
# this tuple to anything the money path reads would break that guarantee.
_CSV_ONLY = ("away_pitcher_throws_hand", "home_pitcher_throws_hand")


def _backfill_display_cols(row: dict, date_iso: str) -> None:
    """Fill display-only columns the Supabase row is missing, from the CSV."""
    if all(row.get(c) for c in _CSV_ONLY):
        return
    path = REPO / "data" / f"picks_{date_iso[:4]}.csv"
    if not path.exists():
        return

    def same_game(r: dict) -> bool:
        pk, rpk = str(row.get("game_pk") or "").strip(), str(r.get("game_pk") or "").strip()
        if pk and rpk:
            return pk == rpk                      # unique, survives doubleheaders
        return ((r.get("date") or "").strip() == date_iso
                and (r.get("away_team") or "").strip() == (row.get("away_team") or "").strip()
                and (r.get("home_team") or "").strip() == (row.get("home_team") or "").strip())

    try:
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if not same_game(r):
                    continue
                for c in _CSV_ONLY:
                    if not row.get(c) and (r.get(c) or "").strip():
                        row[c] = r[c].strip()
                return
    except Exception as exc:                       # noqa: BLE001
        print(f"  ! display backfill skipped: {exc}", file=sys.stderr)


def implied(odds: float) -> float:
    return (-odds) / (-odds + 100) if odds < 0 else 100 / (odds + 100)


def fmt_odds(o: float) -> str:
    return f"+{o:.0f}" if o > 0 else f"\u2212{abs(o):.0f}"


def load_night(date_iso: str) -> dict:
    """The night's No.1, chosen by the SAME gate the alerts use."""
    sys.path.insert(0, str(REPO))
    import importlib.util
    spec = importlib.util.spec_from_file_location("_plc", REPO / "tools" / "pl_calc.py")
    plc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plc)
    import tracker

    rows, source = plc._load_rows(int(date_iso[:4]))
    night = [r for r in rows if (r.get("date") or "").strip() == date_iso]
    if not night:
        raise SystemExit(f"no rows for {date_iso}")

    # RANK EXPLICITLY, THEN CONFIRM WITH THE GATE — not the other way round.
    # `_row_is_nights_top_pick` FAILS OPEN by design (CLAUDE.md: a broken gate
    # must spam rather than silence), so it returns True for any row whose side
    # is neither NRFI nor YRFI. Scanning the slate with it as the finder picked
    # a PASS row with a 0.0u stake. The ranking below mirrors
    # pl_calc.select_top_picks: YRFI only, bet placed, priced, strongest first.
    cand = []
    for r in night:
        if (r.get("pick_side") or "").strip().upper() != "YRFI":
            continue
        if (r.get("bet_placed") or "").strip().upper() != "Y":
            continue
        p, o = _f(r.get("nrfi_prob")), _f(r.get("market_yrfi_odds"))
        if p is None or o is None or o == 0:
            continue          # unpriced stays unpriced — never guessed
        cand.append((p, implied(o),
                     f"{(r.get('away_team') or '')}@{(r.get('home_team') or '')}", r))
    if not cand:
        raise SystemExit(f"{date_iso}: no priced STRONG YRFI play on this slate")
    cand.sort(key=lambda t: t[:3])
    top = cand[0][3]
    _backfill_display_cols(top, date_iso)

    if not tracker._row_is_nights_top_pick(top, [x for x in night if x is not top]):
        print(f"  ! warning: {date_iso} ranking and the alert gate disagree",
              file=sys.stderr)

    side = (top.get("pick_side") or "").strip().upper()
    p_nrfi = _f(top.get("nrfi_prob"))
    odds = _f(top.get("market_yrfi_odds" if side == "YRFI" else "market_nrfi_odds"))
    if p_nrfi is None or odds is None:
        raise SystemExit(f"{date_iso}: the No.1 has no captured price — nothing fabricated")

    day = datetime.strptime(date_iso, "%Y-%m-%d")
    names = team_names()
    away = (top.get("away_team") or "").strip().upper()
    home = (top.get("home_team") or "").strip().upper()
    t = (top.get("game_time_et") or "").strip()
    return {
        "source": source, "side": side, "date": date_iso, "first_pitch": t,
        "matchup": f"{names.get(away, away)} at {names.get(home, home)}",
        "when": f"{day.strftime('%a %b')} {day.day} · {t}",
        "model": ((1 - p_nrfi) if side == "YRFI" else p_nrfi) * 100,
        "odds": odds, "implied": implied(odds) * 100,
        "stake": _f(top.get("units_risked")) or 0.0,
        "result": (top.get("graded_result") or "").strip().upper(),
        "clubs": {
            "away": _club_block(top, "away", away, names.get(away, away)),
            "home": _club_block(top, "home", home, names.get(home, home)),
        },
        # The winning row itself, so a consumer that needs a column this dict
        # does not name (make_post.py wants park factor, weather and the
        # pitchers' recent first-inning record) can read it WITHOUT
        # re-implementing the ranking. There is one definition of "the No.1"
        # and it lives above; nothing downstream gets to have its own.
        "row": top,
    }


def _daypart(first_pitch: str) -> str:
    """"Tonight's" over a 3:40 PM first pitch is simply wrong, and it is the
    kind of wrong a reader notices before they notice anything else."""
    try:
        hh, rest = first_pitch.split(":", 1)
        h = int(hh) % 12 + (12 if "PM" in rest.upper() else 0)
        return "Tonight's top play" if h >= 17 else "Today's top play"
    except Exception:
        return "Today's top play"


def top_pick_card(n: dict) -> dict:
    edge = n["model"] - n["implied"]
    # YRFI is EITHER team scoring, not the away team's — say so, because
    # "Yes run" beside a matchup reads as a bet on the first-named side.
    return {
        "tag": "No.1 Play",
        # The date and first pitch ride the eyebrow now. They used to have
        # their own 64px line under the matchup, and the club columns needed
        # that band; as one gold rule it still answers "when is this?".
        "eyebrow": f"{_daypart(n.get('first_pitch', ''))} · {n['when']}",
        "headline": ("Either team scores in the 1st" if n["side"] == "YRFI"
                     else "No runs in the 1st inning"),
        "matchup": n["matchup"],
        "when": n["when"],
        "clubs": n["clubs"],
        # THE EDGE IS DERIVED HERE, NEVER READ FROM `edge_on_pick`.
        #
        # The dashboard's `deriveEdge` does exactly this and says why: the
        # stored column is written by a different process than the one that
        # writes `nrfi_prob`, so the two drift. 41 rows disagree with a
        # correct recomputation (mean 1.66pp, worst 7.75pp) and 2026-06-17
        # PIT@OAK has the SIGN BACKWARDS — stored +4.8% on a bet whose real
        # edge at -150 is -0.6%. Publishing that on a card is worse than
        # publishing it on a board.
        #
        # Deriving from the two figures printed beside it also means the card
        # cannot contradict the board: same inputs, same formula, same
        # number. (Model 66.9 and market 54.5 print a 12.3 edge, not 12.4 —
        # the edge is computed at full precision, then rounded once. Matching
        # the ledger matters more than surviving mental arithmetic.)
        "stats": [
            ("Market", f"{n['implied']:.1f}%", "flat"),
            ("Our model", f"{n['model']:.1f}%", "flat"),
            # Real minus (U+2212), not a hyphen — `fmt_odds` already uses one
            # and the two sit side by side in the same mono row, where a
            # stubby hyphen next to a full-width minus is visible.
            ("Edge", f"{'+' if edge >= 0 else '−'}{abs(edge):.1f}%",
             "up" if edge >= 0 else "down"),
            ("Stake", f"{n['stake']:.1f}u", "flat"),
            ("Odds", fmt_odds(n["odds"]), "flat"),
        ],
        "footer": "1 unit = 1% of your bankroll",
    }


# ---- publishing --------------------------------------------------------------
def publish(png: Path) -> str:
    """Upload to the `cards` Supabase bucket and return its public URL.

    Same credential path as every other Python-side write in this project:
    SUPABASE_SERVICE_KEY, which bypasses RLS. The dashboard reads the bucket
    with the anon key and can only SELECT. Upsert on purpose — re-rendering a
    date must replace that date's card, not accumulate copies.
    """
    from dotenv import load_dotenv
    from supabase import create_client
    import os

    load_dotenv(REPO / ".env")
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — cannot publish")

    sb = create_client(url, key)
    sb.storage.from_("cards").upload(
        png.name, png.read_bytes(),
        {"content-type": "image/png", "upsert": "true", "cache-control": "300"},
    )
    return sb.storage.from_("cards").get_public_url(png.name)


if __name__ == "__main__":
    # the Windows console is cp1252 and the odds carry a real minus sign
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    names = [p.stem.split("-", 1)[1] for p in sorted(PLATES.glob("*.[jp][pn]g"))]
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--plate", default="leather", choices=names + ["all"])
    ap.add_argument("--publish", action="store_true",
                    help="also upload to Supabase so the dashboard and your phone can see it")
    a = ap.parse_args()

    night = load_night(a.date)
    card = top_pick_card(night)
    picked = names if a.plate == "all" else [a.plate]

    print(f"source : {night['source']}")
    print(f"No.1   : {night['matchup']}  {night['side']}  "
          f"{fmt_odds(night['odds'])}  {night['stake']:.1f}u  "
          f"model {night['model']:.1f}%  needs {night['implied']:.1f}%")
    for name in picked:
        plate = next(p for p in PLATES.glob("*.[jp][pn]g") if p.stem.endswith(name))
        png = render(plate, card, OUTDIR / f"backfist_{a.date}_{name}.png")
        print("saved  :", png)
        if a.publish:
            print("posted :", publish(png))
