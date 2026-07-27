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
      /* axis unit names dropped from the chart (§ user: mostly self-evident);
         units now live in the title tooltip. axisLabel colors still tie each
         numeric scale to its series on dual-axis charts. */
      yAxes=[
        {type:'value',scale:true,position:'left',max:yMax,min:yMin,
         axisLabel:{color:axisColor[0]||'#5A5A5A'},
         splitLine:{lineStyle:{color:'#e2e2e2'}}},
        {type:'value',scale:true,position:'right',
         axisLabel:{color:axisColor[1]||'#5A5A5A'},
         splitLine:{show:false}}
      ];
    } else {
      yAxes={type:'value',scale:true,max:yMax,min:yMin,
             splitLine:{lineStyle:{color:'#e2e2e2'}}};
    }
    /* category (not time) x-axis: market data has no weekend/holiday points, so a
       time axis leaves blank slots for every non-trading day (visible as bar gaps
       and warped spacing). Category spaces one slot per trading day present. Build
       the sorted union of dates across all series so multi-series charts share one
       axis; series data stays [date,value] pairs, which ECharts maps by category. */
    var catSet={}; m.series.forEach(function(s){(s.points||[]).forEach(function(p){catSet[p.date]=1;});});
    var cats=Object.keys(catSet).sort();
    ch.setOption({grid:{left:56,right:dual?56:16,top:legend?40:26,bottom:24},
      legend:legend,
      xAxis:{type:'category',data:cats,axisLine:{lineStyle:{color:'#1E1E1E'}}},
      yAxis:yAxes,
      tooltip:{trigger:'axis',confine:true},
      dataZoom:[{type:'inside',xAxisIndex:0,filterMode:'filter'}],  /* y re-fits to visible window */
      textStyle:{fontFamily:'Roboto,system-ui,sans-serif'},
      series:series});
    window.__CHARTS__.push({ch:ch,cats:cats,id:id,el:el});
    window.addEventListener('resize',function(){ch.resize();});
  });
  var chartById=function(id){var f=null;window.__CHARTS__.forEach(function(c){if(c.id===id)f=c.ch;});return f;};
  /* header range buttons: months back from each chart's own latest point; null = All.
     On a category axis dataZoom bounds are indices, so translate "N months back"
     into the first trading-day slot at/after that calendar cutoff.

     The window is per-chart, measured from that chart's OWN last point, so a
     series shorter than the requested window simply shows all of itself:
     cats[0] is already at/after the cutoff, startIdx stays 0. That is what makes
     a 10Y default safe across a panel mixing 1947-> and 2023-> histories. */
  window.setRange=function(months,btn){
    document.querySelectorAll('.range-btns button').forEach(function(b){b.classList.remove('active');});
    if(btn) btn.classList.add('active');
    window.__CHARTS__.forEach(function(c){
      var cats=c.cats||[];
      if(months===null||!cats.length){
        c.ch.dispatchAction({type:'dataZoom',start:0,end:100});
      } else {
        var cutoff=+new Date(cats[cats.length-1])-months*30.44*86400000;
        var startIdx=0;
        for(var i=0;i<cats.length;i++){ if(+new Date(cats[i])>=cutoff){startIdx=i;break;} }
        c.ch.dispatchAction({type:'dataZoom',startValue:startIdx,endValue:cats.length-1});
      }
    });
  };
  /* Apply the default range on load. The template marks the 10Y button active for
     the pre-JS paint, but the charts themselves boot at full range, so without
     this call the highlighted button would lie about what is on screen. Pass the
     button element: setRange clears every .active first, so calling it without one
     would leave no range highlighted at all. */
  var DEFAULT_RANGE_MONTHS=120;   /* 10Y — keep in step with the template's active button */
  window.setRange(DEFAULT_RANGE_MONTHS,
                  document.querySelector('.masthead .range-btns button.active'));
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


