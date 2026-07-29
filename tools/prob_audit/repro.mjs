// Verbatim copies of the two coercers under audit.
// board-supabase.ts (PRODUCTION Supabase read path)
function num(v){ if(v===null||v===undefined||v==="") return 0; const n=Number(v); return Number.isFinite(n)?n:0; }
function nullableNum(v){ if(v===null||v===undefined||v==="") return null; const n=Number(v); return Number.isFinite(n)?n:null; }
// board.ts / csv.ts (CSV fallback path)
function toNumber(s){ if(s===undefined||s===null||s==="") return null; const n=Number(s); return Number.isFinite(n)?n:null; }

// ---- real rows read from production Supabase (see sb_check.py output) ----
const supaRows = [
 {tag:"2026-05-05 LAD@HOU", pick_side:"NRFI", edge_on_pick:null, market_nrfi_odds:"-130", market_yrfi_odds:"+100", opened_nrfi_odds:"", opened_yrfi_odds:"", bet_placed:"Y", sportsbook:"DraftKings"},
 {tag:"2026-05-05 TEX@NYY", pick_side:"YRFI", edge_on_pick:null, market_nrfi_odds:null,   market_yrfi_odds:"+115", opened_nrfi_odds:"", opened_yrfi_odds:"", bet_placed:"Y", sportsbook:"DraftKings"},
 {tag:"2026-07-17 PIT@CLE", pick_side:"NRFI", edge_on_pick:null, market_nrfi_odds:"-145", market_yrfi_odds:"+110", opened_nrfi_odds:"", opened_yrfi_odds:"", bet_placed:"N", sportsbook:"DraftKings"},
 {tag:"2026-07-25 TOR@BOS", pick_side:"NRFI", edge_on_pick:null, market_nrfi_odds:"-155", market_yrfi_odds:"+120", opened_nrfi_odds:"", opened_yrfi_odds:"", bet_placed:"N", sportsbook:"DraftKings"},
];

function chip(edgeOnPick, side, price, bet){
  // verbatim from BoardRow.tsx:1009-1051
  const edgePct = edgeOnPick != null ? edgeOnPick * 100 : null;
  const edgeStr = edgePct == null ? "" : (edgePct >= 0 ? `+${edgePct.toFixed(1)}%` : `${edgePct.toFixed(1)}%`);
  const titleText = (bet === "Y"
    ? `Bet placed: ${side} @ ${price} (edge ${edgeStr})`
    : bet === "N"
      ? `Skipped: edge ${edgeStr || "below threshold"} on ${side} @ ${price}`
      : `${side} @ ${price}${edgeStr ? ` (edge ${edgeStr})` : ""}`);
  return {visibleEdgeChip: edgeStr || "(none)", tooltip: titleText};
}

console.log("=== edge_on_pick = NULL in Postgres: what each read path renders ===");
for (const r of supaRows){
  const price = r.pick_side==="NRFI"? r.market_nrfi_odds : r.market_yrfi_odds;
  const sb  = chip(num(r.edge_on_pick),         r.pick_side, price, r.bet_placed);
  const csv = chip(toNumber(r.edge_on_pick),    r.pick_side, price, r.bet_placed);
  console.log(`\n${r.tag}  side=${r.pick_side} price=${price} bet=${r.bet_placed}`);
  console.log(`  SUPABASE (prod)  edgeOnPick=${num(r.edge_on_pick)}  chip="${sb.visibleEdgeChip}"  tip="${sb.tooltip}"`);
  console.log(`  CSV  (fallback)  edgeOnPick=${toNumber(r.edge_on_pick)}  chip="${csv.visibleEdgeChip}"  tip="${csv.tooltip}"`);
}

console.log("\n\n=== lambda_lr_total = NULL (April rows): ProjectionPanel total ===");
const apr = [
 {tag:"2026-04-05 CHC@CLE", lambda_lr_total:null, combined_lambda:0.7689},
 {tag:"2026-04-05 MIA@NYY", lambda_lr_total:null, combined_lambda:0.7735},
 {tag:"2026-04-05 STL@DET", lambda_lr_total:null, combined_lambda:0.9082},
 {tag:"2026-04-05 BAL@PIT", lambda_lr_total:null, combined_lambda:0.9153},
];
const fmtProj = n => (n==null||Number.isNaN(n)) ? "—" : n.toFixed(2);
const totalTone = n => n==null?"muted": n<=0.55?"green": n<=0.75?"lean_green": n<0.95?"muted": n<1.05?"lean_red":"red";
for (const r of apr){
  // GameDetails.tsx:693-694  detail?.lambdaLrTotal ?? detail?.combinedLambda ?? row.lambda
  const sbTotal  = num(r.lambda_lr_total)      ?? num(r.combined_lambda);
  const csvTotal = toNumber(r.lambda_lr_total) ?? toNumber(r.combined_lambda);
  console.log(`${r.tag}  SUPABASE -> "${fmtProj(sbTotal)}" (${totalTone(sbTotal)})   CSV -> "${fmtProj(csvTotal)}" (${totalTone(csvTotal)})   truth combined_lambda=${r.combined_lambda}`);
}
