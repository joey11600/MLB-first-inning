"""The SHADOW model (2026-09-04) must never touch the live pick.

Three things are pinned here, in order of how much money they protect:

  1. The live classifier is byte-identical with and without the new `max_p`
     parameter, and `_shadow_score` never mutates the live model objects.
  2. Every failure mode of the candidate directory (absent, malformed,
     kill-switched) leaves the six shadow fields blank and raises nothing.
  3. fi_form.py -- the one input the shadow has that the live model does not --
     reproduces the research column that was validated in tools/refit2026,
     cannot see its own game, and hands an unknown pitcher the league mean.

Plus the plumbing check the 2026-08-22 columns taught us: every new ledger
column must be appended behind sizing_prob and mapped in both Supabase paths.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fi_form                                 # noqa: E402
import mlb_first_inning_predictor as P         # noqa: E402
import tracker                                 # noqa: E402

SHADOW_COLS = ["shadow_model", "shadow_nrfi_prob", "shadow_nrfi_prob_raw",
               "shadow_pick_label", "home_fi_form", "away_fi_form"]


def _reset_shadow(monkeypatch, where: Path | None = None) -> None:
    monkeypatch.setattr(P, "_shadow_loaded", False)
    monkeypatch.setattr(P, "_shadow", None)
    if where is not None:
        monkeypatch.setattr(P, "_SHADOW_DIR", where)


def _neutral_vectors() -> tuple[list[float], list[float]]:
    """A game at the live model's training means -- every input exactly average."""
    m_t1, m_b1 = P._load_lr_models()
    assert m_t1 is not None and m_b1 is not None, "live model artifacts missing"
    return list(m_t1["mean"]), list(m_b1["mean"])


# ---------------------------------------------------------------------------
# 1. The live pick is untouched
# ---------------------------------------------------------------------------

def test_live_classifier_is_identical_with_and_without_max_p():
    for p in (0.30, 0.35, 0.40, 0.4129, 0.413, 0.4131, 0.42, 0.44, 0.45, 0.50, 0.55, 0.62, 0.70):
        for lam in (0.60, 0.75, 0.77, 0.90):
            for dome in (False, True):
                live = P.classify_pick_lr(p, 20, lam, wx_temp_c=30.0, wx_wind_kmh=12.0, wx_is_dome=dome)
                assert live == P.classify_pick_lr(p, 20, lam, wx_temp_c=30.0, wx_wind_kmh=12.0,
                                                  wx_is_dome=dome, max_p=None)


def test_max_p_moves_only_the_strong_lean_boundary_on_the_yrfi_side():
    # just under the live ceiling: STRONG live, LEAN under a tighter shadow ceiling
    assert P.classify_pick_lr(0.4125, 20, 0.90) == ("YRFI", "STRONG")
    assert P.classify_pick_lr(0.4125, 20, 0.90, max_p=0.4117) == ("YRFI", "LEAN")
    # every zone ABOVE the YRFI-STRONG band is untouched by max_p, however
    # extreme the value (0.30 here would reclassify anything below 0.44)
    for p in (0.45, 0.50, 0.55, 0.62, 0.70):
        assert P.classify_pick_lr(p, 20, 0.90) == P.classify_pick_lr(p, 20, 0.90, max_p=0.30)
    # and inside the band, max_p is the ONLY thing that moves
    assert P.classify_pick_lr(0.30, 20, 0.90) == ("YRFI", "STRONG")
    assert P.classify_pick_lr(0.30, 20, 0.90, max_p=0.30) == ("YRFI", "LEAN")   # p >= ceiling -> LEAN


def test_shadow_score_does_not_touch_the_live_models(monkeypatch):
    if not (P._SHADOW_DIR / "meta.json").exists():
        pytest.skip("no candidate directory in this checkout")
    _reset_shadow(monkeypatch)
    before = P._load_lr_models()
    t1, b1 = _neutral_vectors()
    P._shadow_score(t1, b1, "Nobody", "Nobody Else", "2026-09-04", 20, 25.0, 10.0, False, [], "")
    after = P._load_lr_models()
    assert after[0] is before[0] and after[1] is before[1]
    assert P._load_lr_calibrator() is P._load_lr_calibrator()