IPO_TRACKER_BOOT = r"""
(function(){
  var P=window.__PULSE__||{}; var D=P.ipo; if(!D) return;
  if(!document.getElementById('ipo-tracker')) return;
  var rows=D.rows||[]; var stageOrder=D.stage_order||[];
  var filt={vehicle:'Operating Co',sector:'all',status:'all',from:'',to:''};
  var sortP={col:'Raise ($mm)',dir:-1};      /* priced default: largest raise first */
  var sortL={col:'Tier',dir:1};  /* pipeline default: by confidence tier A→D */

  function esc(s){return (s==null?'':String(s)).replace(/[&<>"]/g,function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
  function money(v){ if(v==null) return '—'; var a=Math.abs(v);
    return a>=1000 ? '$'+(v/1000).toFixed(1)+'B' : '$'+v.toFixed(0)+'mm'; }
  function pct(v){ if(v==null) return '—'; return (v>=0?'+':'')+(v*100).toFixed(1)+'%'; }
  function uniq(key){ var s={}; rows.forEach(function(r){ if(r[key]) s[r[key]]=1; });
    return Object.keys(s).sort(); }

  function passes(r){
    if(filt.status!=='all' && r.Status!==filt.status) return false;
    if(filt.vehicle==='__nospac__'){ if(r['Vehicle Type']==='SPAC') return false; }
    else if(filt.vehicle!=='all' && r['Vehicle Type']!==filt.vehicle) return false;
    if(filt.sector!=='all' && r.Sector!==filt.sector) return false;
    var kd=r['Key Date'];  /* lenient: rows without a date pass the date filter */
    if(filt.from && kd && kd<filt.from) return false;
    if(filt.to && kd && kd>filt.to) return false;
    return true;
  }
  function currentFiltered(){ return rows.filter(passes); }

  function buildFilters(){
    var vehicles=uniq('Vehicle Type'), sectors=uniq('Sector');
    var f=document.getElementById('ipo-filters');
    var secOpts=['<option value="all">All sectors</option>'].concat(sectors.map(function(v){
      return '<option'+(filt.sector===v?' selected':'')+'>'+esc(v)+'</option>';})).join('');
    var vehOpts='<option value="all">All vehicles</option><option value="__nospac__">Exclude SPACs</option>'+
      vehicles.map(function(v){return '<option'+(filt.vehicle===v?' selected':'')+'>'+esc(v)+'</option>';}).join('');
    f.innerHTML=
      '<label>Vehicle<select id="f-veh">'+vehOpts+'</select></label>'+
      '<label>Sector<select id="f-sec">'+secOpts+'</select></label>'+
      '<label>Status<select id="f-stat"><option value="all">All</option>'+
        '<option>Priced</option><option>Pipeline</option><option>Withdrawn</option></select></label>'+
      '<label>Priced from<input type="date" id="f-from" value="'+filt.from+'"></label>'+
      '<label>to<input type="date" id="f-to" value="'+filt.to+'"></label>'+
      '<button id="f-reset" type="button">Reset</button>';
    f.querySelector('#f-veh').value=filt.vehicle; f.querySelector('#f-stat').value=filt.status;
    var on=function(id,k){ f.querySelector(id).onchange=function(e){filt[k]=e.target.value;render();}; };
    on('#f-veh','vehicle'); on('#f-sec','sector'); on('#f-stat','status');
    on('#f-from','from'); on('#f-to','to');
    f.querySelector('#f-reset').onclick=function(){
      filt={vehicle:'Operating Co',sector:'all',status:'all',from:'',to:''}; buildFilters(); render(); };
  }

  /* ---- KPI strip ---- */
  function median(a){ if(!a.length) return null; a=a.slice().sort(function(x,y){return x-y;});
    var m=Math.floor(a.length/2); return a.length%2?a[m]:(a[m-1]+a[m])/2; }
  function wret(list,key){ /* raise-weighted (value-weighted) aggregate return */
    var num=0,den=0; list.forEach(function(r){ var v=r[key],w=r['Raise ($mm)'];
      if(v!=null&&w!=null){num+=w*v;den+=w;} }); return den? num/den : null; }
  function renderKPIs(fr){
    var priced=fr.filter(function(r){return r.Status==='Priced';});
    var pipe=fr.filter(function(r){return r.Status==='Pipeline';});
    var op=priced.filter(function(r){return r['Vehicle Type']==='Operating Co';});
    var spac=priced.filter(function(r){return r['Vehicle Type']==='SPAC';});
    var gross=priced.reduce(function(s,r){return s+(r['Raise ($mm)']||0);},0)/1000;
    var med=median(op.map(function(r){return r['Since Offer (%)'];}).filter(function(v){return v!=null;}));
    var trOff=wret(op,'Since Offer (%)'), trOpn=wret(op,'Since Open (%)');
    var cards=[
      ['YTD priced', priced.length, op.length+' operating · '+spac.length+' SPAC'],
      ['Gross proceeds', '$'+gross.toFixed(1)+'B', 'sum of raises, priced'],
      ['Median since-offer', med==null?'—':pct(med), 'priced operating cos'],
      ['Total return since offer', trOff==null?'—':pct(trOff), 'raise-weighted · priced op cos'],
      ['Total return since open', trOpn==null?'—':pct(trOpn), 'from first-day open · raise-weighted'],
      ['Pipeline', pipe.length, 'names tracked forward'],
    ];
    document.getElementById('ipo-kpis').innerHTML=cards.map(function(c){
      return '<div class="ipo-kpi"><div class="k-lab">'+esc(c[0])+'</div>'+
        '<div class="k-val">'+esc(c[1])+'</div><div class="k-sub">'+esc(c[2])+'</div></div>';}).join('');
  }

  /* ---- generic sortable, collapsible table ---- */
  function fmtCell(r,c){ var v=c.get?c.get(r):r[c.key];
    if(c.badge) return v?'<span class="tier tier-'+esc(v)+'">'+esc(v)+'</span>':'';
    if(c.money) return money(v);
    if(c.pct) return pct(v);
    return esc(v==null?(c.dash||'—'):v); }
  function sortVal(r,c){ return c.sortVal?c.sortVal(r):(c.get?c.get(r):r[c.key]); }
  function tdCls(r,c){ var cl=[]; if(c.num)cl.push('num'); if(c.cell)cl.push(c.cell);
    if(c.color){ var v=r[c.key]; if(v!=null)cl.push(v>=0?'pos':'neg'); } return cl.join(' '); }
  function makeTable(mount, cols, ss, data, titleFn, openDefault){
    mount.innerHTML='<details class="ipo-tbl"'+(openDefault===false?'':' open')+
      '><summary></summary><div class="ipo-twrap"></div></details>';
    var sum=mount.querySelector('summary'), host=mount.querySelector('.ipo-twrap');
    function draw(){
      var sd=cols.filter(function(c){return c.key===ss.col;})[0]||cols[0];
      var d=data.slice().sort(function(a,b){ var x=sortVal(a,sd),y=sortVal(b,sd);
        if(x==null&&y==null)return 0; if(x==null)return 1; if(y==null)return -1;
        if(x<y)return -ss.dir; if(x>y)return ss.dir; return 0; });
      sum.innerHTML=titleFn(data.length);
      var head='<tr>'+cols.map(function(c){ var ar=ss.col===c.key?(ss.dir>0?' ▲':' ▼'):'';
        return '<th data-col="'+esc(c.key)+'"'+(c.num?' class="num"':'')+'>'+esc(c.label)+ar+'</th>';}).join('')+'</tr>';
      var body=d.map(function(r){ return '<tr'+(r._tip?' title="'+esc(r._tip)+'"':'')+'>'+
        cols.map(function(c){ var cl=tdCls(r,c);
          return '<td'+(cl?' class="'+cl+'"':'')+'>'+fmtCell(r,c)+'</td>';}).join('')+'</tr>';}).join('');
      host.innerHTML='<table class="ipo-table"><thead>'+head+'</thead><tbody>'+
        (body||'<tr><td class="ipo-empty">No rows match the current filters.</td></tr>')+'</tbody></table>';
      host.querySelectorAll('th[data-col]').forEach(function(th){ th.onclick=function(){
        var k=th.getAttribute('data-col'); var cd=cols.filter(function(c){return c.key===k;})[0];
        if(ss.col===k) ss.dir=-ss.dir; else { ss.col=k; ss.dir=cd.num?-1:1; }
        draw(); }; });
    }
    draw();
  }

  var PCOLS=[
    {key:'Company',label:'Company',cell:'c-name'},
    {key:'Ticker',label:'Ticker'},
    {key:'Key Date',label:'Priced',num:true},
    {key:'Vehicle Type',label:'Vehicle'},
    {key:'Sector',label:'Sector',get:function(r){return r.Sector||'Unclassified';}},
    {key:'Raise ($mm)',label:'Raise',num:true,money:true},
    {key:'Valuation ($mm)',label:'Mkt cap',num:true,money:true},
    {key:'Since Offer (%)',label:'Since offer',num:true,pct:true,color:true},
    {key:'Since Open (%)',label:'Since open',num:true,pct:true,color:true}];
  var LCOLS=[
    {key:'Company',label:'Company',cell:'c-name'},
    {key:'Tier',label:'Tier',badge:true,cell:'tcenter'},
    {key:'Stage',label:'Stage',sortVal:function(r){var i=stageOrder.indexOf(r.Stage);return i<0?99:i;}},
    {key:'Sector',label:'Sector',get:function(r){return r.Sector||'Unclassified';}},
    {key:'Offer/Target Window',label:'Window'},
    {key:'Valuation ($mm)',label:'Last private val',num:true,money:true},
    {key:'Raise ($mm)',label:'Target ($, est.)',num:true,money:true}];

  function withTip(r){ r._tip=[r['Valuation Basis']?'Basis: '+r['Valuation Basis']:'',
    r.Source?'Source: '+r.Source:'', r['As Of']?'As of '+r['As Of']:''].filter(Boolean).join(' · '); return r; }

  function render(){
    var fr=currentFiltered();
    renderKPIs(fr);
    makeTable(document.getElementById('ipo-priced'), PCOLS, sortP,
      fr.filter(function(r){return r.Status==='Priced';}),
      function(n){return 'Priced · '+n+' companies';});
    makeTable(document.getElementById('ipo-pipeline'), LCOLS, sortL,
      fr.filter(function(r){return r.Status==='Pipeline';}).map(withTip),
      function(n){return 'Forward pipeline · '+n+' names';});
    var wd=fr.filter(function(r){return r.Status==='Withdrawn';}).map(withTip);
    var wdEl=document.getElementById('ipo-withdrawn');
    if(wd.length){ wdEl.style.display=''; makeTable(wdEl, LCOLS, {col:'Valuation ($mm)',dir:-1}, wd,
      function(n){return 'Withdrawn · '+n;}, false); }
    else { wdEl.style.display='none'; wdEl.innerHTML=''; }
  }
  buildFilters(); render();
})();
"""


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


