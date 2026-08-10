"""Suite-wide safety rails.

THE INCIDENT THIS EXISTS TO PREVENT (2026-08-09).  Twice in one session a
test run wrote fabricated rows into the PRODUCTION Supabase table and they
appeared on the public dashboard -- once with the headline "THE No.1 PLAY /
CCC at DDD / STAKE 10u", once as four plausible-looking games (CWS@TB 8u,
KC@COL 5u, ...) that were indistinguishable from real picks at a glance.

The mechanism is quiet and completely reasonable-looking: `tracker.log_picks`
and `tracker.import_odds` both end by calling `_mirror_picks_to_supabase`,
which is a NO-OP when the Supabase environment variables are unset and a
LIVE PRODUCTION WRITE when they are set.  On the operator's machine they are
set -- that is how the real predictor runs.  So any test that drives either
function writes to production, and nothing in the test says so.

The guards below are `autouse`, so they apply to every test in this
directory whether or not the author thought about it.  That is deliberate:
the failure mode is one nobody thinks about, and an opt-in guard would have
prevented neither incident.

If a test ever legitimately needs to exercise the mirror, assert on the
recorded calls (`supabase_calls`) rather than lifting the guard.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _no_production_writes(monkeypatch):
    """Sever every path from the test suite to production Supabase.

    Belt AND braces, because either alone has a hole:
      * unsetting the env vars makes `_mirror_picks_to_supabase` take its
        own no-op branch -- but only for code that re-reads the env at call
        time, and a module that cached a client at import time would slip
        through;
      * replacing the mirror function itself catches the cached-client case
        -- but only for the one entry point we patched.
    """
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_ANON_KEY",
                "SUPABASE_KEY", "NEXT_PUBLIC_SUPABASE_URL"):
        monkeypatch.delenv(var, raising=False)

    calls = []

    def _blocked(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    import tracker
    monkeypatch.setattr(tracker, "_mirror_picks_to_supabase", _blocked,
                        raising=False)
    for name in ("_supabase_client", "_get_supabase", "get_client"):
        if hasattr(tracker, name):
            monkeypatch.setattr(
                tracker, name,
                lambda *a, **k: pytest.fail(
                    "a test tried to open a Supabase connection -- see "
                    "tests/conftest.py"),
                raising=False)
    return calls


@pytest.fixture(autouse=True)
def _no_telegram(monkeypatch):
    """Same reasoning for the operator's phone.  A test that drives a commit
    path can otherwise fire a real 'BET LOCKED' push about a game that does
    not exist."""
    import tracker
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    for name in ("_telegram_send", "_send_telegram", "_notify_telegram"):
        if hasattr(tracker, name):
            monkeypatch.setattr(tracker, name, lambda *a, **k: None,
                                raising=False)


@pytest.fixture(autouse=True)
def _no_real_ledger_writes(monkeypatch):
    """Refuse to let a test WRITE the real data/picks_<year>.csv.

    Guarding the write, not `_csv_path`, is deliberate.  Several legitimate
    tests stub `_read_rows` and let `_committed_on` compute the real path --
    the path value never gets used, so failing there would punish correct
    tests.  The write is the destructive act, and `_write_rows` is atomic, so
    a test that forgets to redirect it succeeds silently and corrupts the
    operator's live ledger.
    """
    import pathlib

    import tracker
    real_write = tracker._write_rows
    repo_data = pathlib.Path(tracker.__file__).resolve().parent / "data"

    def guarded(path, rows, *a, **k):
        target = pathlib.Path(path).resolve()
        if target.parent == repo_data:
            raise AssertionError(
                f"test tried to write the REAL ledger at {target}. "
                f"Monkeypatch tracker._csv_path to a tmp_path file. "
                f"See tests/conftest.py.")
        return real_write(path, rows, *a, **k)

    monkeypatch.setattr(tracker, "_write_rows", guarded)
