import subprocess, csv, io, sys
date=sys.argv[1]; away=sys.argv[2]
shas = subprocess.run(['git','log','--reverse','--format=%H %ad %s','--date=iso',
                       '--since='+date, '--until=2026-06-25','--','data/picks_2026.csv'],
                      capture_output=True,text=True).stdout.strip().split('\n')
prev=None
for line in shas:
    sha=line.split()[0]; meta=' '.join(line.split()[1:])
    try:
        blob=subprocess.run(['git','show',f'{sha}:data/picks_2026.csv'],capture_output=True,text=True,encoding='utf-8').stdout
    except Exception: continue
    if not blob: continue
    for r in csv.DictReader(io.StringIO(blob)):
        if r['date']==date and r['away_team']==away:
            cur=(r['yrfi_prob'],r['nrfi_prob'],r['market_yrfi_odds'],r['implied_yrfi_prob'],r['edge_yrfi'],r['edge_on_pick'],r['bet_placed'])
            if cur!=prev:
                print(meta[:30], 'yrfi_p=%s imp=%s edge_yrfi=%s edge_pick=%s odds=%s bet=%s'%(cur[0],cur[3],cur[4],cur[5],cur[2],cur[6]))
                prev=cur
            break
