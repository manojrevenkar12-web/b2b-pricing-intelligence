"""The pricing dashboard: one self-contained HTML file built from the marts.

It opens in any browser without a server (data embedded, charts drawn as SVG), filters by region,
service line and year, and shows what a pricing lead looks at weekly: win rate and discount
trends, the price waterfall, where margin leaks and who to talk to, what the price test showed, the
bookings forecast and the recommendations for open quotes. It only reads the marts and the model
registry, never the simulation's truth.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from bpi import warehouse
from bpi.config import Settings, paths
from bpi.models import leakage
from bpi.models.recommend import recommend
from bpi.pipeline import load_current


def _cube() -> list[dict]:
    q = warehouse.read(
        """
        select extract(year from quote_date)::int as year, extract(quarter from quote_date)::int as quarter,
               region, service_line,
               count(*) as quotes,
               sum(case when is_decided then 1 else 0 end) as decided,
               sum(won) as won,
               sum(net_eur) as quoted_eur,
               sum(case when won = 1 then net_eur else 0 end) as booked_eur,
               sum(list_eur) as list_eur_q,
               sum(net_eur) as net_eur_q
        from marts.fct_quotes group by all
        """
    )
    w = warehouse.read(
        """
        select extract(year from invoice_date)::int as year, extract(quarter from invoice_date)::int as quarter,
               region, service_line,
               sum(list_eur) as list_eur, sum(negotiated_discount_eur) as negotiated, sum(billing_deviation_eur) as billing,
               sum(freight_eur) as freight, sum(expedite_eur) as expedite, sum(warranty_eur) as warranty,
               sum(early_payment_eur) as early_payment, sum(terms_cost_eur) as terms, sum(rebate_eur) as rebate,
               sum(invoice_eur) as invoice_eur, sum(pocket_eur) as pocket_eur, sum(cost_eur) as cost_eur
        from marts.fct_price_waterfall group by all
        """
    )
    c = q.merge(w, on=["year", "quarter", "region", "service_line"], how="outer").fillna(0)
    return [
        {
            k: (round(float(v), 2) if isinstance(v, float | np.floating) else (int(v) if isinstance(v, np.integer) else v))
            for k, v in r.items()
        }
        for r in c.to_dict("records")
    ]


def _price_test() -> list[dict]:
    d = warehouse.read(
        """
        select service_line, price_test_cell as cell, count(*) as quotes, avg(won) as win_rate
        from marts.fct_quotes where price_test_cell is not null and is_decided group by all order by 1, 2
        """
    )
    return d.round(4).to_dict("records")


def _discount_bands() -> list[dict]:
    d = warehouse.read(
        """
        select service_line, round(least(discount_pct, 0.249) / 0.05) * 5 as band, count(*) as quotes, avg(won) as win_rate
        from marts.fct_quotes where is_decided and price_test_cell is null group by all having count(*) >= 40 order by 1, 2
        """
    )
    return d.round(4).to_dict("records")


def _open_recommendations(st: Settings) -> list[dict]:
    q = warehouse.quotes()
    open_q = q[q["status"] == "open"].sort_values("quote_date", ascending=False).head(400)
    if open_q.empty:
        return []
    m = load_current()
    recs = recommend(open_q.reset_index(drop=True), m.response, st.guardrails)
    meta = open_q.reset_index(drop=True)
    out = []
    for r, (_, row) in zip(recs, meta.iterrows(), strict=True):
        out.append(
            {
                "quote_id": r.quote_id,
                "customer": row["customer_name"],
                "region": row["region"],
                "service_line": row["service_line"],
                "status": r.status,
                "current_eur": r.current_price_eur,
                "recommended_eur": r.recommended_price_eur,
                "low_eur": round(r.low_ratio * row["list_eur"], 2),
                "high_eur": round(r.high_ratio * row["list_eur"], 2),
                "p_win_current": r.p_win_current,
                "p_win_recommended": r.p_win_recommended,
                "gain_eur": round(r.expected_margin_recommended_eur - r.expected_margin_current_eur, 2),
                "reason": r.reasons[0] if r.reasons else "",
            }
        )
    return out


def build(st: Settings, evaluation: dict | None = None, out: Path | None = None) -> Path:
    w = warehouse.read("select * from marts.fct_price_waterfall")
    b = warehouse.read("select * from marts.fct_billing_exceptions")
    rb = warehouse.read("select * from marts.fct_rebate_reconciliation")
    s = leakage.summary(w, b, rb, st.leakage_z)
    reps = s["rep_table"].merge(warehouse.read("select rep_id, rep_name from marts.dim_rep"), on="rep_id")
    forecast_rows = []
    if evaluation and "forecast_chart" in evaluation:
        forecast_rows = evaluation["forecast_chart"]
    m = load_current()
    data = {
        "as_of": str(st.as_of),
        "model_version": m.meta["version"],
        "validated_lines": sorted(m.meta["validated_slopes"]),
        "cube": _cube(),
        "price_test": _price_test(),
        "discount_bands": _discount_bands(),
        "reps": reps[["rep_name", "region", "invoices", "invoiced_eur", "excess_rate", "z", "excess_eur", "flagged"]]
        .round(4)
        .to_dict("records"),
        "billing": {"count": len(b), "eur": round(float(b["gap_eur"].sum()), 2)},
        "rebates": {
            "customers": s["rebate_overpaid_customers"],
            "eur": s["rebate_overpaid_eur"],
            "incomplete": s["incomplete_rebate_years"],
        },
        "open": _open_recommendations(st),
        "forecast": forecast_rows,
    }
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data, default=str))
    out = out or paths().reports / "dashboard.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gas Services Pricing</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75..100,400..800&display=swap" rel="stylesheet">
<style>
:root{
  --ink:#14213d; --ink-2:#3d4a63; --muted:#7d8799; --line:#d9dee7; --paper:#f6f7f9; --card:#ffffff;
  --steel:#2f4b7c; --steel-2:#9db1d3; --ember:#c2410c; --ember-soft:#fde4d6; --ok:#0f766e;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ink:#e8ecf3; --ink-2:#b8c1d1; --muted:#8792a6; --line:#2a3345; --paper:#0e1422; --card:#151d2e;
    --steel:#8fb0e6; --steel-2:#3b5687; --ember:#f08a4b; --ember-soft:#3a2418; --ok:#3cc4b4;
  }
}
:root[data-theme="dark"]{
  --ink:#e8ecf3; --ink-2:#b8c1d1; --muted:#8792a6; --line:#2a3345; --paper:#0e1422; --card:#151d2e;
  --steel:#8fb0e6; --steel-2:#3b5687; --ember:#f08a4b; --ember-soft:#3a2418; --ok:#3cc4b4;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:Archivo,system-ui,sans-serif;font-size:15px;line-height:1.5}
header{padding:28px 32px 18px;border-bottom:1px solid var(--line);display:flex;flex-wrap:wrap;gap:16px 32px;align-items:flex-end;justify-content:space-between}
h1{font-stretch:78%;font-weight:800;font-size:34px;letter-spacing:-.01em;margin:0;line-height:1.05}
.sub{color:var(--muted);font-size:13px;margin-top:6px}
.filters{display:flex;gap:10px;flex-wrap:wrap}
select{font:inherit;font-size:14px;padding:7px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--ink)}
select:focus-visible,button:focus-visible{outline:2px solid var(--steel);outline-offset:2px}
main{padding:24px 32px 48px;display:grid;grid-template-columns:repeat(12,1fr);gap:20px;max-width:1400px}
.kpis{grid-column:1/-1;display:grid;grid-template-columns:repeat(5,1fr);gap:0;border:1px solid var(--line);border-radius:10px;background:var(--card);overflow:hidden}
.kpi{padding:16px 18px;border-right:1px solid var(--line)}
.kpi:last-child{border-right:0}
.kpi b{display:block;font-stretch:80%;font-size:30px;font-weight:700;line-height:1.1}
.kpi span{color:var(--muted);font-size:13px}
.kpi .d{font-size:12px;color:var(--ink-2)}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px 20px}
section h2{font-size:17px;font-weight:700;margin:0 0 2px}
section p.note{color:var(--muted);font-size:13px;margin:0 0 12px}
.w6{grid-column:span 6}.w7{grid-column:span 7}.w5{grid-column:span 5}.w12{grid-column:1/-1}
svg{display:block;width:100%;height:auto}
svg text{fill:var(--muted);font-family:Archivo,sans-serif;font-size:11px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;color:var(--ink-2);border-bottom:1px solid var(--line);padding:6px 8px}
td{border-bottom:1px solid var(--line);padding:6px 8px;font-variant-numeric:tabular-nums}
td.n,th.n{text-align:right}
tr.flag td{background:var(--ember-soft)}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line);color:var(--ink-2)}
.pill.rec{border-color:var(--ok);color:var(--ok)}
.pill.hold{border-color:var(--ember);color:var(--ember)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin-top:6px;flex-wrap:wrap}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.callouts{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}
.callout{border-left:3px solid var(--ember);padding:4px 10px}
.callout b{font-stretch:80%;font-size:22px;display:block}
.callout span{font-size:13px;color:var(--muted)}
.scroll{max-height:360px;overflow:auto}
@media (max-width:900px){
  header,main{padding-left:16px;padding-right:16px}
  .kpis{grid-template-columns:repeat(2,1fr)}
  .kpi{border-bottom:1px solid var(--line)}
  .w6,.w7,.w5{grid-column:1/-1}
}
@media (prefers-reduced-motion:no-preference){ .bar{transition:height .25s ease, y .25s ease} }
</style>
</head>
<body>
<header>
  <div>
    <h1>Gas Services pricing</h1>
    <div class="sub" id="sub"></div>
  </div>
  <div class="filters">
    <label><span class="sr">Region </span><select id="f-region" aria-label="Region"></select></label>
    <label><span class="sr">Service line </span><select id="f-line" aria-label="Service line"></select></label>
    <label><span class="sr">Year </span><select id="f-year" aria-label="Year"></select></label>
  </div>
</header>
<main>
  <div class="kpis" id="kpis"></div>

  <section class="w7">
    <h2>From list price to pocket price</h2>
    <p class="note">Every invoice in the selection, as a share of list price. Ember steps are concessions that never show on the invoice.</p>
    <div id="waterfall"></div>
  </section>
  <section class="w5">
    <h2>Win rate and discount by quarter</h2>
    <p class="note">Decided quotes, by quote date.</p>
    <div id="trend"></div>
    <div class="legend"><span><i style="background:var(--steel)"></i>win rate</span><span><i style="background:var(--ember)"></i>average discount</span></div>
  </section>

  <section class="w6">
    <h2>What the price test showed</h2>
    <p class="note">Quotes that got a random price change on top of the rep's price. Within the test, higher prices clearly win less often; in everyday quotes (below), bigger discounts do not seem to win more, because reps discount the deals they are about to lose.</p>
    <div id="pricetest"></div>
    <div id="bands"></div>
  </section>
  <section class="w6">
    <h2>Where margin leaks</h2>
    <p class="note">Reps whose off-invoice concessions sit far above peers selling the same lines in the same region (robust z above 3 is flagged).</p>
    <div class="scroll"><table id="reps"></table></div>
    <div class="callouts" id="callouts"></div>
  </section>

  <section class="w12">
    <h2>Bookings forecast</h2>
    <p class="note">Forecast made at the start of each test month against what was booked, with the 80% interval. All service lines in the selection.</p>
    <div id="forecast"></div>
  </section>

  <section class="w12">
    <h2>Open quotes: recommended prices</h2>
    <p class="note" id="recnote"></p>
    <div class="scroll"><table id="open"></table></div>
  </section>
</main>
<script>
const D = /*DATA*/null;
const $ = s => document.querySelector(s);
const eur = v => (Math.abs(v) >= 1e9 ? (v/1e9).toFixed(1)+' bn€' : Math.abs(v) >= 1e6 ? (v/1e6).toFixed(1)+' M€' : Math.abs(v) >= 1e3 ? (v/1e3).toFixed(0)+' k€' : v.toFixed(0)+' €');
const pct = (v, d=1) => isFinite(v) ? (v*100).toFixed(d)+'%' : '–';
const LINES = {spare_parts:'Spare parts', field_service:'Field service', repairs:'Repairs', upgrades:'Upgrades', ltsa:'Service agreements'};
const REG = {EMEA:'EMEA', AMERICAS:'Americas', APAC:'Asia Pacific', MIDDLE_EAST:'Middle East'};
function opts(el, items, all){ el.innerHTML = `<option value="">${all}</option>` + items.map(([v,l])=>`<option value="${v}">${l}</option>`).join(''); }
opts($('#f-region'), Object.entries(REG), 'All regions');
opts($('#f-line'), Object.entries(LINES), 'All service lines');
const years = [...new Set(D.cube.map(r=>r.year))].filter(y=>y>0).sort();
opts($('#f-year'), years.map(y=>[y,y]), 'All years');
$('#sub').textContent = `Data to ${D.as_of}. Model ${D.model_version}. Recommendations active for ${D.validated_lines.map(l=>LINES[l].toLowerCase()).join(' and ')}.`;
try { const s = JSON.parse(localStorage.getItem('bpi-filters')||'{}'); for (const k of ['region','line','year']) if (s[k]!==undefined) $('#f-'+k).value = s[k]; } catch(e) {}
['#f-region','#f-line','#f-year'].forEach(s=>$(s).addEventListener('change', render));

function sel(){ return {region:$('#f-region').value, line:$('#f-line').value, year:$('#f-year').value}; }
function rows(f, ignoreYear){ return D.cube.filter(r => (!f.region||r.region===f.region) && (!f.line||r.service_line===f.line) && (ignoreYear||!f.year||String(r.year)===f.year)); }
function sum(rs,k){ return rs.reduce((a,r)=>a+(r[k]||0),0); }

function svg(w,h,body){ return `<svg viewBox="0 0 ${w} ${h}" role="img">${body}</svg>`; }

function kpis(rs){
  const win = sum(rs,'won')/sum(rs,'decided'), disc = 1-sum(rs,'net_eur_q')/sum(rs,'list_eur_q');
  const pocket = sum(rs,'pocket_eur')/sum(rs,'list_eur'), off = (sum(rs,'invoice_eur')-sum(rs,'pocket_eur'))/sum(rs,'invoice_eur');
  const pm = (sum(rs,'pocket_eur')-sum(rs,'cost_eur'))/sum(rs,'pocket_eur');
  const k = [[pct(win),'win rate',`${sum(rs,'decided').toLocaleString()} decided quotes`],[pct(disc),'average discount off list',`${eur(sum(rs,'quoted_eur'))} quoted`],
    [pct(pocket),'pocket price, share of list',`${eur(sum(rs,'pocket_eur'))} kept`],[pct(off),'of invoiced revenue given away off-invoice',''],[pct(pm),'pocket margin','after every concession']];
  $('#kpis').innerHTML = k.map(([v,l,d])=>`<div class="kpi"><b>${v}</b><span>${l}</span><div class="d">${d}</div></div>`).join('');
}

function waterfall(rs){
  const L = sum(rs,'list_eur'); if(!L){$('#waterfall').innerHTML='<p class="note">No invoices in this selection.</p>';return;}
  const steps = [['List',L,'base'],['Negotiated discount',-sum(rs,'negotiated'),'on'],['Billing deviation',-sum(rs,'billing'),'on'],['Freight',-sum(rs,'freight'),'off'],['Expedites',-sum(rs,'expedite'),'off'],['Warranty',-sum(rs,'warranty'),'off'],['Early payment',-sum(rs,'early_payment'),'off'],['Payment terms',-sum(rs,'terms'),'off'],['Rebates',-sum(rs,'rebate'),'off'],['Pocket',sum(rs,'pocket_eur'),'end']];
  const W=720,H=260,top=16,bot=46,lo=0.70,y=v=>top+(1-(v-lo)/(1-lo))*(H-top-bot),bw=W/steps.length;
  let lvl=0, g='';
  steps.forEach(([n,v,t],i)=>{
    const s = v/L; let a,b;
    if(t==='base'){a=0;b=s;lvl=s}else if(t==='end'){a=0;b=s}else{a=lvl+s;b=lvl;lvl=lvl+s}
    const col = t==='off'?'var(--ember)':t==='end'?'var(--ok)':t==='base'?'var(--steel)':'var(--steel-2)';
    const yt=y(Math.max(a,b)), yb=y(Math.max(Math.min(a,b),lo));
    g+=`<rect class="bar" x="${i*bw+8}" y="${yt}" width="${bw-16}" height="${Math.max(1,yb-yt)}" fill="${col}"><title>${n}: ${pct(Math.abs(s),2)} of list (${eur(Math.abs(v))})</title></rect>`;
    g+=`<text x="${i*bw+bw/2}" y="${yt-4}" text-anchor="middle">${t==='base'||t==='end'?pct(s,1):'−'+pct(Math.abs(s),1)}</text>`;
    g+=`<text x="${i*bw+bw/2}" y="${H-bot+16}" text-anchor="middle">${n.split(' ')[0]}</text><text x="${i*bw+bw/2}" y="${H-bot+29}" text-anchor="middle">${n.split(' ').slice(1).join(' ')}</text>`;
  });
  $('#waterfall').innerHTML = svg(W,H,g);
}

function trend(f){
  const rs = rows(f,true); const by={};
  rs.forEach(r=>{ if(!r.year) return; const k=r.year+' Q'+r.quarter; (by[k]=by[k]||[]).push(r); });
  const keys = Object.keys(by).sort(); const W=520,H=230,l=34,r=10,t=10,b=28;
  const win = keys.map(k=>sum(by[k],'won')/Math.max(1,sum(by[k],'decided'))), dis = keys.map(k=>1-sum(by[k],'net_eur_q')/sum(by[k],'list_eur_q'));
  const x=i=>l+i*(W-l-r)/Math.max(1,keys.length-1), y=v=>t+(1-v/0.7)*(H-t-b);
  const path=(a)=>a.map((v,i)=>`${i?'L':'M'}${x(i)},${y(isFinite(v)?v:0)}`).join('');
  let g=''; [0,.2,.4,.6].forEach(v=>{g+=`<line x1="${l}" x2="${W-r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${l-6}" y="${y(v)+4}" text-anchor="end">${v*100}%</text>`});
  keys.forEach((k,i)=>{ if(k.endsWith('Q1')) g+=`<text x="${x(i)}" y="${H-8}" text-anchor="middle">${k.slice(0,4)}</text>`; if(f.year && k.startsWith(f.year)) g+=`<rect x="${x(i)-4}" y="${t}" width="8" height="${H-t-b}" fill="var(--steel-2)" opacity=".25"/>`; });
  g+=`<path d="${path(win)}" fill="none" stroke="var(--steel)" stroke-width="2.2"/><path d="${path(dis)}" fill="none" stroke="var(--ember)" stroke-width="2"/>`;
  $('#trend').innerHTML = svg(W,H,g);
}

function priceTest(f){
  const lines = ['spare_parts','field_service'].filter(l=>!f.line||l===f.line);
  const W=560,H=200,l=40,r=10,t=14,b=30; let g='';
  const x=c=>l+(c+0.07)/0.14*(W-l-r), y=v=>t+(1-(v-0.3)/0.4)*(H-t-b);
  [.3,.4,.5,.6,.7].forEach(v=>{g+=`<line x1="${l}" x2="${W-r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--line)"/><text x="${l-6}" y="${y(v)+4}" text-anchor="end">${Math.round(v*100)}%</text>`});
  [-.06,-.03,0,.03,.06].forEach(c=>g+=`<text x="${x(c)}" y="${H-10}" text-anchor="middle">${c>0?'+':''}${Math.round(c*100)}% price</text>`);
  lines.forEach((ln,i)=>{ const pts=D.price_test.filter(p=>p.service_line===ln); const col=i?'var(--ember)':'var(--steel)';
    g+=`<path d="${pts.map((p,j)=>`${j?'L':'M'}${x(p.cell)},${y(p.win_rate)}`).join('')}" fill="none" stroke="${col}" stroke-width="2"/>`;
    pts.forEach(p=>g+=`<circle cx="${x(p.cell)}" cy="${y(p.win_rate)}" r="4" fill="${col}"><title>${LINES[ln]} ${p.cell>0?'+':''}${p.cell*100}%: ${pct(p.win_rate)} of ${p.quotes} quotes</title></circle>`);
    g+=`<text x="${W-r}" y="${t+12+i*14}" text-anchor="end" style="fill:${col}">${LINES[ln]}</text>`; });
  $('#pricetest').innerHTML = svg(W,H,g);
  const bl = D.discount_bands.filter(p=>!f.line||p.service_line===f.line);
  const ln = f.line || 'spare_parts'; const pts = bl.filter(p=>p.service_line===ln);
  $('#bands').innerHTML = `<p class="note" style="margin-top:10px">${LINES[ln]}, everyday quotes, win rate by discount: ` + pts.map(p=>`${p.band}% off: ${pct(p.win_rate,0)}`).join(' · ') + '</p>';
}

function reps(f){
  const rs = D.reps.filter(r=>!f.region||r.region===f.region).slice(0,12);
  $('#reps').innerHTML = `<tr><th>Rep</th><th>Region</th><th class="n">Invoices</th><th class="n">Excess vs peers</th><th class="n">z</th><th class="n">Excess, €</th></tr>` +
    rs.map(r=>`<tr class="${r.flagged?'flag':''}"><td>${r.rep_name}</td><td>${REG[r.region]}</td><td class="n">${r.invoices}</td><td class="n">${pct(r.excess_rate,2)}</td><td class="n">${r.z.toFixed(1)}</td><td class="n">${eur(r.excess_eur)}</td></tr>`).join('');
  $('#callouts').innerHTML = `<div class="callout"><b>${eur(D.billing.eur)}</b><span>${D.billing.count} invoices billed below the approved quote</span></div><div class="callout"><b>${eur(D.rebates.eur)}</b><span>rebates paid above contract, ${D.rebates.customers.length} customers</span></div>`;
}

function forecast(f){
  const rs = D.forecast.filter(r=>!f.line||r.service_line===f.line); const by={};
  rs.forEach(r=>{ const o=(by[r.target]=by[r.target]||{forecast:0,actual:0,lo:0,hi:0}); for(const k of ['forecast','actual','lo','hi']) o[k]+=r[k]; });
  const ks=Object.keys(by).sort(); if(!ks.length){$('#forecast').innerHTML='';return;}
  const W=1100,H=230,l=60,r=34,t=10,b=26; const mx=Math.max(...ks.map(k=>Math.max(by[k].hi,by[k].actual)))*1.05;
  const x=i=>l+i*(W-l-r)/Math.max(1,ks.length-1), y=v=>t+(1-v/mx)*(H-t-b);
  let g=''; [0,.25,.5,.75,1].forEach(v=>{g+=`<line x1="${l}" x2="${W-r}" y1="${y(v*mx)}" y2="${y(v*mx)}" stroke="var(--line)"/><text x="${l-6}" y="${y(v*mx)+4}" text-anchor="end">${eur(v*mx)}</text>`});
  g+=`<path d="${ks.map((k,i)=>`${i?'L':'M'}${x(i)},${y(by[k].hi)}`).join('')} ${ks.slice().reverse().map((k,i)=>`L${x(ks.length-1-i)},${y(by[k].lo)}`).join('')} Z" fill="var(--steel-2)" opacity=".35"/>`;
  g+=`<path d="${ks.map((k,i)=>`${i?'L':'M'}${x(i)},${y(by[k].actual)}`).join('')}" fill="none" stroke="var(--ink)" stroke-width="2.2"/>`;
  g+=`<path d="${ks.map((k,i)=>`${i?'L':'M'}${x(i)},${y(by[k].forecast)}`).join('')}" fill="none" stroke="var(--steel)" stroke-width="2" stroke-dasharray="5 4"/>`;
  ks.forEach((k,i)=>g+=`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${k}</text>`);
  $('#forecast').innerHTML = svg(W,H,g) + '<div class="legend"><span><i style="background:var(--ink)"></i>booked</span><span><i style="background:var(--steel)"></i>forecast</span><span><i style="background:var(--steel-2)"></i>80% interval</span></div>';
}

function openQuotes(f){
  const rs = D.open.filter(r=>(!f.region||r.region===f.region)&&(!f.line||r.service_line===f.line));
  const rec = rs.filter(r=>r.status==='recommended'), gain = rec.reduce((a,r)=>a+r.gain_eur,0);
  $('#recnote').textContent = `${rs.length} open quotes; ${rec.length} have a recommendation, worth ${eur(gain)} of expected margin. Others keep the rep's price until a price test validates their service line, or go to the deal desk.`;
  const top = rs.slice().sort((a,b)=>b.gain_eur-a.gain_eur).slice(0,60);
  $('#open').innerHTML = `<tr><th>Quote</th><th>Customer</th><th>Line</th><th>Status</th><th class="n">Rep's price</th><th class="n">Recommended</th><th class="n">Range</th><th class="n">Win chance</th><th class="n">Expected gain</th><th>Why</th></tr>` +
    top.map(r=>`<tr><td>${r.quote_id}</td><td>${r.customer}</td><td>${LINES[r.service_line]}</td><td><span class="pill ${r.status==='recommended'?'rec':'hold'}">${r.status.replace('_',' ')}</span></td><td class="n">${eur(r.current_eur)}</td><td class="n">${eur(r.recommended_eur)}</td><td class="n">${eur(r.low_eur)} – ${eur(r.high_eur)}</td><td class="n">${pct(r.p_win_current,0)} → ${pct(r.p_win_recommended,0)}</td><td class="n">${eur(r.gain_eur)}</td><td>${r.reason}</td></tr>`).join('');
}

function render(){
  const f = sel(); try { localStorage.setItem('bpi-filters', JSON.stringify(f)); } catch(e) {}
  const rs = rows(f); kpis(rs); waterfall(rs); trend(f); priceTest(f); reps(f); forecast(f); openQuotes(f);
}
render();
</script>
<style>.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}</style>
</body>
</html>
"""
