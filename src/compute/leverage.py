"""leverage.py — Panel 4 Phase-1 computes (LV6, LV8, LV11, LV13, LV15).

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
        "notes": (
            "**What it shows.** The implied cost of index leverage read from the S&P 500 "
            "(ES) futures roll, quoted as a spread over SOFR. A rich (positive) spread "
            "means leverage demand is paying up to be long the index via futures.\n\n"
            "**How it's computed.** The calendar between the front and second ES "
            "contracts implies a financing rate, `ln(ES2/ES1)/Δt` with `Δt ≈ 0.25y` "
            "(91/365). Because holding futures forgoes dividends, we add back the "
            "trailing S&P 500 dividend yield — estimated from the one-year SPTR-minus-SPX "
            "return drift — and subtract SOFR, leaving a spread in basis points.\n\n"
            "**Caveats.** On generic-contract roll days the front/second ratio explodes "
            "(verified swings of several hundred bp around quarterly expiry), so days "
            "within ±2 business days of the March/June/September/December third Friday "
            "are dropped, backed by a ±150bp-versus-60-day-median filter."
        ),
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
        "id": "LV11", "name": "Variance risk premium", "panel": "volatility",
        "source": "BBG VIX/VXN vs realized", "cadence": "daily",
        "asof": tile_df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " vol pts",
        "series": out_series,
        "tile": {"value": round(float(tile_df["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(tile_df["value"])},
        "provenance": "derived",
        "tooltip": "Implied vol minus what was subsequently realized — the toll option "
                   "buyers paid; ends ~1 month ago by construction.",
        "notes": (
            "**What it shows.** What a month of implied volatility turned out to cost "
            "versus what actually came to pass — one-month implied vol (VIX for the "
            "S&P 500, VXN for the Nasdaq-100) minus the volatility realized over the "
            "month that followed. Positive means option buyers overpaid and sellers "
            "earned the premium; negative means realized vol overshot what was priced. "
            "Because it looks forward to realized outcomes, the series necessarily "
            "ends about a month ago.\n\n"
            "**How it's computed.** For each index, `VRP = IV_1M − RV_next21`, where "
            "`IV_1M` is the implied-vol index and `RV_next21` is realized volatility "
            "over the subsequent 21 trading sessions — daily returns, a rolling 21-day "
            "standard deviation, annualized by √252, then shifted back so each day is "
            "paired with the volatility that came after it. The tile ranks the S&P 500 "
            "series.\n\n"
            "**Caveats.** The forward-looking realized leg cannot be computed for the "
            "most recent ~21 sessions, so by construction the line stops about one "
            "month short of today."
        ),
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
        "notes": (
            "**What it shows.** How much leveraged ETFs must trade to keep their exposure "
            "on target as the market moves — both the structural capacity per 1% index "
            "move and the realized forced end-of-day flow. Large capacity means these "
            "funds can amplify late-day moves.\n\n"
            f"**How it's computed.** Rebalance capacity per 1% move is `Σ AUM·|L·(L−1)|·"
            f"0.01`; realized forced flow is `Σ AUM·L·(L−1)·(underlying daily return)`, "
            f"both summed across {len(flows)} major leveraged funds (a curated universe — "
            "see ETF flow universe in Shared methodology). `L` is each fund's leverage "
            "factor. Because the forced flow chases the day's move into the close, it "
            "flips sign every day; we plot its magnitude on a 5-day mean as rebalance "
            "*intensity* rather than the sign-flipping signed series.\n\n"
            "**Caveats.** Covers the curated leveraged universe, not every leveraged ETF. "
            "The flow line is a magnitude, so it shows how much rebalancing there is, not "
            "its direction."
        ),
    })
    return True


def build_lv13() -> bool:
    """LV13: leveraged-ETF financing residual (§5.8; total-return fix 2026-07-10).

    Model: nav_ret = L×r_tr − fee/252 − (L−1)×fin/252, so embedded financing is
    fin = −resid×252/(L−1). r_tr is the underlying's TOTAL return (SPTR / XNDX),
    NOT price return: the fund's swaps earn the index total return, so price-only
    left the index dividend yield in the residual and read as apparent NEGATIVE
    financing (a ~−(L/(L−1))×div bias, lumpy around quarterly ex-dates). Long
    index funds only (inverse funds' estimator sign opposes); SPY/QQQ funds only
    (SOXL/SOXS track the ICE Semi index, not SMH)."""
    f = store.read_latest("fred_sofr")
    if f is None:
        return False
    sofr = f.sort_values("date")[["date", "value"]].copy()
    sofr["date"] = pd.to_datetime(sofr["date"])
    sofr = sofr.set_index("date")["value"]

    # underlying -> total-return index series; daily TR return backs financing out
    tr_of = {"SPY": "spx_tr", "QQQ": "ndx_tr"}
    tr_ret = {}
    for u, mnem in tr_of.items():
        s = _series(mnem)
        if s is None:
            return False
        tr_ret[u] = s.set_index("date")["value"].pct_change()

    fins = []
    for t, (cat, L) in config.ETF_UNIVERSE.items():
        if cat != "leveraged" or L < 2:
            continue  # long index funds only (see docstring)
        u = config.LEV_ETF_UNDERLYING.get(t)
        e = _etf(t)
        if e is None or u not in tr_ret:
            continue
        nav_ret = e.set_index("date")["nav"].pct_change()
        r = tr_ret[u].reindex(nav_ret.index)
        resid = nav_ret - (L * r - LEV_FEE_ANNUAL / 252)
        # 60d mean: the residual amplifies ~1bp/day NAV-vs-index tracking noise
        # into ~250bp of financing, so a short window is unreadable — 60d lands
        # the latest read on its own trend (20d spikes to ~2x it).
        fin = -resid.rolling(60).mean() * 252 / (L - 1) * 100.0  # pct-pts, gross
        fins.append(fin.rename(t))
    if not fins:
        return False
    med = pd.concat(fins, axis=1).median(axis=1).dropna()          # gross, pct-pts
    spread = ((med - sofr.reindex(med.index).ffill()) * 100.0).dropna()  # bp vs SOFR
    df = spread.rename("value").reset_index()
    df.columns = ["date", "value"]
    store.log_run("compute:LV13", "detail",
                  f"last spread {df['value'].iloc[-1]:+.0f}bp vs SOFR "
                  f"({len(fins)} funds, total-return basis)")
    store.write_display("LV13", {
        "id": "LV13", "name": "L3: Leveraged-ETF financing residual", "panel": "leverage",
        "source": "BBG NAV × total-return index", "cadence": "weekly estimate",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp vs SOFR",
        "series": [_display_series(df, "Median embedded financing − SOFR (bp, 60d roll)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "derived",
        "status": {"level": "provisional", "label": "new methodology"},
        "tooltip": "Financing spread embedded in leveraged-ETF NAV drag, vs SOFR — "
                   "the cost levered funds actually pay.",
        "notes": (
            "**What it shows.** The financing spread buried inside leveraged-ETF returns "
            "— the cost these funds effectively pay on their embedded swaps, versus SOFR. "
            "It is the recurring toll of levered index exposure.\n\n"
            "**How it's computed.** The return model is `nav_ret = L·r_tr − fee/252 − "
            "(L−1)·fin/252`, so embedded financing backs out as `fin = −[nav_ret − (L·r_tr "
            "− fee/252)]·252/(L−1)`. Crucially `r_tr` is the underlying's TOTAL return "
            "(SPTR / XNDX), not price return: the funds' swaps earn the index total "
            "return, so using price-only return would leave the dividend yield in the "
            "residual and read as spurious negative financing. We take a 60-day mean, the "
            "median across TQQQ/QLD/UPRO/SSO, minus same-day SOFR, in basis points, with a "
            "flat 0.9% fee assumed.\n\n"
            "**Caveats.** New-methodology badge. The residual amplifies tiny NAV-versus-"
            "index tracking errors into large financing swings, so the level is noisy — "
            "read the trend, not the point. Long 2×/3× S&P and Nasdaq funds only "
            "(inverse funds' estimator has the opposite sign)."
        ),
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
        "source": "BBG SPX chain", "cadence": "daily",
        "asof": tile["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp (tile: 3M)",
        "series": series,
        "tile": {"value": round(float(tile["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(tile["value"])},
        "provenance": "bloomberg_cache",
        "tooltip": "Equity-implied borrow rate from SPX box spreads, vs SOFR.",
        "notes": (
            "**What it shows.** The risk-free borrowing rate implied by S&P 500 options "
            "box spreads, versus SOFR — an equity-market read on funding costs, and a "
            "clean benchmark for the other financing measures.\n\n"
            "**How it's computed.** A box spread (offsetting call and put pairs at two "
            "strikes) locks in a fixed payoff, so its price implies a fixed financing "
            "rate. We take the median implied rate across five wide strike pairs at NBBO "
            "midpoints, minus SOFR, for the 1-month and 3-month tenors (basis points).\n\n"
            "**Caveats.** History accumulates one point per tenor per run day. Quotes "
            "pulled away from the close are wider, so near-close runs are preferred; the "
            "series is cross-checked against boxtrades.com within ±25bp."
        ),
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
        "notes": (
            "**What it shows.** The posted margin interest rates at major retail brokers "
            "— the headline price of retail leverage. The tile shows the spread between "
            "the cheapest and most expensive broker.\n\n"
            f"**How it's computed.** Posted rates entered by hand each quarter (no API "
            f"exists), as of {BROKER_RATES_ASOF}, each with its own source and date; "
            "discount-broker tiered rates are shown against full-service base rates.\n\n"
            "**Caveats.** Manual quarterly values, not a live feed."
            + ("" if BROKER_RATES_VERIFIED else
               " The current figures are seed values pending verification against the "
               "brokers' posted pages.")
        ),
    })
    return True


def build_lv15() -> bool:
    """LV15: FINRA margin debt as a share of nominal GDP, 1997→ (archive + live
    page). Normalizing by GDP makes the leverage stock comparable across the
    cycle rather than growing mechanically with the economy."""
    md = store.read_latest("finra_margin_debt")
    gdp = store.read_latest("fred_gdp")
    if md is None or md.empty or gdp is None or gdp.empty:
        return False
    md = md.sort_values("date").copy()
    md["date"] = pd.to_datetime(md["date"])
    md["bn"] = md["value"] / 1e3          # $M → $B
    gdp = gdp.sort_values("date").copy()
    gdp["date"] = pd.to_datetime(gdp["date"])
    # GDP is quarterly ($B, SAAR); carry each print forward onto the monthly
    # margin-debt dates so the ratio steps at quarter boundaries.
    m = pd.merge_asof(md, gdp.rename(columns={"value": "gdp_bn"})[["date", "gdp_bn"]],
                      on="date", direction="backward").dropna(subset=["gdp_bn"])
    m["pct"] = m["bn"] / m["gdp_bn"] * 100.0
    pct = m[["date", "pct"]].rename(columns={"pct": "value"})
    store.write_display("LV15", {
        "id": "LV15", "name": "L4: FINRA margin debt (% of GDP)", "panel": "leverage",
        "source": "FINRA margin statistics · FRED GDP", "cadence": "monthly",
        "asof": m["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " %",
        "series": [_display_series(pct, "Margin debt (% of GDP)", unit="%")],
        "tile": {"value": round(float(m["pct"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(m["pct"], min_history=120)},
        "provenance": "finra_cache",
        "tooltip": "Debit balances in securities margin accounts as a share of nominal GDP "
                   "— the stock of investor leverage, scaled to the economy.",
        "notes": (
            "**What it shows.** Total debit balances in securities margin accounts — the "
            "outstanding stock of investor leverage — as a percentage of nominal GDP, so "
            "the level is comparable across decades rather than drifting up with the size "
            "of the economy. A classic risk-appetite gauge: sharp rises tend to accompany "
            "late-cycle exuberance.\n\n"
            "**How it's computed.** FINRA's monthly margin statistics (debit balances, in "
            "billions of dollars) divided by nominal GDP — FRED series GDP, quarterly in "
            "billions at a seasonally-adjusted annual rate, carried forward to each month "
            "— times 100. Margin-debt history stitches a 1997–2021 archive to the live "
            "FINRA page table.\n\n"
            "**Caveats.** Margin debt is reported with roughly a three-week lag after "
            "month-end; GDP is quarterly, so the denominator steps at quarter boundaries."
        ),
    })
    return True


def build() -> dict[str, bool]:
    # LV7/LV14 keep computing so their history accrues (they no longer surface
    # on the page: the LVT snapshot table was removed 2026-07-24, CIO cleanup).
    # LV16 (short interest aggregate) removed 2026-07-27, CIO: not helpful. The
    # BBG short-interest pull stays so the lake keeps accruing prints.
    return {"LV6": build_lv6(), "LV7": build_lv7(), "LV8": build_lv8(),
            "LV11": build_lv11(), "LV13": build_lv13(), "LV14": build_lv14(),
            "LV15": build_lv15()}
