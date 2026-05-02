-- =============================================================================
-- NRFI Terminal — Supabase / Postgres Schema (v1)
-- =============================================================================
--
-- Phase 1 of the CSV → Postgres migration.  Goal: make the schema mirror the
-- existing picks_2026.csv field-for-field so the migration is mechanical and
-- reversible.  Normalization (separating pitcher / batting / odds into their
-- own tables) is a Phase 2+ concern; for now we want a single picks_<year>
-- table that maps 1:1 to tracker.FIELDS.
--
-- This schema is run ONCE in the Supabase SQL editor when setting up the
-- project.  It's idempotent (CREATE TABLE IF NOT EXISTS) so safe to re-run.
-- See SUPABASE_SETUP.md for the full setup walkthrough.
--
-- Tables created:
--   picks_2026          — one row per game per slate (mirrors picks_2026.csv)
--   pick_changes        — journal of intraday pick flips (mirrors pick_changes.csv)
--   system_errors       — cron failure log (mirrors system_errors.csv)
--   live_game_state     — real-time per-game inning/score (Phase 4 worker)
--   odds_history        — per-scrape line history (Phase 5 multi-book)
--
-- Realtime publication: picks_2026, pick_changes, live_game_state are added
-- so the dashboard can subscribe to row changes via Supabase Realtime.