def _md_to_html(md: str) -> str:
    """Minimal Markdown → HTML for the appendix (the subset APPENDIX.md uses:
    #/##/### headings, **bold**, *italic*, `code`, blank-line paragraphs, - bullets).
    No external dependency — the page must stay self-contained (§6)."""
    import html as _html
    import re

    def inline(t: str) -> str:
        t = _html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        t = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", t)
        return t

    out, para, in_list = [], [], False

    def flush_para():
        if para:
            out.append("<p>" + " ".join(para) + "</p>")
            para.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>")
            in_list = False

    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            flush_para(); close_list(); continue
        if s.startswith("### "):
            flush_para(); close_list(); out.append("<h3>" + inline(s[4:]) + "</h3>")
        elif s.startswith("## "):
            flush_para(); close_list(); out.append("<h2>" + inline(s[3:]) + "</h2>")
        elif s.startswith("# "):
            flush_para(); close_list(); out.append("<h1>" + inline(s[2:]) + "</h1>")
        elif s.startswith("- "):
            flush_para()
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append("<li>" + inline(s[2:]) + "</li>")
        else:
            close_list(); para.append(inline(s))
    flush_para(); close_list()
    return "\n".join(out)


def _appendix_page(md_text: str, css: str, wordmark_data_uri, slug: str,
                   run_date: str) -> str:
    """Standalone, self-contained appendix page (served at /<slug>/appendix)."""
    mark = (f'<img class="wordmark" src="{wordmark_data_uri}" alt="avos"/>'
            if wordmark_data_uri
            else '<span class="wordmark" style="font-family:Georgia,serif;font-size:22px">avos</span>')
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"/>'
        '<meta name="viewport" content="width=device-width,initial-scale=1"/>'
        '<title>US Market Conditions — Appendix</title>'
        f'<style>{css}</style></head><body>'
        '<header class="masthead">'
        f'{mark}<h1>US Market Conditions — Appendix</h1>'
        f'<a class="appendix-link" href="/{slug}">← Back to dashboard</a>'
        '</header>'
        f'<div class="appendix-doc">{_md_to_html(md_text)}</div>'
        '</body></html>'
    )


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
            # distinct series units, first-seen order — mirrors the boot script's
            # axis assignment (units[0]=left, units[1]=right). The axis no longer
            # prints these; they surface in the title tooltip instead.
            units = []
            if d.get("series"):
                for s in d["series"]:
                    u = _clean_unit(s.get("unit") or "") or unit
                    if u and u not in units:
                        units.append(u)
                if not units and unit:
                    units = [unit]
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
                # hover = one-sentence reading hint only. The long methodology
                # (`notes`) is now multi-paragraph and belongs solely to the
                # appendix — never fall it back into the on-page tooltip, or the
                # hover balloons. Missing tooltip degrades to nothing.
                "tip": d.get("tooltip") or "",
                "badge": status.get("level"), "badge_label": status.get("label"),
                "units": units,
                "table": d.get("table"),
            })
        if charts:
            panels.append({"title": title, "charts": charts})

    # IPO tracker (collapsible tabular section) — not a chart metric; embedded
    # separately and populated client-side by IPO_TRACKER_BOOT.
    ipo = display.get("ipo_tracker")

    tmpl = Template(open(os.path.join(HERE, "template.html.j2"), encoding="utf-8").read(),
                    autoescape=True)
    html = tmpl.render(
        css=css, run_date=run_date, phase=config.PHASE,
        lead=lead, panels=panels, ipo=ipo, app_slug=config.APP_SLUG,
        glossary=[{"term": t, "defn": d} for t, d in GLOSSARY],
        build_version=build_version, run_ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        size_budget=config.SIZE_BUDGET_MB,
        source_status=_source_status_line(),
        wordmark_data_uri=_wordmark_data_uri(),
        data_json=json.dumps({"metrics": embed, "ipo": ipo}, ensure_ascii=False),
        echarts_js=_vendored_echarts(), chart_boot=CHART_BOOT,
        ipo_boot=IPO_TRACKER_BOOT,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    dated = os.path.join(OUT_DIR, f"pulse_{run_date}.html")
    latest = os.path.join(OUT_DIR, "pulse_latest.html")
    for path in (dated, latest):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(tmp, path)
    md_path = _write_appendix(display)  # §25: methodology appendix, single-sourced
    # standalone HTML appendix, served at /<slug>/appendix (linked from the masthead)
    md_text = open(md_path, encoding="utf-8").read()
    appendix_html = _appendix_page(md_text, css, _wordmark_data_uri(),
                                   config.APP_SLUG, run_date)
    ap = os.path.join(OUT_DIR, "appendix.html")
    tmp = ap + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(appendix_html)
    os.replace(tmp, ap)
    return latest


# NOTE FOR MAINTAINERS: this file (APPENDIX.md) and the appendix page are
# generated. Do NOT edit them by hand — the per-metric prose lives in the
# `notes` field of each store.write_display(...) call in src/compute/*.py, the
# shared intro is APPENDIX_PREAMBLE below, and the bibliography is
# APPENDIX_REFERENCES. Regenerate with the render step.
APPENDIX_PREAMBLE = """\
# US Market Conditions — methodology appendix

*This page is generated from the dashboard's compute layer, so every definition
here ties directly to the code that produces the chart.*

This appendix explains how each chart on the dashboard is built — the exact
inputs, the formula, and the conventions and caveats behind the number. It is
written to stand on its own: you should not need any other document to follow it.

**How to read each entry.** Every metric is laid out the same way:

- **What it shows** — the question the chart answers, and how to read it.
- **How it's computed** — the inputs, the formula, and every term defined.
- **Caveats** — scaling, lags, coverage, and what the metric is *not*.

Each entry also carries a source-and-cadence line and, where relevant, a status
badge (for example *uncalibrated* or *proxy*) whose meaning is spelled out in
that entry's caveats. Conventions shared across many charts are collected
immediately below; individual entries refer back to them by name rather than
repeating them. Full citations for every external source appear under
References at the foot of the page.

## Shared methodology

### Retail identification and scaling
Retail trades are picked out of the consolidated tape by their execution
signature: off-exchange (TRF) prints that receive sub-penny price improvement,
the hallmark of a wholesaler internalizing a retail order. Each identified
trade is signed as a buy or a sell by comparing its price to the prevailing
national best bid/offer (NBBO) midpoint — above the midpoint is a buy, below is
a sell, and trades exactly at the midpoint are left out because their direction
is ambiguous.

This signature captures only about a third of true retail activity — the part
that leaves the sub-penny fingerprint. Dollar-denominated retail metrics are
therefore multiplied by a scale factor of ×3.0 to estimate market-wide totals.
That factor is provisional: it is flagged *uncalibrated* until it has been
fit empirically against Nasdaq's Retail Activity Tracker (RTAT), the gate being
a trailing 60-day correlation of at least 0.6 between our identified flow and
RTAT. Ratio and slope metrics — retail concentration and buy-the-dip
sensitivity — are unaffected by the scale factor and are shown unscaled.

### Small-lot options proxy
Options trades smaller than 10 contracts are used as a *proxy* for retail
options activity. This is an observed empirical regularity — small tickets
cluster in retail-favored names and behave like retail flow — not a positive
identification, and every chart built on it is labeled a proxy. The proxy
reconciles to published retail-flow totals.

### FINRA participation anchor
FINRA publishes weekly non-ATS (over-the-counter) share volume for each
reporting firm. We use only the T1 and T2 reporting tiers; OTCE (pink-sheet)
volume is excluded because it has no counterpart in our NMS-tape denominator.
FINRA's per-firm rows count both sides of an internalized trade, so its level
runs roughly twice a one-sided participation share. We rescale it onto our own
participation definition with a single multiplicative constant, `k = mean(ours ÷
FINRA)` measured over the weeks the two series overlap, and render the rescaled
FINRA line as the official trend anchor. Our own classifier estimate extends
the series through the two-to-four-week window before FINRA publishes.

### ETF flow universe
ETF-flow charts cover a curated universe of roughly 53 funds — the largest of
each complex — rather than the entire US ETF market; the coverage is labeled on
the chart. Flows use the shares-outstanding method: `flow = Δshares × NAV`, the
day-over-day change in shares outstanding times net asset value. This isolates
genuine creation and redemption activity from price moves.

### Realized-volatility conventions
Realized volatility is computed from the daily returns of index closes over a
rolling trading-day window, annualized by the square root of the number of
trading days in a year, and expressed in percentage points. Legs that tie to a
Bloomberg Terminal field follow Bloomberg's convention — log returns and √260
annualization — so the chart values match the Terminal (for example, the
360-day realized legs reproduce the VOLATILITY_360D field). Where a chart
compares implied and realized volatility at the same horizon, the realized
window is chosen to match the implied tenor (21 trading sessions ≈ one month);
where the two windows differ, the entry says so. Each entry states its exact
window and annualization factor.

## Per-metric notes
"""


# Bibliography rendered at the foot of the appendix. Full attributions for every
# external source referenced in the shared methodology and the per-metric notes,
# so the body prose can stay self-contained (no inline citations).
# Each bullet MUST be a single physical line — the appendix markdown converter
# has no lazy-continuation for list items, so a wrapped "- " line would split
# into a broken bullet plus an orphan paragraph.
APPENDIX_REFERENCES = """\
## References

- **Retail trade identification (sub-penny prints + midpoint signing).** Barber, Huang, Jorion, Odean & Schwarz, "A (Sub)penny for Your Thoughts: Tracking Retail Investor Activity in TAQ," *Journal of Finance*, 2024.
- **Retail-flow calibration benchmark.** Nasdaq Retail Activity Tracker (RTAT), Nasdaq Data Link.
- **Small-lot options proxy.** Citadel Securities equity market-structure notes (ch. 11); full external citation to be confirmed.
- **FINRA participation & margin data.** FINRA OTC (non-ATS) Transparency Data — weekly firm-level share volume — and FINRA monthly margin statistics, Financial Industry Regulatory Authority.
- **Cboe volatility & correlation indices.** VIX, VIX3M, VXN, S&P 500 Implied Correlation (COR1M / COR3M), and S&P 500 Dispersion (DSPX) index methodologies, Cboe Global Markets.
- **Realized-volatility & Terminal fields.** Bloomberg VOLATILITY_360D and related fields — trading-day window, log returns, √260 annualization.
- **Rates volatility.** ICE BofA MOVE Index (bond-market implied volatility).
- **Corporate credit spreads.** Bloomberg US Corporate (LUACOAS, IG) and US High Yield (LF98OAS, HY) option-adjusted spreads, primary (deep history); ICE BofA OAS via FRED as the recent cross-check.
- **Mortgage & agency MBS.** Freddie Mac Primary Mortgage Market Survey (PMMS); Fannie Mae current-coupon yield (Bloomberg MTGEFNCL).
- **Household debt & delinquency.** Federal Reserve Bank of New York, Household Debt and Credit Report (HHDC).
- **Household balance sheet & saving.** Federal Reserve Z.1 Financial Accounts (table B.101) and Distributional Financial Accounts (DFA); household debt-service ratio (TDSP); U.S. Bureau of Economic Analysis personal saving (PSAVERT, PMSAVE) — accessed via FRED.
- **Treasury yields & SOFR.** Federal Reserve H.15 constant-maturity Treasury yields (DGS2 / DGS5 / DGS10) and the Secured Overnight Financing Rate (SOFR), via FRED.
- **Manager sentiment.** NAAIM Exposure Index, National Association of Active Investment Managers.
- **Box-spread cross-check.** boxtrades.com (LV7 implied-yield reference).
- **Broker margin rates.** Posted margin schedules of major retail brokers (manual quarterly capture).
- **ETF flows & assets.** Fund shares outstanding, net asset value, and total assets, Bloomberg; fund universe curated in-house.
- **IPO issuance.** Jay R. Ritter, "Initial Public Offerings: Updated Statistics," University of Florida (deal roster); Bloomberg EQS (proceeds and shares); Renaissance Capital IPO ETF (IS4 aftermarket basket).
- **New-issue & fund-registration filings.** U.S. Securities and Exchange Commission, EDGAR full-text and form indexes (S-1 / F-1, 485APOS, N-1A).
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
                lines.append("")            # blank line: keep the note body its own
                lines.append(d["notes"])    # paragraph, not merged into the meta line
            lines.append("")
    lines.append(APPENDIX_REFERENCES)
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
