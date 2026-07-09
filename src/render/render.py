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
    var series=m.series.map(function(s,i){
      var color = s.role==='benchmark' ? '#A66C3D' : (s.role==='avos'||s.role==='nowcast' ? '#4F5C41' : P[i%%P.length]);
      var dashed = s.role==='nowcast' || !!s.estimated_from;   /* §A9.5: estimates dashed */
      var u=s.unit||fallbackUnit;
      var ax=Math.min(units.indexOf(u),1);                      /* extras share the left axis */
      if(axisColor[ax]===undefined) axisColor[ax]=color;
      var pts=(s.points||[]);
      var isBar = s.kind==='bar';
      return {name:s.name,type:isBar?'bar':'line',barWidth:isBar?'60%%':undefined,
              showSymbol:!isBar&&pts.length<40,symbolSize:5,yAxisIndex:dual?ax:0,
              lineStyle:{width:2,color:color,type:dashed?'dashed':'solid'},
              itemStyle:{color:color,opacity:s.role==='nowcast'?0.55:1},
              data:(s.points||[]).map(function(p){return [p.date,p.value];})};
    });
    var yAxes;
    if(dual){
      yAxes=[
        {type:'value',scale:true,position:'left',name:units[0],
         nameTextStyle:{color:axisColor[0]||'#5A5A5A',align:'left'},
         axisLabel:{color:axisColor[0]||'#5A5A5A'},
         splitLine:{lineStyle:{color:'#e2e2e2'}}},
        {type:'value',scale:true,position:'right',name:units[1],
         nameTextStyle:{color:axisColor[1]||'#5A5A5A',align:'right'},
         axisLabel:{color:axisColor[1]||'#5A5A5A'},
         splitLine:{show:false}}
      ];
    } else {
      yAxes={type:'value',scale:true,name:units[0]||fallbackUnit,
             nameTextStyle:{color:'#5A5A5A',align:'left'},
             splitLine:{lineStyle:{color:'#e2e2e2'}}};
    }
    ch.setOption({grid:{left:56,right:dual?56:16,top:26,bottom:24},
      xAxis:{type:'time',axisLine:{lineStyle:{color:'#1E1E1E'}}},
      yAxis:yAxes,
      tooltip:{trigger:'axis'},
      dataZoom:[{type:'inside',xAxisIndex:0,filterMode:'filter'}],  /* y re-fits to visible window */
      textStyle:{fontFamily:'Roboto,system-ui,sans-serif'},
      series:series});
    var maxX=0; m.series.forEach(function(s){(s.points||[]).forEach(function(p){
      var t=+new Date(p.date); if(t>maxX) maxX=t;});});
    window.__CHARTS__.push({ch:ch,maxX:maxX});
    window.addEventListener('resize',function(){ch.resize();});
  });
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
    """Embed the avos wordmark from style_guides/ if reachable (§A9)."""
    candidates = [
        os.path.join(os.path.dirname(BASE), "equities-pm", "style_guides", "LOGOTYPE-1.png"),
        os.path.join(BASE, "style_guides", "LOGOTYPE-1.png"),
    ]
    for p in candidates:
        if os.path.exists(p):
            b = base64.b64encode(open(p, "rb").read()).decode()
            return f"data:image/png;base64,{b}"
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
            if not d or not d.get("series"):
                continue
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
            charts.append({
                "id": m.id, "name": m.name, "source": d.get("source", m.source),
                "asof": asof, "stale": stale,
                "value_fmt": _fmt_value(tile.get("value"), unit),
                "percentile": (round(tile["percentile"]) if tile.get("percentile")
                               is not None else None),
                "notes": d.get("notes", ""),
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
    return latest


def _source_status_line() -> str:
    if not os.path.exists(store.RUN_LOG):
        return "no run log yet"
    lines = open(store.RUN_LOG, encoding="utf-8").read().strip().splitlines()
    recent = [json.loads(x) for x in lines[-12:]] if lines else []
    return " · ".join(f"{r['source']}:{r['status']}" for r in recent) or "—"


if __name__ == "__main__":
    print("Wrote", build())