# ---------------------------------------------------------------------------
# 2. Fail-open, every way it can fail
# ---------------------------------------------------------------------------

def test_missing_candidate_directory_leaves_the_fields_blank(monkeypatch, tmp_path):
    _reset_shadow(monkeypatch, tmp_path / "does-not-exist")
    t1, b1 = _neutral_vectors()
    out = P._shadow_score(t1, b1, "A", "B", "2026-09-04", 20, 25.0, 10.0, False, [], "")
    assert out == P._SHADOW_BLANK


def test_malformed_candidate_leaves_the_fields_blank(monkeypatch, tmp_path):
    d = tmp_path / "cand"; d.mkdir()
    (d / "meta.json").write_text(json.dumps({"features_t1": ["x"], "features_b1": ["y"]}), encoding="utf-8")
    (d / "lr_t1.json").write_text("this is not json", encoding="utf-8")
    _reset_shadow(monkeypatch, d)
    t1, b1 = _neutral_vectors()
    out = P._shadow_score(t1, b1, "A", "B", "2026-09-04", 20, 25.0, 10.0, False, [], "")
    assert out == P._SHADOW_BLANK
    assert P._load_shadow_models() is None


def test_candidate_asking_for_an_input_the_live_vector_lacks_is_blank(monkeypatch, tmp_path):
    """A feature name the live vector cannot supply must not become a silent 0.0."""
    if not (P._SHADOW_DIR / "meta.json").exists():
        pytest.skip("no candidate directory in this checkout")
    d = tmp_path / "cand"; d.mkdir()
    for f in ("lr_t1.json", "lr_b1.json", "calibration_v2.json"):
        d.joinpath(f).write_bytes((P._SHADOW_DIR / f).read_bytes())
    meta = json.loads((P._SHADOW_DIR / "meta.json").read_text(encoding="utf-8"))
    t1 = json.loads((d / "lr_t1.json").read_text(encoding="utf-8"))
    t1["feature_names"] = [n if n != "home_fi_form" else "home_made_up_input" for n in t1["feature_names"]]
    (d / "lr_t1.json").write_text(json.dumps(t1), encoding="utf-8")
    meta["features_t1"] = t1["feature_names"]
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    _reset_shadow(monkeypatch, d)
    a, b = _neutral_vectors()
    out = P._shadow_score(a, b, "A", "B", "2026-09-04", 20, 25.0, 10.0, False, [], "")
    assert out == P._SHADOW_BLANK


def test_kill_switch(monkeypatch):
    monkeypatch.setenv("NRFI_SHADOW_MODEL", "disabled")
    _reset_shadow(monkeypatch)
    assert P._load_shadow_models() is None
    t1, b1 = _neutral_vectors()
    assert P._shadow_score(t1, b1, "A", "B", "2026-09-04", 20, 25.0, 10.0, False, [], "") == P._SHADOW_BLANK


# ---------------------------------------------------------------------------
# 3. What the shadow writes when it works
# ---------------------------------------------------------------------------

def test_shadow_scores_a_real_game_shape(monkeypatch):
    if not (P._SHADOW_DIR / "meta.json").exists():
        pytest.skip("no candidate directory in this checkout")
    _reset_shadow(monkeypatch)
    t1, b1 = _neutral_vectors()
    out = P._shadow_score(t1, b1, "Nobody", "Nobody Else", "2026-09-04", 20, 25.0, 10.0, False, [], "")
    assert set(out) == set(SHADOW_COLS)
    assert out["shadow_model"]
    assert 0.0 < float(out["shadow_nrfi_prob"]) < 1.0
    assert 0.0 < float(out["shadow_nrfi_prob_raw"]) < 1.0
    side_ok = out["shadow_pick_label"].startswith(("STRONG YRFI", "LEAN YRFI", "LEAN NRFI", "STRONG NRFI", "PASS"))
    assert side_ok, out["shadow_pick_label"]
    # unknown pitchers -> the league mean, on both sides
    lg = fi_form.get_log().league_mean_before("2026-09-04")
    assert float(out["home_fi_form"]) == pytest.approx(lg)
    assert float(out["away_fi_form"]) == pytest.approx(lg)


