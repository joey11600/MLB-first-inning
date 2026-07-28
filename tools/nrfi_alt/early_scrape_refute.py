"""Refutation harness for: "scrape the first-inning market earlier (at line post)".

Claim under test: DK's *opening* first-inning line may be soft, and the system has
never observed one (median capture lead 1.02h). If true, capturing earlier should
yield NRFI prices that beat the 5.65pp pricing wall.

Three independent attacks, all on REAL captured DK prices:
  A. Verify the lead-time distribution claim itself.
  B. Dose-response: lead time varies across rows (natural experiment). If earlier
     == softer, longer-lead rows must show better NRFI ROI. Day-block bootstrap.
  C. Line-movement direction: where opened != market, does the line move in the
     direction that implies the open was NRFI-soft? And does betting the OPEN
     price instead of the market price clear break-even?
"""
import sys, math, json
import numpy as np
import pandas as pd
from datetime import datetime

RNG = np.random.default_rng(20260728)


def american_to_dec(o):
    o = float(o)
    return 1.0 + (o / 100.0 if o > 0 else 100.0 / abs(o))


def implied(o):
    o = float(o)
    return 100.0 / (o + 100.0) if o > 0 else abs(o) / (abs(o) + 100.0)


def payout(odds, won):
    if won:
        return american_to_dec(odds) - 1.0
    return -1.0


def load():
    d = pd.read_csv('data/picks_2026.csv', low_memory=False)
    d = d[d['fi_total_runs'].notna()].copy()
    d['nrfi_win'] = (d['fi_total_runs'].astype(float) == 0).astype(int)
    # game datetime in UTC (ET times -> UTC, ET is UTC-4 in season)
    def gdt(r):
        try:
            t = str(r['game_time_et']).replace(' ET', '').strip()
            dt = datetime.strptime(f"{r['date']} {t}", '%Y-%m-%d %I:%M %p')
            return pd.Timestamp(dt, tz='America/New_York').tz_convert('UTC')
        except Exception:
            return pd.NaT
    d['game_utc'] = d.apply(gdt, axis=1)
    for c in ('odds_captured_at', 'opened_captured_at'):
        d[c + '_dt'] = pd.to_datetime(d[c], errors='coerce', utc=True)
    d['lead_open_h'] = (d['game_utc'] - d['opened_captured_at_dt']).dt.total_seconds() / 3600.0
    d['lead_mkt_h'] = (d['game_utc'] - d['odds_captured_at_dt']).dt.total_seconds() / 3600.0
    return d


def day_bootstrap(df, oddscol, n=4000):
    """Block bootstrap over calendar days. Returns (roi, lo, hi, p_roi_gt_0)."""
    days = df['date'].unique()
    byday = {k: v for k, v in df.groupby('date')}
    rois = []
    for _ in range(n):
        pick = RNG.choice(days, size=len(days), replace=True)
        pl = []
        for dday in pick:
            g = byday[dday]
            pl.extend(payout(o, w) for o, w in zip(g[oddscol], g['nrfi_win']))
        if pl:
            rois.append(float(np.mean(pl)))
    rois = np.array(rois)
    return np.percentile(rois, 2.5), np.percentile(rois, 97.5), float((rois > 0).mean())


def report(tag, g, oddscol, boot=True):
    if len(g) == 0:
        print(f'  {tag:<34} n=0')
        return
    pl = np.array([payout(o, w) for o, w in zip(g[oddscol], g['nrfi_win'])])
    hit = g['nrfi_win'].mean()
    be = np.mean([implied(o) for o in g[oddscol]])
    line = (f'  {tag:<34} n={len(g):>4}  hit={hit:6.1%}  '
            f'breakeven={be:6.1%}  gap={100*(hit-be):+6.2f}pp  ROI={pl.mean():+7.2%}')
    if boot and len(g) >= 25:
        lo, hi, p = day_bootstrap(g, oddscol)
        line += f'  95%CI=[{lo:+.2%},{hi:+.2%}] P(ROI>0)={p:.2f}'
    print(line)


