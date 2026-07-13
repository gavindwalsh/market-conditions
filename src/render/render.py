"""render.py — display layer → single self-contained HTML (§6).

Reads ONLY build_data/*.json (via store.load_all_display) so the page is a pure
function of the display layer (deterministic, offline). Inlines house CSS, the
data JSON, the vendored ECharts (if present), and a chart-boot script that
builds house-styled series from the embedded data.
"""
from __future__ import annotations

import base64
import json
import os
from datetime import date, datetime

from jinja2 import Template  # autoescape enabled at render (see build())

from .. import config, store, util

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(HERE))
OUT_DIR = os.path.join(BASE, "outputs", "dashboard")
STYLE_GUIDES = os.path.join(
    os.path.dirname(BASE), "..",  # sibling lookups if needed
)

SERIES_PALETTE = ["#7F77B2", "#A66C3D", "#4F5C41", "#A6B296", "#9AA3C2", "#637F8F"]

GLOSSARY = [
    ("0DTE", "same-day-expiry option"), ("OAS", "option-adjusted spread"),
    ("GEX", "dealer gamma exposure"), ("DTE", "days to expiry"),
    ("VRP", "variance risk premium"), ("MOC", "market-on-close"),
    ("TRF", "FINRA Trade Reporting Facility"), ("NBBO", "national best bid/offer"),
    ("DFA", "Fed Distributional Financial Accounts"),
    ("PMMS", "Freddie Mac Primary Mortgage Market Survey"),
    ("HHDC", "NY Fed Household Debt & Credit report"), ("HHI", "Herfindahl-Hirschman index"),
]

