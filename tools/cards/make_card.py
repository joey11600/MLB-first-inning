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
import sys
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
}
LIGHT = {
    "ink": (33, 30, 26), "dim": (92, 84, 74),
    "green": (0, 112, 48), "gold": (138, 104, 20), "loss": (154, 27, 18),
    "rule": (33, 30, 26), "tag_ink": (251, 248, 241), "veil": (251, 249, 244),
    "lo": 0.10, "hi": 0.74, "gamma": 1.5,
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


# ---- the card ----------------------------------------------------------------
def render(plate: Path, d: dict, out: Path) -> Path:
    img, T = scrim(plate)
    dr = ImageDraw.Draw(img)
    COL, RIGHT = SIZE - M * 2, SIZE - M

    # header — the logo IS the wordmark, so there is no text mark
    logo_h, logo_y = 158, 50
    logo = Image.open(BRAND).convert("RGBA").resize(
        (round(logo_h * LOGO_ASPECT), logo_h), Image.LANCZOS)
    img.paste(logo, (M, logo_y), logo)

    f_tag = _archivo(23, 800)
    tag = d["tag"].upper()
    tb = dr.textbbox((0, 0), tag, font=f_tag)
    bw = measure(dr, tag, f_tag, 0.14) + 44
    bh = (tb[3] - tb[1]) + 28
    bx, by = RIGHT - bw, logo_y + (logo_h - bh) // 2
    dr.rectangle([bx, by, bx + bw, by + bh], fill=d.get("tag_fill") or T["gold"])
    ink_at(dr, bx + 22, by + 14, tag, f_tag, T["tag_ink"], 0.14)

    dr.line([(M, 240), (RIGHT, 240)], fill=T["rule"], width=3)

    # the bet, in plain words — the hero
    ink_at(dr, M, 272, d["eyebrow"].upper(), _archivo(24, 800), T["gold"], 0.20)
    f_hero, lines = fit_block(dr, d["headline"].upper(), _black, COL, 268, -0.015, 1.02)
    y = 330
    for ln in lines:
        y += ink_at(dr, M, y, ln, f_hero, T["ink"], -0.015) + f_hero.size * 0.20

    # the game
    dr.line([(M, 636), (RIGHT, 636)], fill=T["rule"], width=2)
    f_game = _archivo(46, 800)
    while measure(dr, d["matchup"], f_game) > COL and f_game.size > 26:
        f_game = _archivo(f_game.size - 2, 800)
    ink_at(dr, M, 668, d["matchup"], f_game, T["ink"])
    ink_at(dr, M, 732, d["when"].upper(), _archivo(25, 700), T["dim"], 0.14)

    # the numbers
    dr.line([(M, 800), (RIGHT, 800)], fill=T["rule"], width=2)
    f_k, f_v = _archivo(22, 800), _mono(50, 700)
    cw = COL / max(len(d["stats"]), 1)
    for i, (k, v, kind) in enumerate(d["stats"]):
        x = M + cw * i
        ink_at(dr, x, 832, k.upper(), f_k, T["dim"], 0.14)
        ink_at(dr, x, 872, v, f_v,
               T["green"] if kind == "up" else T["loss"] if kind == "down" else T["ink"])

    ink_at(dr, M, 986, d["footer"].upper(), _archivo(21, 700), T["dim"], 0.12)

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
    # YRFI is EITHER team scoring, not the away team's — say so, because
    # "Yes run" beside a matchup reads as a bet on the first-named side.
    return {
        "tag": "No.1 Play",
        "eyebrow": _daypart(n.get("first_pitch", "")),
        "headline": ("Either team scores in the 1st" if n["side"] == "YRFI"
                     else "No runs in the 1st inning"),
        "matchup": n["matchup"],
        "when": n["when"],
        "stats": [
            ("We make it", f"{n['model']:.1f}%", "up"),
            ("Price needs", f"{n['implied']:.1f}%", "flat"),
            (f"Stake at {fmt_odds(n['odds'])}", f"{n['stake']:.1f}u", "flat"),
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