-- =============================================================================
-- picks_2026 — the season ledger.  Mirrors tracker.FIELDS.
-- =============================================================================
CREATE TABLE IF NOT EXISTS picks_2026 (
  -- Identity (composite primary key handles doubleheaders correctly:
  -- DH-1 and DH-2 share teams + date but have distinct game_pk)
  date          DATE NOT NULL,
  game_pk       TEXT NOT NULL,
  PRIMARY KEY (date, game_pk),

  season        INT NOT NULL,
  game_number   INT NOT NULL DEFAULT 1,
  double_header TEXT DEFAULT 'N',
  away_team     TEXT NOT NULL,
  home_team     TEXT NOT NULL,
  game_time_et  TEXT,

  -- Pitchers (name + id + quality flag: live/ltd/sm/avg)
  away_pitcher       TEXT,
  home_pitcher       TEXT,
  away_pitcher_id    INT,
  home_pitcher_id    INT,
  away_pitcher_q     TEXT,
  home_pitcher_q     TEXT,

  -- Batting quality
  away_batting_q     TEXT,
  home_batting_q     TEXT,

  -- Pitcher season stats
  away_era REAL, home_era REAL,
  away_whip REAL, home_whip REAL,
  away_fip REAL, home_fip REAL,
  away_bb9 REAL, home_bb9 REAL,
  away_hr9 REAL, home_hr9 REAL,
  away_k9 REAL, home_k9 REAL,

  -- Pitcher Statcast / advanced
  away_xera REAL, home_xera REAL,
  away_whiff_pct_rank REAL, home_whiff_pct_rank REAL,

  -- Pitcher recent form
  home_p_last5_pitcher_nrfi  REAL,
  away_p_last5_pitcher_nrfi  REAL,
  home_p_last10_pitcher_nrfi REAL,
  away_p_last10_pitcher_nrfi REAL,

  -- Pitcher-vs-team familiarity + opener detection (Phase F)
  home_pvt_nrfi_rate     REAL,
  away_pvt_nrfi_rate     REAL,
  home_avg_ip_per_start  REAL,
  away_avg_ip_per_start  REAL,

  -- Team batting (season aggregates)
  away_obp REAL, home_obp REAL,
  away_slg REAL, home_slg REAL,
  away_rpg REAL, home_rpg REAL,

  -- Top-3 batter aggregates (lineup-aware when available)
  home_top3c_obp REAL, away_top3c_obp REAL,
  home_top3c_slg REAL, away_top3c_slg REAL,
  home_top3c_iso REAL, away_top3c_iso REAL,
  home_top3c_source TEXT,  -- 'lineup' | 'team_fallback'
  away_top3c_source TEXT,

  -- Per-batter top-3 detail (JSON arrays of {id, name, bats, obp, slg, iso, ab})
  home_lineup_json JSONB DEFAULT '[]'::jsonb,
  away_lineup_json JSONB DEFAULT '[]'::jsonb,

  -- Environment
  park_factor REAL,
  wx_temp_c REAL,
  wx_wind_kmh REAL,
  wx_humidity REAL,
  wx_is_dome SMALLINT,

  -- Umpire
  home_plate_ump_id        INT,
  home_plate_ump_nrfi_rate REAL,

  -- Model output (probabilities + lambdas)
  away_proj_runs    REAL,
  home_proj_runs    REAL,
  combined_lambda   REAL,
  lambda_lr_t1      REAL,
  lambda_lr_b1      REAL,
  lambda_lr_total   REAL,
  nrfi_prob         REAL,
  yrfi_prob         REAL,
  over_1_5_prob     REAL,
  under_1_5_prob    REAL,

  -- Pick decision
  pick_side       TEXT,    -- 'NRFI' | 'YRFI' | 'PASS'
  pick_strength   TEXT,    -- 'STRONG' | 'LEAN' | 'NO EDGE' | 'NO DATA' | 'STARTER PENDING' | 'LINEUP PENDING' | 'LOW LAMBDA'
  pick_label      TEXT,
  blended_inputs  INT,

  -- LR feature contributions per half (T4.15)
  top_factors_t1_json JSONB DEFAULT '[]'::jsonb,
  top_factors_b1_json JSONB DEFAULT '[]'::jsonb,

  -- Timestamps
  created_at  TIMESTAMPTZ DEFAULT NOW(),
  updated_at  TIMESTAMPTZ DEFAULT NOW(),

  -- Grade outcome (filled by tracker.grade_picks)
  actual_result   TEXT,    -- 'NRFI' | 'YRFI' | 'POSTPONED' | 'SUSPENDED'
  graded_result   TEXT,    -- 'WIN' | 'LOSS' | 'PASS' | 'POSTPONED' | 'SUSPENDED'
  fi_away_runs    INT,
  fi_home_runs    INT,
  fi_total_runs   INT,
  graded_at       TIMESTAMPTZ,

  -- Live odds (current snapshot — "closing line" once locked)
  sportsbook         TEXT,
  market_nrfi_odds   TEXT,
  market_yrfi_odds   TEXT,
  odds_captured_at   TIMESTAMPTZ,
  implied_nrfi_prob  REAL,
  implied_yrfi_prob  REAL,
  edge_nrfi          REAL,
  edge_yrfi          REAL,
  edge_on_pick       REAL,

  -- Opening line (T4.28 CLV)
  opened_nrfi_odds      TEXT,
  opened_yrfi_odds      TEXT,
  opened_captured_at    TIMESTAMPTZ,
  clv_pct               REAL,

  -- Bet outcome (filled by tracker._apply_odds_to_row + tracker._calc_pnl)
  bet_placed         TEXT,    -- 'Y' | 'N' | ''
  units_risked       REAL,
  profit_loss_units  REAL
);

-- Hot-path indices for the dashboard's most common queries
CREATE INDEX IF NOT EXISTS idx_picks_date           ON picks_2026 (date);
CREATE INDEX IF NOT EXISTS idx_picks_team_date      ON picks_2026 (away_team, home_team, date);
CREATE INDEX IF NOT EXISTS idx_picks_pick_side      ON picks_2026 (pick_side, pick_strength);
CREATE INDEX IF NOT EXISTS idx_picks_graded         ON picks_2026 (graded_result)
  WHERE graded_result IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_picks_bet_y          ON picks_2026 (date, bet_placed)
  WHERE bet_placed = 'Y';