def test_shadow_mirrors_the_live_data_availability_pass(monkeypatch):
    if not (P._SHADOW_DIR / "meta.json").exists():
        pytest.skip("no candidate directory in this checkout")
    _reset_shadow(monkeypatch)
    t1, b1 = _neutral_vectors()
    out = P._shadow_score(t1, b1, "A", "B", "2026-09-04", 20, 25.0, 10.0, False,
                          ["LINEUP PENDING"], "LINEUP PENDING")
    assert out["shadow_pick_label"] == "PASS - Lineup pending"
    assert out["shadow_nrfi_prob"] != ""          # the probability is still recorded


# ---------------------------------------------------------------------------
# fi_form.py -- the input itself
# ---------------------------------------------------------------------------

def test_fi_form_matches_the_validated_research_column():
    if not (ROOT / "data" / "candidates" / "factor_fi_form.csv").exists():
        pytest.skip("research column not built in this checkout")
    assert fi_form._check() == 0


def test_fi_form_uses_only_strictly_earlier_starts():
    log = fi_form.get_log()
    # the starter with the most 2026 starts
    name, starts = max(((n, s) for n, s in log.by_name.items() if any(se == 2026 for _, se, _ in s)),
                       key=lambda kv: sum(1 for _, se, _ in kv[1] if se == 2026))
    for d, se, _ in starts:
        if se != 2026:
            continue
        prior = [c for dd, ss, c in starts if ss == 2026 and dd < d]
        mu = log.league_mean_before(d)
        expect = (sum(prior) + fi_form.K_STARTS * mu) / (len(prior) + fi_form.K_STARTS)
        assert log.estimate(name, d) == pytest.approx(expect, abs=1e-12)


def test_fi_form_unknown_pitcher_is_the_league_mean_and_never_raises():
    log = fi_form.get_log()
    assert fi_form.estimate("Zzz Nobody At All", "2026-09-04") == pytest.approx(log.league_mean_before("2026-09-04"))
    assert fi_form.estimate("", "2026-09-04") == pytest.approx(log.league_mean_before("2026-09-04"))
    assert 0.5 < fi_form.estimate("Zzz Nobody", "not-a-date") < 0.9      # falls back, does not raise


def test_fi_form_normalises_accents_and_whitespace():
    log = fi_form.get_log()
    # composed vs decomposed accent forms and stray spaces must find the same pitcher
    a = log.estimate("  Eury Pérez ", "2026-09-02")
    b = log.estimate("Eury Pérez", "2026-09-02")
    assert a == pytest.approx(b)


# ---------------------------------------------------------------------------
# Plumbing: the columns survive every hop
# ---------------------------------------------------------------------------

def test_shadow_columns_are_wired_end_to_end():
    from db import migrate_csv_to_supabase, supabase_writer
    tail = tracker.FIELDS[tracker.FIELDS.index("sizing_prob"):]
    assert tail == ["sizing_prob", "home_fi_xwoba", "away_fi_xwoba"] + SHADOW_COLS
    for c in SHADOW_COLS:
        assert c in supabase_writer.PICKS_CONVERTERS, c
        assert c in migrate_csv_to_supabase.PICKS_FIELD_MAP, c
    # both writers agree on the CSV -> Supabase column name
    for c in SHADOW_COLS:
        assert migrate_csv_to_supabase.PICKS_FIELD_MAP[c][0] == c
