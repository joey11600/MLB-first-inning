# NRFI Terminal

A Next.js dashboard for the MLB First-Inning Run Predictor — first-inning
intelligence in a Bloomberg-style terminal aesthetic.

It reads the Python predictor's existing output:

- `data/boards/board_YYYY_MM_DD.csv` — ranked slate (lambda, pick side, probabilities)
- `data/picks_YYYY.csv` — per-game model inputs (pitcher ERA/WHIP/FIP/BB9/HR9/K9,
  first-inning splits, team OBP/SLG/RPG, park factor, data-quality tags)

The dashboard expects these files at the repo root (`../data` relative to the
dashboard). Board rows always render; expanded game-detail panels appear when
a `picks_YYYY.csv` row exists for the selected date.

## Local dev

```bash
cd dashboard
npm install
npm run dev           # http://localhost:3000
```

The URL accepts `?date=YYYY-MM-DD` to jump to a specific slate.

## Generating new slates

Slates come from the Python predictor. Run it in the repo root, then refresh
the dashboard:

```bash
python mlb_first_inning_predictor.py --date 05/01/2026
```

That writes `data/boards/board_2026_05_01.csv` (and appends rows to the picks
CSV for the full stat context that powers the details drawer).

## Deploy to Vercel

From the repo root:

```bash
vercel --cwd dashboard
```

Or point Vercel's dashboard at this repo and set the project root to
`dashboard/`. `next.config.mjs` already traces `../data/**` so the CSVs ship
with the serverless bundle.

Re-deploy after generating new board CSVs to push the updated slate live, or
wire the Python job to a cron that commits the CSVs and triggers a Vercel
redeploy.

## Controls

- **Date** — choose any board you have a CSV for. Arrow buttons step through.
- **Side** — All / NRFI / PASS / YRFI.
- **Strength** — All / Lean+ / Strong.
- **Sort** — lambda (asc/desc), NRFI %, YRFI %, or board rank.
- **Find** — filter by team abbreviation.

Click any row to expand it in place — projection, probability bars, pitcher
vs pitcher with first-inning splits, offense, park factor, and data-quality
tags (`live` ≥ 80 IP / 20 G, `ltd` ≥ 20 IP / 5 G, `sm` ≥ 1 IP / 1 G, `avg` =
league default).

## File layout

```
dashboard/
  app/
    api/board/route.ts   # GET /api/board?date=YYYY-MM-DD
    layout.tsx
    page.tsx
    globals.css
  components/            # DashboardShell, BoardTable/Row, GameDetails,
                         # LambdaMeter, SummaryStrip, ControlPanel,
                         # Ticker, StatusLine (+ .module.css each)
  lib/
    board.ts             # server-side CSV loading
    csv.ts               # RFC-4180-ish parser
    types.ts
  next.config.mjs        # traces ../data into the bundle
  vercel.json
```