def main():
    d = load()
    print('=' * 108)
    print('A. LEAD-TIME DISTRIBUTION  (does the system really never see an early line?)')
    print('=' * 108)
    for col, name in (('lead_open_h', 'opened_captured_at'), ('lead_mkt_h', 'odds_captured_at')):
        s = d[col].dropna()
        s = s[s > -24]
        print(f'  {name:<22} n={len(s):>4}  median={s.median():6.2f}h  mean={s.mean():6.2f}h  '
              f'p90={s.quantile(.90):6.2f}h  max={s.max():6.2f}h  '
              f'frac>3h={100*(s>3).mean():5.1f}%  frac>6h={100*(s>6).mean():5.1f}%')

    priced = d[d['market_nrfi_odds'].notna()].copy()
    op = d[d['opened_nrfi_odds'].notna() & d['lead_open_h'].notna()].copy()
    op = op[op['lead_open_h'] > -24]

    print()
    print('=' * 108)
    print('B. DOSE-RESPONSE: if earlier == softer, longer lead must pay better (OPENED price)')
    print('=' * 108)
    print('  -- betting every game NRFI at the OPENED price, bucketed by capture lead --')
    bins = [(-99, 1.0), (1.0, 2.0), (2.0, 4.0), (4.0, 8.0), (8.0, 99)]
    for lo, hi in bins:
        g = op[(op['lead_open_h'] > lo) & (op['lead_open_h'] <= hi)]
        report(f'lead {lo:g}-{hi:g}h', g, 'opened_nrfi_odds')
    print()
    # correlation of per-game PL with lead time
    pl = np.array([payout(o, w) for o, w in zip(op['opened_nrfi_odds'], op['nrfi_win'])])
    lt = op['lead_open_h'].values
    if len(pl) > 10:
        r = np.corrcoef(lt, pl)[0, 1]
        print(f'  corr(lead_hours, per-game NRFI P/L at open) = {r:+.4f}   n={len(pl)}')
        # day-block bootstrap of the correlation
        days = op['date'].unique(); byday = {k: v for k, v in op.groupby('date')}
        rs = []
        for _ in range(2000):
            picks = RNG.choice(days, size=len(days), replace=True)
            L, P = [], []
            for dd in picks:
                g = byday[dd]
                L.extend(g['lead_open_h'].values)
                P.extend(payout(o, w) for o, w in zip(g['opened_nrfi_odds'], g['nrfi_win']))
            if len(set(L)) > 2:
                rs.append(np.corrcoef(L, P)[0, 1])
        rs = np.array(rs)
        print(f'  day-block 95% CI on that corr = [{np.percentile(rs,2.5):+.4f}, {np.percentile(rs,97.5):+.4f}]'
              f'   P(corr>0)={float((rs>0).mean()):.2f}')

    print()
    print('  -- same, restricted to model-preferred NRFI side (pick_side==NRFI) --')
    opn = op[op['pick_side'].astype(str).str.upper() == 'NRFI']
    for lo, hi in bins:
        g = opn[(opn['lead_open_h'] > lo) & (opn['lead_open_h'] <= hi)]
        report(f'NRFI-pick lead {lo:g}-{hi:g}h', g, 'opened_nrfi_odds')

    print()
    print('=' * 108)
    print('C. LINE MOVEMENT: open -> market, and does the OPEN price beat the wall?')
    print('=' * 108)
    both = d[d['opened_nrfi_odds'].notna() & d['market_nrfi_odds'].notna()].copy()
    both['moved'] = both['opened_nrfi_odds'] != both['market_nrfi_odds']
    print(f'  rows with both prices: {len(both)}   moved: {both["moved"].sum()} '
          f'({100*both["moved"].mean():.1f}%)   frozen: {(~both["moved"]).sum()}')
    mv = both[both['moved']].copy()
    if len(mv):
        mv['d_imp'] = mv['market_nrfi_odds'].map(implied) - mv['opened_nrfi_odds'].map(implied)
        print(f'  mean change in implied NRFI prob (open->market) = {mv["d_imp"].mean()*100:+.3f}pp '
              f'  median={mv["d_imp"].median()*100:+.3f}pp  n={len(mv)}')
        print(f'  NRFI price got BETTER (longer) for the bettor in {100*(mv["d_imp"]<0).mean():.1f}% of moves')
    print()
    print('  -- bet-every-game NRFI, OPEN price vs MARKET price, same games --')
    report('ALL @ opened price', both, 'opened_nrfi_odds')
    report('ALL @ market price', both, 'market_nrfi_odds')
    print()
    report('MOVED subset @ opened', mv, 'opened_nrfi_odds')
    report('MOVED subset @ market', mv, 'market_nrfi_odds')

    print()
    print('  -- model-selected NRFI bets only, open vs market --')
    bn = both[both['pick_side'].astype(str).str.upper() == 'NRFI']
    report('NRFI picks @ opened', bn, 'opened_nrfi_odds')
    report('NRFI picks @ market', bn, 'market_nrfi_odds')
    st = bn[bn['pick_strength'].astype(str).str.upper() == 'STRONG']
    report('STRONG NRFI @ opened', st, 'opened_nrfi_odds')
    report('STRONG NRFI @ market', st, 'market_nrfi_odds')

    print()
    print('=' * 108)
    print('D. HOW MUCH SOFTNESS WOULD BE NEEDED? (the wall arithmetic)')
    print('=' * 108)
    pr = d[d['market_nrfi_odds'].notna()]
    hit = pr['nrfi_win'].mean()
    be = np.mean([implied(o) for o in pr['market_nrfi_odds']])
    print(f'  n={len(pr)}  NRFI hit={hit:.3%}  mean break-even={be:.3%}  wall={100*(be-hit):.2f}pp')
    # what american price would make NRFI break even at the observed hit rate?
    need_dec = 1.0 / hit
    need_am = -100.0 / (need_dec - 1.0) if need_dec < 2 else (need_dec - 1.0) * 100.0
    mean_am = pr['market_nrfi_odds'].mean()
    print(f'  mean captured NRFI price = {mean_am:+.1f}   price needed to break even = {need_am:+.1f}')
    print(f'  => the open would have to be ~{abs(need_am-mean_am):.0f} cents better than what we capture.')

    print()
    print('=' * 108)
    print('E. OUT-OF-SAMPLE SPLIT BY TIME (first half vs second half of the priced season)')
    print('=' * 108)
    op_s = op.sort_values('date')
    if len(op_s) > 100:
        cut = op_s['date'].iloc[len(op_s) // 2]
        for lbl, g in (('EARLY half', op_s[op_s['date'] < cut]), ('LATE half', op_s[op_s['date'] >= cut])):
            print(f'  {lbl} (cut at {cut}):')
            for lo, hi in [(-99, 1.5), (1.5, 99)]:
                sub = g[(g['lead_open_h'] > lo) & (g['lead_open_h'] <= hi)]
                report(f'    lead {lo:g}-{hi:g}h @ open', sub, 'opened_nrfi_odds', boot=False)


if __name__ == '__main__':
    main()
