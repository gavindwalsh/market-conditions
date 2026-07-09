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
        "notes": "Primary mortgage spread. Optimal Blue daily locks, card APR − FF (G.19), "
                 "auto 60mo, and the FHFA lock-in gap extend this row later (§4 MH4).",
    })
    return True


def build_mh1() -> bool:
    """MH1 breadth: % SPX members above 50dma/200dma, A/D line, RSP/SPY ratio,
    NDX/SPX relative. Members from grouped bars × current membership (same
    survivorship caveat as SC5); 200dma coverage extends as the grouped
    backfill deepens — dates with insufficient lookback are simply absent."""
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

    rets = wide.pct_change()
    ad = (rets > 0).sum(axis=1) - (rets < 0).sum(axis=1)
    ad_line = ad.cumsum().dropna()

    etfs = grouped[grouped["ticker"].isin({"RSP", "SPY"})].pivot_table(
        index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    etfs.index = pd.to_datetime(etfs.index)
    rsp_spy = (etfs["RSP"] / etfs["SPY"]).dropna() if {"RSP", "SPY"} <= set(etfs.columns) else pd.Series(dtype=float)

    ndx = store.read_latest("bbg_ndx"); spx = store.read_latest("bbg_spx")
    ndx_spx = pd.Series(dtype=float)
    if ndx is not None and spx is not None:
        j = ndx[["date", "value"]].rename(columns={"value": "n"}).merge(
            spx[["date", "value"]].rename(columns={"value": "s"}), on="date")
        j["date"] = pd.to_datetime(j["date"])
        ndx_spx = j.set_index("date").eval("n / s")

    if pct50.empty:
        return False

    def _s(series, name, role="context"):
        df = series.rename("value").reset_index().rename(columns={"index": "date", series.index.name or "index": "date"})
        df.columns = ["date", "value"]
        return _display_series(df, name, role=role)

    series = [{**_s(pct50, "% members > 50dma", role="avos"), "unit": "%"}]
    if not pct200.empty:
        series.append({**_s(pct200, "% members > 200dma"), "unit": "%"})
    series.append({**_s(ad_line, "A/D line (cumulative)"), "unit": "cum"})
    if not rsp_spy.empty:
        series.append({**_s(rsp_spy / rsp_spy.iloc[0] * 100, "RSP/SPY (rebased)"), "unit": "%"})
    if not ndx_spx.empty:
        series.append({**_s(ndx_spx / ndx_spx.iloc[0] * 100, "NDX/SPX (rebased)", role="benchmark"), "unit": "%"})

    store.write_display("MH1", {
        "id": "MH1", "name": "Breadth", "panel": "internals",
        "source": "Massive grouped bars × membership + BBG", "cadence": "daily",
        "asof": pct50.index[-1].strftime("%Y-%m-%d"), "unit": "% (tile: >50dma)",
        "series": series,
        "tile": {"value": round(float(pct50.iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(pct50)},
        "provenance": "derived",
        "notes": "Membership = current list applied backward (survivorship caveat). "
                 "200dma series appears once the grouped-bars backfill provides the "
                 "lookback. Ratio series rebased to 100 at window start.",
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
        "notes": "Agency MBS current-coupon spread — the market price of household "
                 "mortgage credit/prepay risk. Consumer ABS OAS legs (cards, autos) "
                 "pending BBG index-ticker verification (§4 MH3).",
    })
    return True


def build_mh5() -> bool:
    """MH5 (v1): household debt balances by product, quarterly (NY Fed HHDC).
    G.19 monthly + H.8 weekly nowcast extend this row later (§4)."""
    bal = store.read_latest("hhdc_balances")
    if bal is None or bal.empty:
        return False
    bal = bal.sort_values("date")
    products = [c for c in bal.columns if c not in ("date", "pulled_at", "Total")]
    series = []
    for i, p in enumerate(products):
        s = bal[["date", p]].dropna().rename(columns={p: "value"})
        if s.empty or not pd.api.types.is_numeric_dtype(s["value"]):
            continue
        # mortgage (~$12T) gets its own axis so the smaller products are readable
        u = "$T mortgage" if "mortgage" in str(p).lower() else "$T other"
        series.append(_display_series(s, str(p), role="avos" if i == 0 else "context", unit=u))
    total = bal[["date"] + [p for p in products
                            if pd.api.types.is_numeric_dtype(bal[p])]].set_index("date").sum(axis=1)
    asof = bal["date"].max().strftime("%Y-%m-%d")
    store.write_display("MH5", {
        "id": "MH5", "name": "Household credit — balances by product", "panel": "credit",
        "source": "NY Fed HHDC", "cadence": "quarterly",
        "asof": asof, "unit": " $T (tile: total)",
        "series": series,
        "tile": {"value": round(float(total.iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(total, min_history=40)},
        "provenance": "nyfed_cache",
        "notes": "Quarterly HHDC balances (mortgage, HELOC, auto, card, student). "
                 "G.19 monthly and H.8 weekly nowcast legs land later (§4 MH5).",
    })
    return True


def build_mh6() -> bool:
    """MH6: early-stage (30+) delinquency transition rate by product, quarterly."""
    tr = store.read_latest("hhdc_transitions")
    if tr is None or tr.empty:
        return False
    tr = tr.sort_values("date")
    products = [c for c in tr.columns if c not in ("date", "pulled_at")]
    series, tile_s = [], None
    for p in products:
        s = tr[["date", p]].dropna().rename(columns={p: "value"})
        if s.empty or not pd.api.types.is_numeric_dtype(s["value"]):
            continue
        role = "avos" if "credit card" in str(p).lower() else "context"
        series.append(_display_series(s, str(p), role=role))
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
        "notes": "% of balances newly 30+ days delinquent, by product — the earliest "
                 "household-stress read in the credit stack.",
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
        "notes": "Active-manager mean equity exposure (0=flat, 100=fully long, ±200 "
                 "levered). AAII bull−bear leg blocked (survey file now members-only) — "
                 "see blockers list.",
    })
    return True


def build() -> dict[str, bool]:
    return {"MH1": build_mh1(), "MH2": build_mh2(), "MH3": build_mh3(),
            "MH4": build_mh4(), "MH5": build_mh5(), "MH6": build_mh6(),
            "MH8": build_mh8()}
