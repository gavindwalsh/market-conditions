"""health.py — Panel 6 computes available without a Terminal (§4 MH2/MH4).

MH2  Corporate credit — FRED ICE BofA OAS (the spec's fallback; BBG LUACOAS/
     LF98OAS become primary when the Terminal pull lands, and the ±10bp
     cross-check in §7.3 then activates).
MH4  Household borrowing rates — PMMS 30y − DGS10 spread (weekly). Optimal
     Blue / G.19 / lock-in gap extend this later.
"""
from __future__ import annotations

import pandas as pd

from .. import store, util
from .ownership import _display_series


def build_mh2() -> bool:
    ig = store.read_latest("fred_ig_oas")
    hy = store.read_latest("fred_hy_oas")
    if ig is None or hy is None:
        return False
    ig = ig.sort_values("date"); hy = hy.sort_values("date")
    both = ig[["date", "value"]].rename(columns={"value": "ig"}).merge(
        hy[["date", "value"]].rename(columns={"value": "hy"}), on="date", how="inner")
    both["diff"] = (both["hy"] - both["ig"]) * 100.0  # pct-pts → bp

    tile_val = round(float(hy["value"].iloc[-1]) * 100.0, 0)  # HY OAS in bp
    asof = str(hy["date"].iloc[-1])[:10]
    pct = util.trailing_percentile(hy["value"])

    store.write_display("MH2", {
        "id": "MH2", "name": "Corporate credit (IG/HY OAS)", "panel": "credit",
        "source": "FRED ICE BofA (fallback; BBG primary pending)", "cadence": "daily",
        "asof": asof, "unit": " bp (tile: HY OAS)",
        "series": [
            _display_series(hy[["date", "value"]].assign(value=lambda d: d.value * 100), "HY OAS"),
            _display_series(ig[["date", "value"]].assign(value=lambda d: d.value * 100), "IG OAS", role="context"),
            _display_series(both[["date", "diff"]].rename(columns={"diff": "value"}), "HY−IG", role="benchmark"),
        ],
        "tile": {"value": tile_val, "delta": None, "percentile": pct},
        "provenance": "fred_cache",
        "tooltip": "Investment-grade and high-yield credit spreads; wider = stress.",
        "notes": "ICE BofA US Corporate (IG) and High Yield OAS. BBG LUACOAS/LF98OAS "
                 "become primary when the Terminal pull lands (±10bp cross-check, §7.3).",
    })
    return True


def build_mh4() -> bool:
    mort = store.read_latest("fred_mortgage30")
    dgs = store.read_latest("fred_dgs10")
    if mort is None or dgs is None:
        return False
    mort = mort.sort_values("date"); dgs = dgs.sort_values("date")
    # PMMS is weekly (Thu); align 10y to the same-or-prior business day
    m = pd.merge_asof(
        mort[["date", "value"]].assign(date=lambda d: pd.to_datetime(d.date)).rename(columns={"value": "pmms"}),
        dgs[["date", "value"]].assign(date=lambda d: pd.to_datetime(d.date)).rename(columns={"value": "t10"}),
        on="date", direction="backward")
    m["value"] = (m["pmms"] - m["t10"]) * 100.0  # bp
    spread = m[["date", "value"]].dropna()

    tile_val = round(float(spread["value"].iloc[-1]), 0)
    asof = spread["date"].iloc[-1].strftime("%Y-%m-%d")
    pct = util.trailing_percentile(spread["value"], min_history=52)  # weekly: 1yr = 52 obs

    store.write_display("MH4", {
        "id": "MH4", "name": "Household credit — borrowing rates", "panel": "credit",
        "source": "FRED PMMS − DGS10", "cadence": "weekly",
        "asof": asof, "unit": " bp (tile: 30y mortgage − 10y UST)",
        "series": [_display_series(spread, "PMMS 30y − 10y UST (bp)")],
        "tile": {"value": tile_val, "delta": None, "percentile": pct},
        "provenance": "fred_cache",
        "tooltip": "What new mortgage borrowers pay over the 10-year Treasury.",
        "notes": "Primary mortgage spread. Optimal Blue daily locks, card APR − FF (G.19), "
                 "auto 60mo, and the FHFA lock-in gap extend this row later (§4 MH4).",
    })
    return True


