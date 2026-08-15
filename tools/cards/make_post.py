"""Backfist Bets — the X post that goes beside the card.

    python tools/cards/make_post.py --date 2026-08-14
    python tools/cards/make_post.py --date 2026-08-14 --publish

Writes `backfist_<date>_post.txt` into the same `cards` bucket the images go
to, so `/cards` can show it under that night's cards with a Copy button.

## The split that makes this safe to publish

**The header is DETERMINISTIC. The model only writes the paragraph.**

The first two lines — the play, the units, the side, the price — are built
here in Python from the ledger row, exactly the way the card builds them. A
language model never touches them. That is the same principle as T8.30 (a
refused No.1 that got published as a bet): the money-facing line keys off
what the system actually STAKED, and generated prose is not allowed anywhere
near it.

**The model is given pre-formatted English, not raw numbers to do sums on.**
Every fact below arrives as a finished fragment ("scoreless 1st in 2 of his
last 5 starts"), so composing the paragraph never requires arithmetic. A
model that cannot compute cannot miscompute.

**Every number it writes back is checked against the facts it was given.**
`_unsourced_numbers` pulls every numeric token out of the paragraph and
fails any that did not come from the row. A wrong ERA on a public card is the
fabrication failure this project has rules about, and "we told it not to" is
not a control. On failure it retries once with the offending numbers quoted
back, then falls back to a template with no model in the loop at all.

## Degrading

No `OPENROUTER_API_KEY` -> the deterministic template, silently. Same
contract as the Telegram notifier: the feature is absent, not broken, and
nothing upstream fails because a key is missing.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
REPO = HERE.parent.parent

ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# Sonnet-class by choice, not by accident: this is a 60-word paragraph
# generated a dozen times a day, and the task is composition from supplied
# facts rather than reasoning. Override with OPENROUTER_MODEL. The fallbacks
# exist because OpenRouter's exact model slugs move around and a card should
# not lose its post over a renamed identifier.
DEFAULT_MODEL = "anthropic/claude-sonnet-4.5"
MODEL_FALLBACKS = [
    "anthropic/claude-sonnet-4",
    "anthropic/claude-3.7-sonnet",
    "openai/gpt-4o-mini",
]

MAX_WORDS = 50


def _mc():
    """make_card, imported by path — it owns `load_night`, and the No.1 is
    defined in exactly one place."""
    spec = importlib.util.spec_from_file_location("_mc", HERE / "make_card.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


# ---- facts -------------------------------------------------------------------
def build_facts(n: dict) -> list[str]:
    """Pre-formatted English fragments, every one traceable to a ledger column.

    Returned as finished sentences-worth of fact so the model composes rather
    than calculates. Anything the row does not carry is simply absent — there
    is no placeholder and no estimate.
    """
    # `.get`, not `[...]`: `row` is the raw ledger row and every fact drawn
    # from it is optional by construction. A caller that has the summary but
    # not the row should get the handful of facts that do not need it, not a
    # KeyError.
    row = n.get("row") or {}
    away, home = n["clubs"]["away"], n["clubs"]["home"]
    facts = [
        f"The bet is: at least one run scored in the FIRST INNING ONLY, "
        f"by either team. It is not a bet on either club to win.",
        f"Matchup: {away['club'].title()} at {home['club'].title()}, "
        f"first pitch {n['first_pitch']}.",
        f"Our model puts the chance of a first-inning run at {n['model']:.1f}%.",
        f"The market price implies {n['implied']:.1f}%.",
        f"That is an edge of {n['model'] - n['implied']:+.1f} percentage points.",
    ]

    # `opp` so the top-three fact can name the pitcher they actually face.
    # Without it the model had the two lineups and the two starters but no
    # stated relation between them, and wrote "the Angels counter that
    # weakness" about the Angels' OWN starter — every name and number right,
    # the causal link invented. The fact sheet, not the prompt, is the place
    # to fix that: say who bats against whom and there is nothing to infer.
    for side, c, opp in (("away", away, home), ("home", home, away)):
        who = c["club"].title()
        bits = []
        era, whip = _f(row.get(f"{side}_era")), _f(row.get(f"{side}_whip"))
        k9 = _f(row.get(f"{side}_k9"))
        hand = {"L": "left-handed", "R": "right-handed"}.get(
            (row.get(f"{side}_pitcher_throws_hand") or "").strip().upper(), "")
        if era is not None:
            bits.append(f"a {era:.2f} ERA")
        if whip is not None:
            bits.append(f"a {whip:.2f} WHIP")
        if k9 is not None:
            bits.append(f"{k9:.2f} strikeouts per nine")
        if bits:
            facts.append(f"{who} start {hand} {c['pitcher']}, who has "
                         f"{', '.join(bits)}.".replace("  ", " "))

        # The single most on-the-nose stat for this bet: how often this
        # starter's OWN first inning has been scoreless lately. Converted to a
        # count here so the model never divides anything.
        last5 = _f(row.get(f"{side}_p_last5_pitcher_nrfi"))
        if last5 is not None:
            # BOTH FRAMINGS, because the interesting one is usually the
            # inverse. Given only "scoreless in 2 of his last 5", Sonnet wrote
            # "allowed a first-inning run in three of his last five" — correct,
            # but it got there by subtracting, which is the one thing the fact
            # sheet exists to make unnecessary, and it wrote the result as
            # WORDS where the number guard could not see it. Handing it the
            # subtraction already done removes the reason to do it.
            kept = round(last5 * 5)
            facts.append(f"{c['pitcher']} has kept the first inning scoreless "
                         f"in {kept} of his last 5 starts, and allowed a "
                         f"first-inning run in the other {5 - kept}.")

        if c["bats"]:
            trio = ", ".join(f"{nm} ({obp} on-base)" for nm, obp, _ in c["bats"])
            facts.append(f"{who} top three, who bat in the 1st against "
                         f"{opp['pitcher']}: {trio}.")

    park = _f(row.get("park_factor"))
    if park is not None:
        facts.append(f"Park run factor is {park:.2f} "
                     f"({'above' if park > 1 else 'below'} league average).")
    if (row.get("wx_is_dome") or "").strip() in ("1", "1.0", "True"):
        facts.append("The roof is closed, so weather is not a factor.")
    else:
        temp, wind = _f(row.get("wx_temp_c")), _f(row.get("wx_wind_kmh"))
        if temp is not None:
            facts.append(f"First-pitch temperature about "
                         f"{round(temp * 9 / 5 + 32)} degrees Fahrenheit.")
        if wind is not None:
            facts.append(f"Wind around {round(wind * 0.621)} miles per hour.")
    return facts


# ---- the guard ---------------------------------------------------------------
_NUM = re.compile(r"\d+(?:\.\d+)?")

# SPELLED-OUT COUNTS COUNT AS NUMBERS. The guard originally read digits only,
# and a real generation slipped "three of his last five" past it — a claim
# derived by arithmetic, in words, entirely invisible to a digit scanner. A
# fabricated count is no less fabricated for being spelled.
#
# "one" is deliberately absent: it is a pronoun and an article far more often
# than a count ("this one", "one of the best"), so checking it would reject
# ordinary prose. Both sides of the check use this map, so a phrase that
# appears in the FACTS ("strikeouts per nine") allows its own word.
_WORDS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
          "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_WORD_RE = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.I)


def _numbers_in(text: str) -> list[str]:
    """Every figure in `text`, digits and spelled-out counts alike."""
    return (_NUM.findall(text)
            + [str(_WORDS[w.lower()]) for w in _WORD_RE.findall(text)])


def _allowed_numbers(facts: list[str], n: dict) -> set[str]:
    """Every number the model was actually given, plus "1" for "1st inning".

    THE SEED SET IS ONE ENTRY ON PURPOSE. It first held {1, 3, 5, 9} for "1st
    inning", "top 3", "last 5 starts" and "per nine" — and seeding 3 was
    enough to let "homered in 3 straight games", a complete invention, pass
    the check. The facts spell those as WORDS ("top three", "per nine"), and
    the digits that are genuinely needed ("4 of his last 5") already appear
    in the fact text, so they get allowed by the loop below. Every seeded
    integer is a free pass handed to a fabricated count; hand out as few as
    possible.
    """
    ok = {"1"}
    for f in facts:
        ok.update(_numbers_in(f))
    ok.update(_NUM.findall(f"{n['stake']:.1f} {n['odds']:.0f}"))
    return ok


def _unsourced_numbers(text: str, allowed: set[str]) -> list[str]:
    """Numbers in the paragraph that did not come from the supplied facts.

    DECIMALS ARE MATCHED LENIENTLY, INTEGERS STRICTLY, and the asymmetry is
    deliberate.

    Writing "a 3.7 ERA" for a supplied 3.67 is normal prose, so a decimal
    passes if any supplied value ROUNDS to it at the precision written — a
    rounding is not a fabrication, while 6.12 against a supplied 4.15 still
    fails.

    Integers get no such latitude, because the dangerous invention is a
    COUNT: "scored first in 8 of their last 10 games" is the kind of
    confident, checkable, entirely made-up claim that would have to be
    deleted publicly. Under rounding rules that "8" would sneak through as a
    rounded 7.58 strikeouts-per-nine, so an integer must appear in the facts
    exactly.
    """
    bad = []
    afloat = {float(a) for a in allowed}
    for tok in _numbers_in(text):
        try:
            v = float(tok)
        except ValueError:
            bad.append(tok)
            continue
        dp = len(tok.split(".")[1]) if "." in tok else 0
        ok = (any(round(a, dp) == v for a in afloat) if dp
              else any(a == v for a in afloat))
        if not ok:
            bad.append(tok)
    return bad


# ---- the model ---------------------------------------------------------------
SYSTEM = """You write one short paragraph for a sports-betting post on X for \
a service called Backfist Bets.