CHART_BOOT = """
(function(){
  if(!window.echarts) return;
  var P=%s;
  var data=window.__PULSE__.metrics||{};
  window.__CHARTS__=[];
  Object.keys(data).forEach(function(id){
    var m=data[id]; if(!m.series||!m.series.length) return;
    var el=document.getElementById('chart-'+id); if(!el) return;
    var ch=echarts.init(el,null,{renderer:'canvas'});
    var fallbackUnit=(m.unit||'').replace(/\\s*\\(tile:[^)]*\\)/,'').trim();
    /* distinct per-series units, in first-seen order → up to two y-axes */
    var units=[]; m.series.forEach(function(s){
      var u=s.unit||fallbackUnit;
      if(units.indexOf(u)<0) units.push(u);
    });
    var dual = units.length>1;
    var axisColor={};   /* unit → color of first series on that axis */
    /* two-pass color assignment: role colors are reserved up front, then each
       non-role series takes the next UNUSED palette color — no more duplicate
       lines (role colors #A66C3D/#4F5C41 are palette members, so index-based
       lookup collided on 14 of 33 multi-series charts). */
    var used={};
    m.series.forEach(function(s){
      if(s.role==='benchmark') used['#A66C3D']=1;
      else if(s.role==='avos'||s.role==='nowcast') used['#4F5C41']=1;
    });
    var nextColor=function(i){
      for(var k=0;k<P.length;k++){ if(!used[P[k]]){used[P[k]]=1;return P[k];} }
      return P[i%%P.length];  /* >6 distinct series: wrap */
    };
    var series=m.series.map(function(s,i){
      var color = s.role==='benchmark' ? '#A66C3D' : (s.role==='avos'||s.role==='nowcast' ? '#4F5C41' : nextColor(i));
      var dashed = s.role==='nowcast' || !!s.estimated_from;   /* §A9.5: estimates dashed */
      var u=s.unit||fallbackUnit;
      var ax=Math.min(units.indexOf(u),1);                      /* extras share the left axis */
      if(axisColor[ax]===undefined) axisColor[ax]=color;
      var pts=(s.points||[]);
      var isBar = s.kind==='bar';
      return {name:s.name,type:isBar?'bar':'line',
              stack:isBar&&s.stack?'total':undefined,
              barMinWidth:isBar?2:undefined,barMaxWidth:isBar?26:undefined,
              showSymbol:!isBar&&pts.length<40,symbolSize:5,yAxisIndex:dual?ax:0,
              lineStyle:{width:2,color:color,type:dashed?'dashed':'solid'},
              itemStyle:{color:color,opacity:s.role==='nowcast'?0.55:1},
              data:(s.points||[]).map(function(p){return [p.date,p.value];})};
    });
    /* legend so multi-series charts are readable without the tooltip */
    var legend = m.series.length>1 ? {top:0,left:2,itemWidth:12,itemHeight:7,
        itemGap:8,textStyle:{fontSize:10,color:'#5A5A5A'},icon:'roundRect',
        data:m.series.map(function(s){return s.name;})} : undefined;
    /* optional axis clamps (points beyond run off-chart) — e.g. hide COVID spikes */
    var yMax = (m.y_max===undefined?null:m.y_max), yMin = (m.y_min===undefined?null:m.y_min);
    var yAxes;
    if(dual){
      yAxes=[
        {type:'value',scale:true,position:'left',name:units[0],max:yMax,min:yMin,
         nameTextStyle:{color:axisColor[0]||'#5A5A5A',align:'left'},
         axisLabel:{color:axisColor[0]||'#5A5A5A'},
         splitLine:{lineStyle:{color:'#e2e2e2'}}},
        {type:'value',scale:true,position:'right',name:units[1],
         nameTextStyle:{color:axisColor[1]||'#5A5A5A',align:'right'},
         axisLabel:{color:axisColor[1]||'#5A5A5A'},
         splitLine:{show:false}}
      ];
    } else {
      yAxes={type:'value',scale:true,name:units[0]||fallbackUnit,max:yMax,min:yMin,
             nameTextStyle:{color:'#5A5A5A',align:'left'},
             splitLine:{lineStyle:{color:'#e2e2e2'}}};
    }
    ch.setOption({grid:{left:56,right:dual?56:16,top:legend?40:26,bottom:24},
      legend:legend,
      xAxis:{type:'time',axisLine:{lineStyle:{color:'#1E1E1E'}}},
      yAxis:yAxes,
      tooltip:{trigger:'axis'},
      dataZoom:[{type:'inside',xAxisIndex:0,filterMode:'filter'}],  /* y re-fits to visible window */
      textStyle:{fontFamily:'Roboto,system-ui,sans-serif'},
      series:series});
    var maxX=0; m.series.forEach(function(s){(s.points||[]).forEach(function(p){
      var t=+new Date(p.date); if(t>maxX) maxX=t;});});
    window.__CHARTS__.push({ch:ch,maxX:maxX,id:id,el:el});
    window.addEventListener('resize',function(){ch.resize();});
  });
  var chartById=function(id){var f=null;window.__CHARTS__.forEach(function(c){if(c.id===id)f=c.ch;});return f;};
  /* header range buttons: months back from each chart's own latest point; null = All */
  window.setRange=function(months,btn){
    document.querySelectorAll('.range-btns button').forEach(function(b){b.classList.remove('active');});
    if(btn) btn.classList.add('active');
    window.__CHARTS__.forEach(function(c){
      if(months===null){
        c.ch.dispatchAction({type:'dataZoom',start:0,end:100});
      } else {
        c.ch.dispatchAction({type:'dataZoom',
          startValue:c.maxX-months*30.44*86400000,endValue:c.maxX});
      }
    });
  };
  /* fullscreen overlay: move the chosen chart's element into the shared panel
     and resize the (same) instance; move it back on close. Esc / scrim / ✕ close. */
  var ov=document.getElementById('fs-overlay');
  var body=document.getElementById('fs-body');
  var titleEl=ov?ov.querySelector('.fs-title'):null;
  var srcEl=ov?ov.querySelector('.fs-src'):null;
  var fsState=null;
  var fsOpen=function(id){
    var el=document.getElementById('chart-'+id); if(!el||!ov) return;
    var card=el.closest('.chart-card');
    var m=data[id]||{};
    titleEl.textContent=id+(m.name?' · '+m.name:'');
    var csrc=card?card.querySelector('.csrc'):null;
    srcEl.innerHTML=csrc?csrc.innerHTML:'';
    fsState={el:el,parent:el.parentNode,next:el.nextSibling};
    body.appendChild(el);
    ov.hidden=false;
    /* mirror the header's active range button into the overlay row */
    var hb=document.querySelectorAll('.masthead .range-btns button');
    var ob=ov.querySelectorAll('.fs-range button');
    hb.forEach(function(b,i){if(ob[i])ob[i].classList.toggle('active',b.classList.contains('active'));});
    var ch=chartById(id);
    if(ch){ch.resize();requestAnimationFrame(function(){ch.resize();});}
  };
  var fsClose=function(){
    if(!fsState||!ov) return;
    /* setRange highlights the clicked (overlay) button, clearing the header's —
       mirror the overlay's active range back so the header stays truthful */
    var ob=ov.querySelectorAll('.fs-range button');
    var hb=document.querySelectorAll('.masthead .range-btns button');
    ob.forEach(function(b,i){if(hb[i])hb[i].classList.toggle('active',b.classList.contains('active'));});
    if(fsState.next) fsState.parent.insertBefore(fsState.el,fsState.next);
    else fsState.parent.appendChild(fsState.el);
    ov.hidden=true;
    var ch=chartById(fsState.el.id.replace('chart-',''));
    fsState=null;
    if(ch){ch.resize();requestAnimationFrame(function(){ch.resize();});}
  };
  document.querySelectorAll('.expand-icon').forEach(function(ic){
    ic.addEventListener('click',function(){fsOpen(ic.getAttribute('data-chart'));});
    ic.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();fsOpen(ic.getAttribute('data-chart'));}});
  });
  if(ov){
    ov.querySelector('.fs-close').addEventListener('click',fsClose);
    ov.addEventListener('click',function(e){if(e.target===ov)fsClose();});
  }
  document.addEventListener('keydown',function(e){if(e.key==='Escape'&&ov&&!ov.hidden)fsClose();});
})();
""" % json.dumps(SERIES_PALETTE)