def _s(series: pd.Series, name: str, role: str = "context", unit: str = None):
    df = series.rename("value").reset_index()
    df.columns = ["date", "value"]
    return _display_series(df, name, role=role, unit=unit)


def build_mh1() -> bool:
    """MH1 breadth (split 2026-07-10 per CIO): % SPX members above 50dma/200dma
    ONLY — leadership ratios moved to MH1B; the cumulative A/D line was dropped
    (survivorship compounds over a multi-year cumsum and it forced a dual axis).
    Members from grouped bars × current membership (survivorship caveat as SC5);
    200dma coverage extends as the grouped backfill deepens."""
    from ..pull import massive
    from .structure import _bbg_to_massive
    grouped = massive.read_grouped()
    members = store.read_all("bbg_spx_members")
    if grouped is None or members is None or grouped.empty:
        return False
    latest = members[members["date"] == members["date"].max()].copy()
    syms = {_bbg_to_massive(t) for t in latest["ticker"]}

    g = grouped[grouped["ticker"].isin(syms)]
    wide = g.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    wide.index = pd.to_datetime(wide.index)

    ma50, ma200 = wide.rolling(50).mean(), wide.rolling(200).mean()
    pct50 = ((wide > ma50).sum(axis=1) / wide.notna().sum(axis=1).clip(lower=1) * 100.0)[
        wide.notna().sum(axis=1) >= 400].where(ma50.notna().sum(axis=1) >= 400).dropna()
    pct200 = ((wide > ma200).sum(axis=1) / wide.notna().sum(axis=1).clip(lower=1) * 100.0).where(
        ma200.notna().sum(axis=1) >= 400).dropna()

    if pct50.empty:
        return False

    series = [_s(pct50, "% members > 50dma", role="avos", unit="%")]
    if not pct200.empty:
        series.append(_s(pct200, "% members > 200dma", unit="%"))

    store.write_display("MH1", {
        "id": "MH1", "name": "Breadth (% above moving averages)", "panel": "internals",
        "source": "Massive grouped bars × membership", "cadence": "daily",
        "asof": pct50.index[-1].strftime("%Y-%m-%d"), "unit": "% (tile: >50dma)",
        "series": series,
        "tile": {"value": round(float(pct50.iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(pct50)},
        "provenance": "derived",
        "tooltip": "Share of S&P 500 members above their 50- and 200-day averages.",
        "notes": "Membership = current list applied backward (survivorship caveat). "
                 "200dma series appears once the grouped-bars backfill provides the "
                 "lookback. Leadership ratios: MH1B.",
    })
    return True


def build_mh1b() -> bool:
    """MH1B leadership: RSP/SPY and NDX/SPX, both rebased to 100 at their
    COMMON start (the old MH1 rebased NDX/SPX at its own 2010 start → 237 vs
    ~100 crushed everything sharing the axis)."""
    from ..pull import massive
    grouped = massive.read_grouped()
    if grouped is None or grouped.empty:
        return False
    etfs = grouped[grouped["ticker"].isin({"RSP", "SPY"})].pivot_table(
        index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    etfs.index = pd.to_datetime(etfs.index)
    if not {"RSP", "SPY"} <= set(etfs.columns):
        return False
    rsp_spy = (etfs["RSP"] / etfs["SPY"]).dropna()

    ndx = store.read_latest("bbg_ndx"); spx = store.read_latest("bbg_spx")
    if ndx is None or spx is None:
        return False
    j = ndx[["date", "value"]].rename(columns={"value": "n"}).merge(
        spx[["date", "value"]].rename(columns={"value": "s"}), on="date")
    j["date"] = pd.to_datetime(j["date"])
    ndx_spx = j.set_index("date").eval("n / s").dropna()

    start = max(rsp_spy.index.min(), ndx_spx.index.min())
    rsp_spy = rsp_spy[rsp_spy.index >= start]
    ndx_spx = ndx_spx[ndx_spx.index >= start]
    if rsp_spy.empty or ndx_spx.empty:
        return False
    rsp_spy = rsp_spy / rsp_spy.iloc[0] * 100.0
    ndx_spx = ndx_spx / ndx_spx.iloc[0] * 100.0

    store.write_display("MH1B", {
        "id": "MH1B", "name": "Leadership (RSP/SPY, NDX/SPX)", "panel": "internals",
        "source": "Massive grouped bars + BBG", "cadence": "daily",
        "asof": rsp_spy.index[-1].strftime("%Y-%m-%d"), "unit": "rebased (tile: RSP/SPY)",
        "series": [_s(rsp_spy, "RSP/SPY (equal-weight vs cap-weight)", unit="rebased"),
                   _s(ndx_spx, "NDX/SPX (mega-cap growth vs broad)", unit="rebased")],
        "tile": {"value": round(float(rsp_spy.iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(rsp_spy)},
        "provenance": "derived",
        "tooltip": "Equal-weight vs cap-weight and NDX vs SPX, rebased to 100 — "
                   "falling RSP/SPY = mega-cap leadership.",
        "notes": f"Both ratios rebased to 100 at the common window start "
                 f"({start.strftime('%Y-%m-%d')}).",
    })
    return True


def build_mh3() -> bool:
    """MH3 (v1): agency MBS current-coupon spread — FNMA current coupon
    (MTGEFNCL) minus the 5y/10y Treasury blend. Consumer ABS OAS legs need
    ticker verification (itemized)."""
    fncc = store.read_latest("bbg_fncc")
    d5, d10 = store.read_latest("fred_dgs5"), store.read_latest("fred_dgs10")
    if fncc is None or d5 is None or d10 is None:
        return False
    cc = fncc.sort_values("date")[["date", "value"]].rename(columns={"value": "cc"})
    cc["date"] = pd.to_datetime(cc["date"])
    blend = d5[["date", "value"]].rename(columns={"value": "y5"}).merge(
        d10[["date", "value"]].rename(columns={"value": "y10"}), on="date")
    blend["date"] = pd.to_datetime(blend["date"])
    blend["tsy"] = (blend["y5"] + blend["y10"]) / 2.0
    m = cc.merge(blend[["date", "tsy"]], on="date")
    m["value"] = (m["cc"] - m["tsy"]) * 100.0  # bp
    df = m[["date", "value"]].dropna()
    if df.empty:
        return False
    store.write_display("MH3", {
        "id": "MH3", "name": "Household credit — market-priced (MBS CC spread)", "panel": "credit",
        "source": "BBG MTGEFNCL − FRED 5/10y blend", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp",
        "series": [_display_series(df, "FNMA current coupon − 5/10y UST blend (bp)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 0), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "derived",
        "tooltip": "Market price of mortgage credit/prepay risk.",
        "notes": "Agency MBS current-coupon spread — FNMA current coupon minus a 5y/10y "
                 "Treasury blend. Consumer ABS OAS legs (cards, autos) pending BBG "
                 "index-ticker verification (§4 MH3).",
    })
    return True


def build_mh5() -> bool:
    """MH5: household debt balances by product, quarterly (NY Fed HHDC) —
    stacked bars on ONE $T axis (CIO 2026-07-10; the stack totals to the tile).
    G.19 monthly + H.8 weekly nowcast extend this row later (§4)."""
    bal = store.read_latest("hhdc_balances")
    if bal is None or bal.empty:
        return False
    bal = bal.sort_values("date")
    products = [c for c in bal.columns
                if c not in ("date", "pulled_at", "Total")
                and pd.api.types.is_numeric_dtype(bal[c])]
    # largest-first so Mortgage sits at the bottom of the stack
    products = sorted(products, key=lambda p: -float(bal[p].iloc[-1] or 0))
    series = []
    for p in products:
        s = bal[["date", p]].dropna().rename(columns={p: "value"})
        if s.empty:
            continue
        sd = _display_series(s, str(p), role="context", unit="$T", ds="none")
        sd["kind"], sd["stack"] = "bar", True
        series.append(sd)
    total = bal.set_index("date")[products].sum(axis=1)
    asof = bal["date"].max().strftime("%Y-%m-%d")
    store.write_display("MH5", {
        "id": "MH5", "name": "Household credit — balances by product", "panel": "credit",
        "source": "NY Fed HHDC", "cadence": "quarterly",
        "asof": asof, "unit": " $T (tile: total)",
        "series": series,
        "tile": {"value": round(float(total.iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(total, min_history=40)},
        "provenance": "nyfed_cache",
        "tooltip": "Household debt balances by product — the stack totals to the "
                   "headline number.",
        "notes": "Quarterly HHDC balances by product, stacked. G.19 monthly and H.8 "
                 "weekly nowcast legs land later (§4 MH5).",
    })
    return True


def build_mh6() -> bool:
    """MH6: early-stage (30+) delinquency transition rate by product, quarterly."""
    tr = store.read_latest("hhdc_transitions")
    if tr is None or tr.empty:
        return False
    tr = tr.sort_values("date")
    # sheet headers are ALL-CAPS codes (AUTO, CC, ...) — display-friendly names
    label = {"AUTO": "Auto", "CC": "Credit card", "MORTGAGE": "Mortgage",
             "HELOC": "HELOC", "STUDENT LOAN": "Student loan", "OTHER": "Other"}
    products = [c for c in tr.columns if c not in ("date", "pulled_at", "Total")
                and pd.api.types.is_numeric_dtype(tr[c])]
    series, tile_s = [], None
    for p in products:
        s = tr[["date", p]].dropna().rename(columns={p: "value"})
        if s.empty:
            continue
        name = label.get(str(p).strip().upper(), str(p).title())
        role = "avos" if name == "Credit card" else "context"
        series.append(_display_series(s, name, role=role))
        if role == "avos":
            tile_s = s
    if tile_s is None and series:
        tile_s = tr[["date", products[0]]].dropna().rename(columns={products[0]: "value"})
    if tile_s is None:
        return False
    store.write_display("MH6", {
        "id": "MH6", "name": "Delinquency transitions (30+)", "panel": "credit",
        "source": "NY Fed HHDC", "cadence": "quarterly",
        "asof": tr["date"].max().strftime("%Y-%m-%d"), "unit": "% (tile: credit card)",
        "series": series,
        "tile": {"value": round(float(tile_s["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(tile_s["value"], min_history=40)},
        "provenance": "nyfed_cache",
        "tooltip": "% of balances newly 30+ days delinquent, by product — the earliest "
                   "household-stress read.",
        "notes": "Quarterly HHDC 'New Delinquent Balances by Loan Type' (flow into 30+ "
                 "delinquency, % of balances).",
    })
    return True


def build_mh8() -> bool:
    """MH8 (v1): NAAIM manager exposure. AAII bull−bear leg is members-only
    now (403) — itemized; ships NAAIM-only, labeled."""
    df = store.read_latest("naaim_exposure")
    if df is None or df.empty:
        return False
    df = df.sort_values("date")
    store.write_display("MH8", {
        "id": "MH8", "name": "Sentiment (NAAIM exposure)", "panel": "internals",
        "source": "NAAIM", "cadence": "weekly",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "",
        "series": [_display_series(df[["date", "value"]], "NAAIM manager equity exposure")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(df["value"], min_history=52)},
        "provenance": "scrape_cache",
        "tooltip": "Active-manager equity exposure: 0 flat, 100 fully long, ±200 levered.",
        "notes": "NAAIM weekly manager-exposure survey. AAII bull−bear leg blocked "
                 "(survey file now members-only) — see blockers list.",
    })
    return True


def build() -> dict[str, bool]:
    return {"MH1": build_mh1(), "MH1B": build_mh1b(), "MH2": build_mh2(),
            "MH3": build_mh3(), "MH4": build_mh4(), "MH5": build_mh5(),
            "MH6": build_mh6(), "MH8": build_mh8()}
