"""Scratch: walk git history of data/picks_2026.csv and print one row's evolution."""
import subprocess, csv, io, sys

PK = sys.argv[1] if len(sys.argv) > 1 else '824992'
SINCE = sys.argv[2] if len(sys.argv) > 2 else '2026-06-17'
UNTIL = sys.argv[3] if len(sys.argv) > 3 else '2026-06-21'

log = subprocess.run(
    ['git', 'log', '--format=%H|%ad|%s', '--date=iso', f'--since={SINCE}',
     f'--until={UNTIL}', '--reverse', '--', 'data/picks_2026.csv'],
    capture_output=True, text=True, check=True).stdout.strip().splitlines()

COLS = ['nrfi_prob', 'yrfi_prob', 'pick_side', 'pick_strength', 'bet_placed',
        'market_nrfi_odds', 'market_yrfi_odds', 'implied_yrfi_prob',
        'edge_nrfi', 'edge_yrfi', 'edge_on_pick', 'odds_captured_at',
        'graded_result', 'created_at', 'pick_label']
prev = None
for line in log:
    sha, date, subj = line.split('|', 2)
    try:
        blob = subprocess.run(['git', 'show', f'{sha}:data/picks_2026.csv'],
                              capture_output=True, text=True, check=True).stdout
    except subprocess.CalledProcessError:
        continue
    rd = csv.DictReader(io.StringIO(blob))
    match = [r for r in rd if (r.get('game_pk') or '') == PK]
    if not match:
        continue
    r = match[0]
    cur = {c: r.get(c, '') for c in COLS}
    if cur != prev:
        changed = [c for c in COLS if prev is None or cur[c] != prev[c]]
        print(f'{sha[:8]} {date[:19]} {subj[:26]:26} '
              + ' '.join(f'{c}={cur[c]!r}' for c in changed))
        prev = cur