def _clean_unit(unit: str) -> str:
    """' bp (tile: HY OAS)' → 'bp' — the tile hints died with the tiles."""
    import re
    return re.sub(r"\s*\(tile:[^)]*\)", "", unit).strip()


def _fmt_value(v, unit=""):
    if v is None:
        return "—"
    if isinstance(v, (int, float)):
        s = f"{v:,.2f}".rstrip("0").rstrip(".")
        return f"{s}{unit}"
    return str(v)


def _wordmark_data_uri():
    """Embed the avos wordmark (§A9). The white knockout SVG is VENDORED in-repo
    so the logo is self-contained and identical in every environment (worktree,
    main checkout, deploy). Previously this reached into a sibling repo for
    LOGOTYPE-1.png — a dark-on-WHITE asset that, on the dark header, rendered as a
    white box with a tiny faint wordmark (and only when that sibling was present,
    so it differed between local and deploy). Do not reintroduce the PNG here."""
    p = os.path.join(HERE, "vendor", "avos-wordmark.svg")
    if os.path.exists(p):
        b = base64.b64encode(open(p, "rb").read()).decode()
        return f"data:image/svg+xml;base64,{b}"
    return None


def _vendored_echarts():
    p = os.path.join(HERE, "vendor", "echarts.min.js")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else None


def build(run_date: str = None, build_version: str = "dev", lead: str = None) -> str:
    run_date = run_date or date.today().isoformat()
    display = store.load_all_display()
    css = open(os.path.join(HERE, "house.css"), encoding="utf-8").read()

    panels, embed = [], {}
    for key, title in config.PANELS:
        charts = []
        for m in config.metrics_for_panel(key):
            d = display.get(m.id)
            if not d or not (d.get("series") or d.get("table")):
                continue
            if d.get("series"):
                embed[m.id] = d
            tile = d.get("tile", {})
            stale = False
            asof = d.get("asof", "—")
            try:
                s = util.classify_staleness(date.fromisoformat(asof), m.cadence)
                stale = s.level == "stale"
            except Exception:
                pass
            unit = _clean_unit(d.get("unit", ""))
            status = d.get("status") or {}
            charts.append({
                # display JSON name wins (the compute's honest name) — registry
                # names had drifted on RF1/IS7; registry stays the fallback
                "id": m.id, "name": d.get("name") or m.name,
                "source": d.get("source", m.source),
                "asof": asof, "stale": stale,
                "value_fmt": _fmt_value(tile.get("value"), unit),
                "percentile": (round(tile["percentile"]) if tile.get("percentile")
                               is not None else None),
                # hover = one-sentence reading hint; long methodology lives in
                # APPENDIX.md (§25) — notes kept as fallback for unconverted metrics
                "tip": d.get("tooltip") or d.get("notes", ""),
                "badge": status.get("level"), "badge_label": status.get("label"),
                "table": d.get("table"),
            })
        if charts:
            panels.append({"title": title, "charts": charts})

    tmpl = Template(open(os.path.join(HERE, "template.html.j2"), encoding="utf-8").read(),
                    autoescape=True)
    html = tmpl.render(
        css=css, run_date=run_date, phase=config.PHASE,
        lead=lead, panels=panels,
        glossary=[{"term": t, "defn": d} for t, d in GLOSSARY],
        build_version=build_version, run_ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        size_budget=config.SIZE_BUDGET_MB,
        source_status=_source_status_line(),
        wordmark_data_uri=_wordmark_data_uri(),
        data_json=json.dumps({"metrics": embed}, ensure_ascii=False),
        echarts_js=_vendored_echarts(), chart_boot=CHART_BOOT,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    dated = os.path.join(OUT_DIR, f"pulse_{run_date}.html")
    latest = os.path.join(OUT_DIR, "pulse_latest.html")
    for path in (dated, latest):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, path)
    _write_appendix(display)  # §25: methodology appendix, single-sourced
    return latest


