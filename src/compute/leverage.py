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
    store.write_display("LV8", {
        "id": "LV8", "name": "L1: ES roll implied financing", "panel": "leverage",
        "source": "BBG ES1/ES2 + FRED SOFR", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp vs SOFR",
        "series": [_display_series(df, "ES calendar implied financing − SOFR (bp)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "derived",
        "notes": "Implied rate = ln(ES2/ES1)/0.25y + trailing SPX dividend yield "
                 "(SPTR−SPX drift). Rich vs SOFR = long-leverage demand paying up. "
                 "Noisiest in the ~5 days around quarterly roll (§4 flag).",
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
        "notes": "Implied 1M minus vol realized over the SUBSEQUENT 21 sessions — the "
                 "toll paid by option buyers. Series ends ~1 month ago by construction.",
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
    df = flow.dropna().rename("value").reset_index()
    cdf = cap.dropna().rename("value").reset_index()
    store.write_display("LV6", {
        "id": "LV6", "name": "Leveraged-ETF rebalance notional", "panel": "leverage",
        "source": "BBG AUM × Massive moves", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " $B (tile: per-1% capacity)",
        "series": [_display_series(df, "Estimated forced EOD flow ($B/day)"),
                   _display_series(cdf, "Rebalance capacity per 1% move ($B)", role="context")],
        "tile": {"value": round(float(cdf["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(cdf["value"])},
        "provenance": "derived",
        "notes": f"Σ AUM×L×(L−1)×underlying move across {len(flows)} major leveraged funds "
                 "(curated universe — coverage labeled, §A3). Same-direction flow: both "
                 "long- and inverse-levered funds chase the day's move at the close.",
    })
    return True


def build_lv13() -> bool:
    """LV13: leveraged-ETF financing residual (§5.8) — NAV return minus
    [L×index − fee/252]; rolling 20d mean ×252/(L−1) ≈ embedded financing
    spread. Median across the 2x/3x index complex, vs SOFR."""
    grouped = massive.read_grouped()
    f = store.read_latest("fred_sofr")
    if grouped is None or f is None:
        return False
    closes = grouped.pivot_table(index="date", columns="ticker", values="close",
                                 aggfunc="last").sort_index()
    closes.index = pd.to_datetime(closes.index)
    rets = closes.pct_change()
    spreads = []
    for t, (cat, L) in config.ETF_UNIVERSE.items():
        if cat != "leveraged" or abs(L) < 2:
            continue
        u = config.LEV_ETF_UNDERLYING.get(t)
        e = _etf(t)
        if e is None or u is None or u not in rets.columns:
            continue
        if u not in ("QQQ", "SPY", "SMH"):
            continue  # single-stock levs carry idiosyncratic borrow — index complex only (§5.8)
        nav_ret = e.set_index("date")["nav"].pct_change()
        r = rets[u].reindex(nav_ret.index)
        resid = nav_ret - (L * r - LEV_FEE_ANNUAL / 252)
        spread = resid.rolling(20).mean() * 252 / (L - 1) * 100.0  # pct-pts
        spreads.append(spread.rename(t))
    if not spreads:
        return False
    med = pd.concat(spreads, axis=1).median(axis=1).dropna() * 100.0  # → bp
    df = med.rename("value").reset_index().rename(columns={"index": "date"})
    df.columns = ["date", "value"]
    store.write_display("LV13", {
        "id": "LV13", "name": "L3: Leveraged-ETF financing residual", "panel": "leverage",
        "source": "BBG NAV × Massive index returns", "cadence": "weekly estimate",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp",
        "series": [_display_series(df, "Median embedded financing spread (bp, 20d roll)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "derived",
        "notes": "NAV return − [L×index − fee/252], annualized /(L−1) (§5.8). Flat "
                 f"{LEV_FEE_ANNUAL*100:.1f}% fee assumption across funds (labeled approximation). "
                 "Index 2x/3x complex only; compare vs LV7/LV8 for the leverage-cost stack.",
    })
    return True


def build_lv16() -> bool:
    """LV16: aggregate SPX short interest — Σ(short shares × price) / Σ float
    cap + median days-to-cover proxy (SHORT_INT_RATIO). Accumulates biweekly."""
    si = store.read_all("bbg_short_interest")
    members = store.read_all("bbg_spx_members")
    grouped = massive.read_grouped(days_back=5)
    if si is None or members is None or grouped is None:
        return False
    from .structure import _bbg_to_massive
    px = grouped.sort_values("date").groupby("ticker")["close"].last()
    rows = []
    for d, g in si.groupby("date"):
        g = g.drop_duplicates("ticker", keep="last").copy()
        g["sym"] = g["ticker"].map(_bbg_to_massive)
        g["px"] = g["sym"].map(px)
        snap = members[members["date"] == members["date"].max()].drop_duplicates("ticker")
        fc = snap.set_index("ticker")["float_cap"]
        si_usd = (g["short_int"] * g["px"]).sum()
        rows.append({"date": d, "si_pct": si_usd / fc.sum() / 1e6 * 100.0,
                     "dtc": g["short_int_ratio"].median()})
    df = pd.DataFrame(rows).sort_values("date")
    df["date"] = pd.to_datetime(df["date"])
    store.write_display("LV16", {
        "id": "LV16", "name": "Short interest aggregate (SPX)", "panel": "leverage",
        "source": "BBG SHORT_INT × Massive prices", "cadence": "biweekly",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "% of float (tile)",
        "series": [_display_series(df[["date", "si_pct"]].rename(columns={"si_pct": "value"}),
                                   "Short interest % of float cap", unit="%"),
                   _display_series(df[["date", "dtc"]].rename(columns={"dtc": "value"}),
                                   "Median days-to-cover", role="context", unit="days")],
        "tile": {"value": round(float(df["si_pct"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(df["si_pct"], min_history=26)},
        "provenance": "derived",
        "notes": "Accumulates one point per biweekly FINRA SI print (history builds from "
                 "2026-07-09). Days-to-cover = median SHORT_INT_RATIO across members.",
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
        "notes": ("" if BROKER_RATES_VERIFIED else
                  "*** UNVERIFIED SEED VALUES — do not trust until confirmed against "
                  "broker pages (see pull/free.py). *** ")
                 + f"Manual-quarterly posted rates, as-of {BROKER_RATES_ASOF}. The retail "
                 "leverage price stack: discount tiered vs full-service base rates.",
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
        "notes": "Debit balances in customers' securities margin accounts (monthly, "
                 "~3-week lag). Archive 1997–2021 + live page table stitched.",
    })
    return True


def build() -> dict[str, bool]:
    return {"LV6": build_lv6(), "LV7": build_lv7(), "LV8": build_lv8(),
            "LV11": build_lv11(), "LV13": build_lv13(), "LV14": build_lv14(),
            "LV15": build_lv15(), "LV16": build_lv16()}