-- =============================================================================
-- pick_changes — intraday pick-flip journal.  Mirrors pick_changes.csv (T2.5).
-- =============================================================================
CREATE TABLE IF NOT EXISTS pick_changes (
  id              BIGSERIAL PRIMARY KEY,
  captured_at_utc TIMESTAMPTZ NOT NULL,
  date            DATE NOT NULL,
  game_pk         TEXT,
  away_team       TEXT,
  home_team       TEXT,
  game_time_et    TEXT,
  old_pick_label  TEXT,
  new_pick_label  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pick_changes_date    ON pick_changes (date);
CREATE INDEX IF NOT EXISTS idx_pick_changes_pulled  ON pick_changes (captured_at_utc DESC);

-- =============================================================================
-- system_errors — cron failure log.  Mirrors data/system_errors.csv (T1.3).
-- =============================================================================
CREATE TABLE IF NOT EXISTS system_errors (
  id              BIGSERIAL PRIMARY KEY,
  captured_at_utc TIMESTAMPTZ NOT NULL,
  date            DATE,
  step            TEXT,
  exit_code       INT,
  message         TEXT
);
CREATE INDEX IF NOT EXISTS idx_system_errors_recent ON system_errors (captured_at_utc DESC);

-- =============================================================================
-- live_game_state — Phase 4 real-time MLB state (every ~10s during games).
-- Updated exclusively by the Railway live-state worker.  Dashboard subscribes
-- to row changes via Supabase Realtime so inning/score updates push without
-- the dashboard polling.
-- =============================================================================
CREATE TABLE IF NOT EXISTS live_game_state (
  game_pk             TEXT PRIMARY KEY,
  date                DATE NOT NULL,
  away_team           TEXT,
  home_team           TEXT,
  status              TEXT,    -- "Pre-Game", "Warmup", "In Progress", "Final", "Delayed", etc.
  abstract_game_state TEXT,    -- "Preview", "Live", "Final"
  current_inning      INT,
  inning_state        TEXT,    -- "Top", "Middle", "Bottom", "End"
  away_score          INT,
  home_score          INT,
  fi_away_runs        INT,
  fi_home_runs        INT,
  fi_total_runs       INT,
  fi_complete         BOOLEAN DEFAULT FALSE,
  updated_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_live_state_date      ON live_game_state (date);

-- =============================================================================
-- odds_history — Phase 5 line-movement timeline (every odds scrape).  Lets us
-- chart line movement and capture true closing lines per game.
-- =============================================================================
CREATE TABLE IF NOT EXISTS odds_history (
  id                BIGSERIAL PRIMARY KEY,
  date              DATE NOT NULL,
  game_pk           TEXT,
  away_team         TEXT,
  home_team         TEXT,
  sportsbook        TEXT NOT NULL,
  captured_at       TIMESTAMPTZ NOT NULL,
  start_time_utc    TIMESTAMPTZ,
  market_nrfi_odds  TEXT,
  market_yrfi_odds  TEXT
);
CREATE INDEX IF NOT EXISTS idx_odds_history_game    ON odds_history (date, game_pk, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_odds_history_book    ON odds_history (sportsbook, captured_at DESC);

-- =============================================================================
-- Realtime publication.  Tables added here push row changes to subscribed
-- clients (the dashboard) within ~200ms.  Excludes odds_history because the
-- dashboard doesn't need to subscribe to every odds scrape — it queries on
-- demand for line-movement charts.
-- =============================================================================
DO $$
BEGIN
  -- Add to publication, ignoring "already member" errors so this is idempotent
  BEGIN ALTER PUBLICATION supabase_realtime ADD TABLE picks_2026;       EXCEPTION WHEN duplicate_object THEN NULL; END;
  BEGIN ALTER PUBLICATION supabase_realtime ADD TABLE pick_changes;     EXCEPTION WHEN duplicate_object THEN NULL; END;
  BEGIN ALTER PUBLICATION supabase_realtime ADD TABLE live_game_state;  EXCEPTION WHEN duplicate_object THEN NULL; END;
END
$$;

-- =============================================================================
-- updated_at auto-bump trigger on picks_2026.  Saves us from having to set
-- it manually on every UPDATE.
-- =============================================================================
CREATE OR REPLACE FUNCTION trg_picks_2026_set_updated_at() RETURNS trigger AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS picks_2026_updated_at ON picks_2026;
CREATE TRIGGER picks_2026_updated_at
  BEFORE UPDATE ON picks_2026
  FOR EACH ROW
  EXECUTE FUNCTION trg_picks_2026_set_updated_at();