APPENDIX_PREAMBLE = """\
# Market Pulse — metric appendix

Generated by the render step from the display layer (`build_data/*.json`) —
edit methodology text in the emitting compute module, not here. Tooltips on the
page carry the one-sentence reading hint; this file carries the mechanics.

## Shared methodology

### Retail identification & scaling (§5.1)
The classifier identifies retail trades as off-exchange (TRF) prints with
subpenny price improvement (BHJOS, JF 2024) and signs them against the
prevailing NBBO midpoint (above mid = buy; at-mid excluded). This identifies
roughly 1/3 of retail activity, so DOLLAR-denominated retail metrics are scaled
×3.0 to estimated totals — a provisional factor until the RF9 calibration vs
Nasdaq RTAT fits it empirically (gate: correlation ≥ 0.6, §7.2). Ratios
(concentration, buy-the-dip, trade size) are scale-invariant and unscaled.

### Small-lot options proxy (§5.2)
Trades under 10 contracts proxy retail options activity — an observed
regularity, not identification. Reconciles to published totals (Citadel ch.11).

### FINRA participation anchor (RF2)
FINRA weekly per-firm non-ATS volume (T1+T2 tiers only; OTCE excluded — it has
no counterpart in our NMS tape denominator) counts BOTH sides of internalized
trades, so its level ≈ 2× true share. It is rescaled onto our participation
definition with k = mean(ours/FINRA) over overlap weeks and rendered as the
official trend anchor; our estimate extends FINRA's 2–4 week publication lag.

### ETF flow universe
Curated ~53-fund universe (top of complex, §A3 coverage label) — not all US
ETFs. Flows via the shares-outstanding method: flow = Δshares × NAV.

### Realized vol conventions
Realized-vol legs follow the Bloomberg convention (trading-day window, log
returns, √260 annualization) so chart values tie to Terminal fields; "1M
realized" = 21 sessions, tenor-matched to 30-day implied.

## Per-metric notes
"""


def _write_appendix(display: dict) -> str:
    """APPENDIX.md — per-metric methodology, generated from the display layer."""
    lines = [APPENDIX_PREAMBLE]
    for key, _title in config.PANELS:
        for m in config.metrics_for_panel(key):
            d = display.get(m.id)
            if not d:
                continue
            lines.append(f"### {m.id} — {d.get('name') or m.name}")
            status = d.get("status") or {}
            if status:
                lines.append(f"**Status: {status.get('label') or status.get('level')}**")
            meta = f"*Source: {d.get('source', m.source)} · cadence: {m.cadence}*"
            lines.append(meta)
            if d.get("notes"):
                lines.append(d["notes"])
            lines.append("")
    path = os.path.join(BASE, "APPENDIX.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _source_status_line() -> str:
    if not os.path.exists(store.RUN_LOG):
        return "no run log yet"
    lines = open(store.RUN_LOG, encoding="utf-8").read().strip().splitlines()
    recent = [json.loads(x) for x in lines[-12:]] if lines else []
    return " · ".join(f"{r['source']}:{r['status']}" for r in recent) or "—"


if __name__ == "__main__":
    print("Wrote", build())
