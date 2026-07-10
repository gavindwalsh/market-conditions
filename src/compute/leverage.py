"""leverage.py — Panel 4 Phase-1 computes (LV6, LV8, LV11, LV13, LV16).

LV7 (box yield) needs the SPX chain pull — separate work item.
LV14/LV15 come from free.py / finra.py. LV2-5/9/10/12 are Phase 3.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, store, util
from ..pull import massive
from .ownership import _display_series

LEV_FEE_ANNUAL = 0.009  # approx expense ratio across the major leveraged complex (labeled)


def _series(mnemonic: str) -> pd.DataFrame | None:
    df = store.read_latest(f"bbg_{mnemonic}")
    if df is None or df.empty:
        return None
    df = df.sort_values("date")
    return df[["date", "value"]].assign(date=pd.to_datetime(df["date"]))


def _etf(ticker: str) -> pd.DataFrame | None:
    df = store.read_latest(f"bbg_etf_{ticker.lower()}")
    if df is None or df.empty:
        return None
    df = df.sort_values("date")
    df["date"] = pd.to_datetime(df["date"])
    return df


def _div_yield_trailing() -> pd.Series | None:
    """SPX trailing-1y dividend yield ≈ SPTR 1y return − SPX 1y return."""
    tr, px = _series("spx_tr"), _series("spx")
    if tr is None or px is None:
        return None
    m = tr.rename(columns={"value": "tr"}).merge(
        px.rename(columns={"value": "px"}), on="date").set_index("date")
    return (m["tr"].pct_change(252) - m["px"].pct_change(252)).dropna()


def build_lv8() -> bool:
    """LV8: ES roll implied financing — ln(ES2/ES1)/Δt + div yield, spread over
    SOFR. Generic contracts; quarterly Δt ≈ 91/365. Roll windows flagged in
    the note (spread is noisiest in the 5 days around quarterly expiry)."""
    es1, es2, sofr = _series("es1"), _series("es2"), None
    f = store.read_latest("fred_sofr")
    if es1 is None or es2 is None or f is None:
        return False
    sofr = f.sort_values("date")[["date", "value"]].assign(date=lambda d: pd.to_datetime(d["date"]))
    q = _div_yield_trailing()
    m = es1.rename(columns={"value": "f1"}).merge(
        es2.rename(columns={"value": "f2"}), on="date").set_index("date")
    dt = 91.0 / 365.0
    m["impl"] = np.log(m["f2"] / m["f1"]) / dt * 100.0
    if q is not None:
        m["impl"] = m["impl"] + (q.reindex(m.index) * 100.0).ffill()
    m = m.merge(sofr.rename(columns={"value": "sofr"}), left_index=True, right_on="date")
    m["value"] = (m["impl"] - m["sofr"]) * 100.0  # pct-pts → bp
    df = m[["date", "value"]].dropna()
    if df.empty:
        return False
    # prune quarterly-roll artifacts (CIO 2026-07-10): on generic-contract flip
    # days ln(ES2/ES1)/0.25 explodes (verified −433bp 2026-03-20, +312bp
    # 2025-12-19). Drop ±2 business days around the 3rd Friday of Mar/Jun/Sep/Dec
    # plus a robust backstop vs the rolling median.
    expiries = []
    for y in range(df["date"].dt.year.min(), df["date"].dt.year.max() + 1):
        for mth in (3, 6, 9, 12):
            fri = pd.date_range(f"{y}-{mth:02d}-01", periods=21, freq="D")
            fri = [d for d in fri if d.weekday() == 4][2]  # 3rd Friday
            expiries.append(fri)
    roll_window = set()
    for e in expiries:
        roll_window.update(pd.bdate_range(e - pd.offsets.BDay(2), e + pd.offsets.BDay(2)))
    df = df[~df["date"].isin(roll_window)].copy()
    med = df["value"].rolling(60, min_periods=20).median()
    df = df[((df["value"] - med).abs() <= 150) | med.isna()]
    store.write_display("LV8", {
        "id": "LV8", "name": "L1: ES roll implied financing", "panel": "leverage",
        "source": "BBG ES1/ES2 + FRED SOFR", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp vs SOFR",
        "series": [_display_series(df, "ES calendar implied financing − SOFR (bp)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "derived",
        "tooltip": "Implied cost of index leverage from the ES futures roll, vs SOFR — "
                   "rich = leverage demand paying up.",
        "notes": "Implied rate = ln(ES2/ES1)/0.25y + trailing SPX dividend yield "
                 "(SPTR−SPX drift), minus SOFR. Days within ±2bd of quarterly expiry "
                 "dropped (generic-contract roll artifacts) plus a ±150bp-vs-rolling-"
                 "median backstop.",
    })
    return True


def build_lv11() -> bool:
    """LV11: variance risk premium — 1M implied (VIX/VXN) minus SUBSEQUENT
    21d realized vol. Series necessarily ends 21 trading days ago."""
    out_series, tile_df = [], None
    for iv_m, px_m, label in (("vix", "spx", "SPX VRP"), ("vxn", "ndx", "NDX VRP")):
        iv, px = _series(iv_m), _series(px_m)
        if iv is None or px is None:
            continue
        px = px.set_index("date")
        realized = (px["value"].pct_change().rolling(21).std() * np.sqrt(252) * 100.0)
        fwd_real = realized.shift(-21)  # realized over the NEXT month
        j = iv.set_index("date")["value"].to_frame("iv").join(fwd_real.rename("fr")).dropna()
        j["value"] = j["iv"] - j["fr"]
        df = j[["value"]].reset_index()
        out_series.append(_display_series(df, label, role="avos" if iv_m == "vix" else "context"))
        if iv_m == "vix":
            tile_df = df
    if tile_df is None:
        return False
    store.write_display("LV11", {
        "id": "LV11", "name": "L3: Variance risk premium", "panel": "leverage",
        "source": "BBG VIX/VXN vs realized", "cadence": "daily",
        "asof": tile_df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " vol pts",
        "series": out_series,
        "tile": {"value": round(float(tile_df["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(tile_df["value"])},
        "provenance": "derived",
        "tooltip": "Implied vol minus what was subsequently realized — the toll option "
                   "buyers paid; ends ~1 month ago by construction.",
        "notes": "Implied 1M (VIX/VXN) minus vol realized over the SUBSEQUENT 21 "
                 "sessions.",
    })
    return True


def build_lv6() -> bool:
    """LV6: leveraged-ETF rebalance notional — Σ AUM×L×(L−1)×daily underlying
    move (forced same-direction EOD flow) + capacity per 1% move."""
    grouped = massive.read_grouped()
    if grouped is None:
        return False
    closes = grouped.pivot_table(index="date", columns="ticker", values="close",
                                 aggfunc="last").sort_index()
    closes.index = pd.to_datetime(closes.index)
    rets = closes.pct_change()

    flows, caps = [], []
    for t, (cat, L) in config.ETF_UNIVERSE.items():
        if abs(L) <= 1 or cat != "leveraged":
            continue
        e = _etf(t)
        u = config.LEV_ETF_UNDERLYING.get(t)
        if e is None or u is None or u not in rets.columns:
            continue
        aum = e.set_index("date")["aum"]  # $M
        r = rets[u].reindex(aum.index)
        k = L * (L - 1)
        flows.append((aum.shift(1) * k * r).rename(t))
        caps.append((aum * abs(k) * 0.01).rename(t))
    if not flows:
        return False
    flow = pd.concat(flows, axis=1).sum(axis=1) / 1e3   # $M → $B
    cap = pd.concat(caps, axis=1).sum(axis=1) / 1e3
    cdf = cap.dropna().rename("value").reset_index()
    # The signed daily flow flips sign with the index move (flow ∝ signed move),
    # so the raw series saws every other day and nets to ~0 under smoothing.
    # Plot its MAGNITUDE instead — |forced flow|, 5-day averaged — which reads as
    # rebalance INTENSITY and doesn't cancel (supersedes the signed bars).
    absflow = flow.abs().rolling(5, min_periods=2).mean()
    adf = absflow.dropna().rename("value").reset_index()
    store.write_display("LV6", {
        "id": "LV6", "name": "Leveraged-ETF rebalance notional", "panel": "leverage",
        "source": "BBG AUM × Massive moves", "cadence": "daily",
        "asof": adf["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " $B (tile: per-1% capacity)",
        "series": [
            _display_series(cdf, "Rebalance capacity per 1% move ($B)", unit="$B per 1%"),
            _display_series(adf, "Forced EOD flow magnitude — 5d avg ($B/day)",
                            role="context", unit="$B/day"),
        ],
        "tile": {"value": round(float(cdf["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(cdf["value"])},
        "provenance": "derived",
        "tooltip": "Rebalance capacity per 1% index move (structural); second line = "
                   "realized forced end-of-day flow magnitude, 5-day average.",
        "notes": f"Σ AUM×L×(L−1)×underlying move across {len(flows)} major leveraged funds "
                 "(curated universe, §A3). Forced flow chases the day's move into the "
                 "close so it flips sign daily; we plot its magnitude (5d mean) as "
                 "rebalance intensity rather than the sign-flipping signed series.",
    })
    return True


def build_lv13() -> bool:
    """LV13: leveraged-ETF financing residual (§5.8, corrected 2026-07-10).

    Model: nav_ret = L×r − fee/252 − (L−1)×fin/252, so the embedded financing
    rate is fin = −resid×252/(L−1) — the pre-fix code dropped the minus (the
    chart printed MINUS the gross rate, hence 'persistent negative financing')
    and never subtracted SOFR despite the label. Long index funds only:
    inverse funds' estimator sign opposes (rebate, not borrow) and mixing them
    made the median whipsaw between two clusters; SOXL/SOXS excluded — they
    track the ICE Semi index, not SMH (±38bp/day proxy error)."""
    grouped = massive.read_grouped()
    f = store.read_latest("fred_sofr")
    if grouped is None or f is None:
        return False
    closes = grouped.pivot_table(index="date", columns="ticker", values="close",
                                 aggfunc="last").sort_index()
    closes.index = pd.to_datetime(closes.index)
    rets = closes.pct_change()
    sofr = f.sort_values("date")[["date", "value"]].copy()
    sofr["date"] = pd.to_datetime(sofr["date"])
    sofr = sofr.set_index("date")["value"]

    fins = []
    for t, (cat, L) in config.ETF_UNIVERSE.items():
        if cat != "leveraged" or L < 2:
            continue  # long index funds only (see docstring)
        u = config.LEV_ETF_UNDERLYING.get(t)
        e = _etf(t)
        if e is None or u not in ("QQQ", "SPY") or u not in rets.columns:
            continue
        nav_ret = e.set_index("date")["nav"].pct_change()
        r = rets[u].reindex(nav_ret.index)
        resid = nav_ret - (L * r - LEV_FEE_ANNUAL / 252)
        fin = -resid.rolling(20).mean() * 252 / (L - 1) * 100.0  # pct-pts, gross
        fins.append(fin.rename(t))
    if not fins:
        return False
    med = pd.concat(fins, axis=1).median(axis=1).dropna()          # gross, pct-pts
    spread = ((med - sofr.reindex(med.index).ffill()) * 100.0).dropna()  # bp vs SOFR
    df = spread.rename("value").reset_index()
    df.columns = ["date", "value"]
    store.log_run("compute:LV13", "detail",
                  f"last spread {df['value'].iloc[-1]:+.0f}bp vs SOFR "
                  f"({len(fins)} funds: TQQQ/QLD/UPRO/SSO)")
    store.write_display("LV13", {
        "id": "LV13", "name": "L3: Leveraged-ETF financing residual", "panel": "leverage",
        "source": "BBG NAV × Massive index returns", "cadence": "weekly estimate",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp vs SOFR",
        "series": [_display_series(df, "Median embedded financing − SOFR (bp, 20d roll)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "derived",
        "status": {"level": "provisional", "label": "new methodology"},
        "tooltip": "Financing spread embedded in leveraged-ETF NAV drag, vs SOFR — "
                   "the cost levered funds actually pay.",
        "notes": "fin = −[nav_ret − (L×index − fee/252)]×252/(L−1), 20d mean, median "
                 "over TQQQ/QLD/UPRO/SSO, minus same-day SOFR (§5.8 corrected "
                 "2026-07-10). Flat 0.9% fee assumption; distributions not added back "
                 "(periodic downward spikes possible).",
    })
    return True


def build_lv16() -> bool:
    """LV16: aggregate SPX short interest — Σ(short shares × price) / Σ float
    cap + median days-to-cover (SHORT_INT_RATIO). History from the 2026-07-10
    bdh backfill (bbg_short_interest_hist, 2023-11→) + daily snapshot appends.

    Fixed 2026-07-10: float_cap is in raw DOLLARS (AAPL ≈ 4.5e12), the old
    /1e6 assumed $M and made the series print 0.000. Prices: last grouped
    close on/before each print date. Float caps: CURRENT snapshot applied
    backward (survivorship/repricing caveat, labeled)."""
    hist = store.read_latest("bbg_short_interest_hist")
    snap_si = store.read_all("bbg_short_interest")
    members = store.read_all("bbg_spx_members")
    grouped = massive.read_grouped()
    if members is None or grouped is None or (hist is None and snap_si is None):
        return False
    from .structure import _bbg_to_massive
    parts = [x for x in (hist, snap_si) if x is not None and not x.empty]
    si = pd.concat(parts, ignore_index=True)
    si["date"] = pd.to_datetime(si["date"])
    si = si.sort_values("date").drop_duplicates(["date", "ticker"], keep="last")

    g = grouped[["date", "ticker", "close"]].copy()
    g["date"] = pd.to_datetime(g["date"])
    closes = g.pivot_table(index="date", columns="ticker", values="close",
                           aggfunc="last").sort_index().ffill()

    snap = members[members["date"] == members["date"].max()].drop_duplicates("ticker")
    fc_total = snap.set_index("ticker")["float_cap"].sum()

    rows = []
    for d, gg in si.groupby("date"):
        gg = gg.copy()
        gg["sym"] = gg["ticker"].map(_bbg_to_massive)
        px_dates = closes.index[closes.index <= d]
        if len(px_dates) == 0:
            continue
        px = closes.loc[px_dates[-1]]
        gg["px"] = gg["sym"].map(px)
        si_usd = (gg["short_int"] * gg["px"]).sum()
        if si_usd <= 0:
            continue
        rows.append({"date": d, "si_pct": si_usd / fc_total * 100.0,
                     "dtc": gg["short_int_ratio"].median()})
    if not rows:
        return False
    df = pd.DataFrame(rows).sort_values("date")
    store.write_display("LV16", {
        "id": "LV16", "name": "Short interest aggregate (SPX)", "panel": "leverage",
        "source": "BBG SHORT_INT × Massive prices", "cadence": "biweekly",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "% of float (tile)",
        "series": [_display_series(df[["date", "si_pct"]].rename(columns={"si_pct": "value"}),
                                   "Short interest % of float cap", unit="%", ds="none"),
                   _display_series(df[["date", "dtc"]].rename(columns={"dtc": "value"}),
                                   "Median days-to-cover", role="context", unit="days",
                                   ds="none")],
        "tile": {"value": round(float(df["si_pct"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(df["si_pct"], min_history=26)},
        "provenance": "derived",
        "status": {"level": "provisional", "label": "current floats"},
        "tooltip": "SPX short interest as % of float (biweekly prints); line = median "
                   "days-to-cover.",
        "notes": "Biweekly FINRA prints via BBG bdh backfill (2023-11→) + daily "
                 "accumulation. Denominator = CURRENT float caps applied backward.",
    })
    return True


def build_lv7() -> bool:
    """LV7: SPX box-spread implied yield vs SOFR (§5.4). History accumulates
    one point per tenor per run day (build→ per §4)."""
    box = store.read_all("bbg_box_yield")
    f = store.read_latest("fred_sofr")
    if box is None or box.empty or f is None:
        return False
    sofr = f.sort_values("date").copy()
    sofr["date"] = pd.to_datetime(sofr["date"])
    box = box.copy()
    box["date"] = pd.to_datetime(box["date"])
    box = box.sort_values("pulled_at").drop_duplicates(["date", "tenor"], keep="last")
    m = pd.merge_asof(box.sort_values("date"), sofr[["date", "value"]].rename(columns={"value": "sofr"}),
                      on="date", direction="backward")
    m["spread"] = (m["rate"] - m["sofr"]) * 100.0
    series, tile = [], None
    for tenor in ("1M", "3M"):
        t = m[m["tenor"] == tenor][["date", "spread"]].rename(columns={"spread": "value"})
        if t.empty:
            continue
        series.append(_display_series(t, f"{tenor} box − SOFR (bp)",
                                      role="avos" if tenor == "3M" else "context"))
        if tenor == "3M":
            tile = t
    if tile is None:
        return False
    store.write_display("LV7", {
        "id": "LV7", "name": "L1: Box-spread implied yield vs SOFR", "panel": "leverage",
        "source": "BBG SPX chain (§5.4)", "cadence": "daily",
        "asof": tile["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp (tile: 3M)",
        "series": series,
        "tile": {"value": round(float(tile["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(tile["value"])},
        "provenance": "bloomberg_cache",
        "tooltip": "Equity-implied borrow rate from SPX box spreads, vs SOFR.",
        "notes": "Median across 5 wide strike pairs, NBBO midpoints. History accumulates "
                 "from 2026-07-09 (§6 gate applies). Cross-check vs boxtrades.com ±25bp "
                 "(§7.3). Quotes pulled off-close are wider — prefer near-close runs.",
    })
    return True


def build_lv14() -> bool:
    """LV14: posted broker margin rates — manual-quarterly config values
    (house manual_monthly pattern; no API exists). Each carries its source
    + as-of in free.py; the tile renders the as-of prominently."""
    from ..pull.free import (BROKER_MARGIN_RATES, BROKER_RATES_ASOF,
                             BROKER_RATES_VERIFIED)
    rows = [{"date": pd.Timestamp(BROKER_RATES_ASOF), "broker": k, "value": v}
            for k, v in BROKER_MARGIN_RATES.items()]
    df = pd.DataFrame(rows)
    series = [_display_series(df[df["broker"] == b][["date", "value"]], b,
                              role="avos" if "IBKR" in b else "context")
              for b in df["broker"].unique()]
    spread = df["value"].max() - df["value"].min()
    store.write_display("LV14", {
        "id": "LV14", "name": "L4: Broker margin rates", "panel": "leverage",
        "source": "posted rates (manual, quarterly)", "cadence": "quarterly",
        "asof": BROKER_RATES_ASOF, "unit": "% (tile: IBKR−Schwab spread)",
        "series": series,
        "tile": {"value": round(spread, 2), "delta": None, "percentile": None},
        "provenance": "manual_quarterly",
        "status": (None if BROKER_RATES_VERIFIED
                   else {"level": "unverified", "label": "unverified seeds"}),
        "tooltip": "Posted broker margin rates — the price of retail leverage.",
        "notes": f"Manual-quarterly posted rates, as-of {BROKER_RATES_ASOF}. Discount "
                 "tiered vs full-service base rates."
                 + ("" if BROKER_RATES_VERIFIED else
                    " SEED VALUES pending verification against broker pages "
                    "(pull/free.py)."),
    })
    return True


def build_lv15() -> bool:
    """LV15: FINRA margin debt — level + YoY, 1997→ (archive + live page)."""
    md = store.read_latest("finra_margin_debt")
    if md is None or md.empty:
        return False
    md = md.sort_values("date").copy()
    md["bn"] = md["value"] / 1e3          # $M → $B
    md["yoy"] = md["bn"].pct_change(12) * 100.0
    level = md[["date", "bn"]].rename(columns={"bn": "value"})
    yoy = md[["date", "yoy"]].rename(columns={"yoy": "value"}).dropna()
    store.write_display("LV15", {
        "id": "LV15", "name": "L4: FINRA margin debt", "panel": "leverage",
        "source": "FINRA margin statistics", "cadence": "monthly",
        "asof": md["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " $B",
        "series": [_display_series(level, "Margin debt ($B)", unit="$B"),
                   _display_series(yoy, "YoY (%)", role="benchmark", unit="%")],
        "tile": {"value": round(float(md["bn"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(md["bn"], min_history=120)},
        "provenance": "finra_cache",
        "tooltip": "Debit balances in securities margin accounts — the stock of retail "
                   "leverage.",
        "notes": "FINRA monthly margin statistics (~3-week lag). Archive 1997–2021 + "
                 "live page table stitched.",
    })
    return True


def build_lvt() -> bool:
    """LVT: snapshot TABLE for the no-history leverage measures (CIO 2026-07-10)
    — LV5 GEX, LV7 box yields, LV10 call wings, LV14 broker rates. Each keeps
    computing/accumulating its own display JSON; this card is what the page
    shows until they have chartable history. Runs LAST in build()."""
    display = store.load_all_display()
    rows, asofs = [], []

    lv5 = display.get("LV5")
    if lv5 and lv5.get("series") and lv5["series"][0].get("points"):
        pts = lv5["series"][0]["points"]
        ext = lv5.get("extremes") or {}
        flag = ", ".join(f"{k} {v:+.1f}" for k, v in list(ext.items())[:4]) or "OI-convention"
        rows.append(["Dealer GEX, aggregate (LV5)", f"{pts[-1]['value']:+,.1f} $B/1%",
                     lv5.get("asof", "—"), flag])
        asofs.append(lv5.get("asof"))

    lv7 = display.get("LV7")
    if lv7 and lv7.get("series"):
        for s in lv7["series"]:
            if s.get("points"):
                rows.append([f"Box − SOFR, {s['name'].split(' ')[0]} (LV7)",
                             f"{s['points'][-1]['value']:+,.0f} bp",
                             lv7.get("asof", "—"), "near-close quotes preferred"])
        asofs.append(lv7.get("asof"))

    lv10 = display.get("LV10")
    if lv10 and lv10.get("series") and lv10["series"][0].get("points"):
        rows.append(["Call-wing richness (LV10)",
                     f"{lv10['series'][0]['points'][-1]['value']:+,.2f} vol pts",
                     lv10.get("asof", "—"), "+ = upside chased"])
        asofs.append(lv10.get("asof"))

    lv14 = display.get("LV14")
    if lv14 and lv14.get("series"):
        from ..pull.free import BROKER_RATES_VERIFIED
        for s in lv14["series"]:
            if s.get("points"):
                rows.append([f"Margin rate — {s['name']} (LV14)",
                             f"{s['points'][-1]['value']:,.2f} %",
                             lv14.get("asof", "—"),
                             "" if BROKER_RATES_VERIFIED else "UNVERIFIED"])
        asofs.append(lv14.get("asof"))

    if not rows:
        return False
    store.write_display("LVT", {
        "id": "LVT", "name": "Leverage levels — snapshot", "panel": "leverage",
        "source": "derived", "cadence": "daily",
        "asof": max(a for a in asofs if a), "unit": "",
        "series": [],
        "table": {"columns": ["Measure", "Latest", "As-of", "Flag"], "rows": rows},
        "tile": {"value": None, "delta": None, "percentile": None},
        "provenance": "derived",
        "status": {"level": "building", "label": "history accruing"},
        "tooltip": "Point-in-time leverage reads without chartable history yet — each "
                   "returns as a chart as its history accrues.",
        "notes": "Aggregates the latest LV5/LV7/LV10/LV14 values; those metrics keep "
                 "accumulating their own series behind the scenes.",
    })
    return True


def build() -> dict[str, bool]:
    # LVT is assembled at the END of the opra pass (compute order runs leverage
    # before opra, and LVT needs opra's LV5/LV10 from the SAME run)
    return {"LV6": build_lv6(), "LV7": build_lv7(), "LV8": build_lv8(),
            "LV11": build_lv11(), "LV13": build_lv13(), "LV14": build_lv14(),
            "LV15": build_lv15(), "LV16": build_lv16()}