HARD RULES.
- Use ONLY the facts supplied. Never state a statistic, name, record, trend \
or streak that is not in them. If you want to say something you were not \
given, say something else instead.
- Do not do arithmetic. Every number you need is already written out.
- Do not compress a fact into something that means something else. "Allowed \
a first-inning run in three of his last five starts" is a count of STARTS; \
"three first-inning runs allowed" is a count of RUNS, and it is a different \
claim. Keep the supplied wording where the meaning is load-bearing.
- The bet is on the FIRST INNING ONLY, and it wins if EITHER team scores. \
Never write it as a bet on one team, and never call it a bet on the game.
- Each club's hitters face the OTHER club's starter. A starter's weakness is \
exploited by the opposing lineup, never by his own. The facts state who bats \
against whom; do not infer it.
- Use full club names. Never use abbreviations like CIN or CWS.
- Do not name the ballpark, stadium or city. You are not told where the game \
is played, and clubs do play neutral-site series. "Anaheim" for an Angels \
home game is a guess that happens to land.
- Do not repeat the price, the units or the word "unit" - they are already \
printed above your paragraph.
- Do not give instructions or advice to the reader. Do not say "take", \
"bet", "lock", "hammer", "smash", or "free money".
- No emojis. No hashtags. No links. No quotation marks around the whole \
thing. Do not start with the word "The".

