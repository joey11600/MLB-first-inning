"""T8.23 -- every FIELDS column must be either WRITTEN or PRESERVED.

`tracker.log_picks` rebuilds each row from a dict LITERAL and then copies a
short `preserve` list back over it. `csv.DictWriter` substitutes "" for any
FIELDS key the literal lacks, so a column that is in neither place is
silently blanked on every predict tick -- roughly twelve times a day.

That is not hypothetical. Thirteen columns were in neither, and the damage
was invisible because one of them repaired itself with the WRONG value:
`_apply_odds_to_row` re-seeds `opened_*_odds` whenever it is blank, so the
wipe made the "opening" price re-seed from the CURRENT scrape every cycle.
1191 of 1277 priced rows (93.3%) ended up with opened == market, which is
why closing-line value has been unmeasurable all season. It reads as a
capture gap and it is not one.

This test is a spelling checker for that mistake. It fails when someone
adds a column to FIELDS without deciding which side it belongs on.
"""
import re
from pathlib import Path

import tracker

SRC = Path(tracker.__file__).read_text(encoding="utf-8")


def _preserve_list() -> set[str]:
    m = re.search(r"preserve = \[(.*?)\n            \]", SRC, re.S)
    assert m, "could not locate the preserve list in tracker.log_picks"
    return set(re.findall(r'"([A-Za-z0-9_]+)"', m.group(1)))


def _keys_written_by_new_row() -> set[str]:
    """Keys appearing as `"name":` inside log_picks -- i.e. in the literal.

    Case matters: an earlier pass at this used [a-z0-9_]+ and so missed
    `away_top3_ops_vs_oppHand`, reporting two model features as damaged
    when they were fine. Both are set at tracker.py:879.
    """
    start = SRC.index("def log_picks")
    end = SRC.index("_write_rows(path, rows)", start)
    return set(re.findall(r'"([A-Za-z0-9_]+)"\s*:', SRC[start:end]))


def test_no_field_is_silently_blanked_on_a_predict_tick():
    written = _keys_written_by_new_row()
    preserved = _preserve_list()
    orphans = [f for f in tracker.FIELDS
               if f not in written and f not in preserved]
    assert not orphans, (
        "these FIELDS columns are neither set by log_picks' new_row literal "
        "nor in its preserve list, so every predict tick writes them as "
        f"empty:\n  " + "\n  ".join(orphans) +
        "\n\nAdd each one to whichever side is correct. If the predictor "
        "computes it, set it in the literal; if another tool owns it (the "
        "v21 shadow model, the odds import, a heal), preserve it."
    )


def test_the_opening_price_columns_are_preserved():
    """Named explicitly, because these three are the ones whose loss was
    invisible: the odds import repairs a blank `opened_*` from the CURRENT
    price, so the bug looked like a market that never moves rather than
    like missing data."""
    preserved = _preserve_list()
    for col in ("opened_nrfi_odds", "opened_yrfi_odds", "opened_captured_at"):
        assert col in preserved, f"{col} must be preserved -- see T8.23"


def test_columns_owned_by_other_tools_are_preserved():
    """The predictor must not erase work it does not own."""
    preserved = _preserve_list()
    for col in ("v21_shadow_nrfi_prob", "v21_shadow_pick_side",
                "v21_shadow_pick_strength",
                "away_top3c_last10_obp", "home_top3c_last10_obp",
                "away_top3c_last10_slg", "home_top3c_last10_slg",
                "away_top3c_last10_iso", "home_top3c_last10_iso"):
        assert col in preserved, f"{col} must be preserved -- see T8.23"


def test_the_grading_and_money_columns_are_still_preserved():
    """Guards against someone 'tidying' the enlarged list and dropping the
    originals with it."""
    preserved = _preserve_list()
    for col in ("graded_result", "market_nrfi_odds", "market_yrfi_odds",
                "bet_placed", "units_risked", "profit_loss_units",
                "odds_captured_at", "edge_on_pick"):
        assert col in preserved