STYLE. TWO SENTENCES. 25 to 45 words total. Punchy and declarative.

- Open on the single strongest concrete reason this first inning looks live \
- a pitcher's number, or the hitters due up. No wind-up.
- Cut throat-clearing. Not "brings a 5.92 ERA into this one" but "5.92 ERA, \
and he has allowed a first-inning run in three of his last five."
- Every clause carries a fact. At least one hard figure from the list above.
- No scene-setting, no "sets up well", no summarising the bet back at the \
reader, no closing flourish.
- Fragments are fine if they hit harder. Sound like a sharp person with ten \
seconds, not an advert."""


def _trim(text: str, max_words: int = MAX_WORDS) -> str:
    """Drop WHOLE SENTENCES from the end until it fits the budget.

    The old rule sliced at the word limit and bolted a full stop on, which at
    an 80-word budget rarely fired. At 50 it would fire often, and produce
    things like "...sending Meckler, Trout and." Losing a whole sentence is
    survivable; losing half of one is not. The first sentence is always kept,
    however long — a truncated lead is worse than an over-long one.
    """
    if len(text.split()) <= max_words:
        return text
    kept: list[str] = []
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if kept and len(" ".join(kept + [sent]).split()) > max_words:
            break
        kept.append(sent)
    return " ".join(kept)


def _call(model: str, facts: list[str], daypart: str, extra: str = "") -> str:
    import requests

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content":
                f"It is {daypart}. Write the paragraph from these facts:\n\n"
                + "\n".join(f"- {f}" for f in facts) + extra},
        ],
        "temperature": 0.75,
        "max_tokens": 400,
    }
    r = requests.post(ENDPOINT, timeout=90, json=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # OpenRouter attributes usage to these; they are not auth.
        "HTTP-Referer": "https://nrfi-terminal.vercel.app",
        "X-Title": "Backfist Bets",
    })
    if r.status_code == 401:
        raise SystemExit("OpenRouter rejected the key (401). Check "
                         "OPENROUTER_API_KEY.")
    if r.status_code >= 400:
        raise RuntimeError(f"{r.status_code} {r.text[:300]}")
    data = r.json()
    return (data["choices"][0]["message"]["content"] or "").strip()


def analysis(n: dict, verbose: bool = True) -> tuple[str, str]:
    """(paragraph, how) — `how` names the source so callers can report it."""
    # Key check FIRST. Assembling the fact list is pointless without a model
    # to send it to, and doing it up front made the no-key path — the path
    # this runs on until the operator adds a key — depend on data the
    # template does not need.
    if not os.environ.get("OPENROUTER_API_KEY", "").strip():
        if verbose:
            print("  ! OPENROUTER_API_KEY not set — using the plain template",
                  file=sys.stderr)
        return template(n), "template (no API key)"

    facts = build_facts(n)
    daypart = ("evening, so the game is tonight" if "PM" in n["first_pitch"].upper()
               and not n["first_pitch"].startswith(("12", "1:", "2:", "3:", "4:"))
               else "daytime, so do not call the game 'tonight'")

    models = [os.environ.get("OPENROUTER_MODEL", "").strip() or DEFAULT_MODEL]
    models += [m for m in MODEL_FALLBACKS if m not in models]
    allowed = _allowed_numbers(facts, n)

    for model in models:
        extra = ""
        for attempt in (1, 2):
            try:
                out = _call(model, facts, daypart, extra)
            except RuntimeError as exc:
                if verbose:
                    print(f"  ! {model}: {exc}", file=sys.stderr)
                break                                  # try the next model
            out = " ".join(out.split())
            bad = _unsourced_numbers(out, allowed)
            if bad and attempt == 1:
                extra = ("\n\nYour previous attempt used numbers that were not "
                         f"in the facts: {', '.join(bad)}. Rewrite it using "
                         "only the numbers above, or no numbers at all.")
                continue
            if bad:
                if verbose:
                    print(f"  ! {model} kept inventing numbers ({', '.join(bad)})"
                          " — falling back to the template", file=sys.stderr)
                return template(n), "template (failed number check)"
            return _trim(out), model
    return template(n), "template (no model reachable)"


def template(n: dict) -> str:
    """No model in the loop. Dull on purpose, and always true."""
    a, h = n["clubs"]["away"], n["clubs"]["home"]
    edge = n["model"] - n["implied"]
    bats = ""
    if a["bats"] and h["bats"]:
        bats = (f" {a['bats'][0][0]} and {h['bats'][0][0]} lead the two "
                f"lineups off.")
    return (f"{a['pitcher']} and {h['pitcher']} open this one, and our model "
            f"makes a first-inning run {n['model']:.1f}% to happen against a "
            f"price implying {n['implied']:.1f}% — an edge of "
            f"{abs(edge):.1f} points.{bats}")


# ---- assembly ----------------------------------------------------------------
def build_post(n: dict, verbose: bool = True) -> tuple[str, str]:
    """The whole post. Header is ours; only the paragraph is generated."""
    a, h = n["clubs"]["away"], n["clubs"]["home"]
    mc = _mc()
    side = ("Either team scores in the 1st" if n["side"] == "YRFI"
            else "No runs in the 1st inning")
    para, how = analysis(n, verbose)
    stake = f"{n['stake']:.1f}".rstrip("0").rstrip(".")
    header = (f"🚨 NO.1 PLAY — {stake}u\n"
              f"{a['club'].title()} at {h['club'].title()} · {side} · "
              f"{mc.fmt_odds(n['odds'])}")
    return f"{header}\n\n{para}", how


def publish(date_iso: str, text: str) -> str:
    from dotenv import load_dotenv
    from supabase import create_client

    load_dotenv(REPO / ".env")
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_SERVICE_KEY not set — cannot publish")
    sb = create_client(url, key)
    name = f"backfist_{date_iso}_post.txt"
    sb.storage.from_("cards").upload(
        name, text.encode("utf-8"),
        {"content-type": "text/plain; charset=utf-8",
         "upsert": "true", "cache-control": "60"},
    )
    return sb.storage.from_("cards").get_public_url(name)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")

    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--show-facts", action="store_true",
                    help="print what the model is given, and stop")
    a = ap.parse_args()

    night = _mc().load_night(a.date)
    if a.show_facts:
        for f in build_facts(night):
            print(" -", f)
        raise SystemExit(0)

    text, how = build_post(night)
    print(f"source : {how}")
    print("-" * 64)
    print(text)
    print("-" * 64)
    if a.publish:
        print("posted :", publish(a.date, text))
