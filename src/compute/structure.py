"""structure.py — Panel 1 computes (§4 SC1–SC4).

SC1/SC2/SC3 killed 2026-07-10 per CIO (registry rows removed); build_sc123
kept below for reference but no longer wired into build(). SC4 (DSPX) has
full history from inception → percentile live.

SEMIS_GICS: GICS industry 453010 (§4). The snapshot stores numeric codes
(e.g. 452020.0), so match on int.
"""
from __future__ import annotations

import pandas as pd

from .. import store, util
from .ownership import _display_series

SEMIS_GICS = 453010


def _member_daily() -> pd.DataFrame | None:
    """All accumulated member snapshots → one frame [date, ticker, weight, gics]."""
    df = store.read_all("bbg_spx_members")
    if df is None or df.empty:
        return None
    # dedupe within a date (last pull wins per run day)
    return df.sort_values("pulled_at").drop_duplicates(["date", "ticker"], keep="last")


def _concentration_series(df: pd.DataFrame):
    """Per date: top-10 weight, effective N, semi weight."""
    rows = []
    for d, g in df.groupby("date"):
        w = g["weight"] / 100.0
        rows.append({
            "date": d,
            "top10": g.nlargest(10, "weight")["weight"].sum(),
            "eff_n": 1.0 / (w ** 2).sum(),
            "semi": g[g["gics_industry"].fillna(0).astype(int) == SEMIS_GICS]["weight"].sum(),
        })
    return pd.DataFrame(rows).sort_values("date")


def build_sc123() -> bool:
    df = _member_daily()
    if df is None:
        return False
    cs = _concentration_series(df)
    asof = str(cs["date"].iloc[-1])[:10]
    specs = [
        ("SC1", "Top-10 weight of S&P 500", "top10", "%",
         "Σ 10 largest member weights; weights computed from float-adjusted caps "
         "(S&P weights not DAPI-entitled — see spec §4 SC1). History accumulates "
         "from build date; percentile gated until 1yr (§6)."),
        ("SC2", "Effective N (1/HHI)", "eff_n", " names",
         "1 / Σw² over SPX members — the number of equal-weight names with the "
         "same concentration. Same accumulation caveat as SC1."),
        ("SC3", "Semiconductor weight of SPX", "semi", "%",
         "Σ member weights where GICS industry = 453010. Same accumulation caveat."),
    ]
    for mid, name, col, unit, note in specs:
        s = cs[["date", col]].rename(columns={col: "value"})
        store.write_display(mid, {
            "id": mid, "name": name, "panel": "other",
            "source": "BBG member caps (computed)", "cadence": "daily",
            "asof": asof, "unit": unit,
            "series": [_display_series(s.assign(date=pd.to_datetime(s["date"])), name)],
            "tile": {"value": round(float(s["value"].iloc[-1]), 2), "delta": None,
                     "percentile": util.trailing_percentile(s["value"])},
            "provenance": "bloomberg_cache", "notes": note,
        })
    return True


def build_sc4() -> bool:
    dspx = store.read_latest("bbg_dspx")
    if dspx is None or dspx.empty:
        return False
    dspx = dspx.sort_values("date")
    store.write_display("SC4", {
        "id": "SC4", "name": "Implied dispersion (DSPX)", "panel": "other",
        "source": "BBG DSPX Index", "cadence": "daily",
        "asof": str(dspx["date"].iloc[-1])[:10], "unit": "",
        "series": [_display_series(
            dspx[["date", "value"]].assign(date=pd.to_datetime(dspx["date"])),
            "DSPX (Cboe S&P 500 Dispersion)")],
        "tile": {"value": round(float(dspx["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(dspx["value"])},
        "provenance": "bloomberg_cache",
        "tooltip": "Cboe implied dispersion — how much single names are expected to move "
                   "independently of the index.",
        "notes": (
            "**What it shows.** Cboe's implied dispersion index (DSPX) — how much single "
            "stocks are expected to move independently of the index over the coming "
            "month. High dispersion is a stock-picker's environment; low dispersion means "
            "names are expected to move together. It is the implied, forward-looking "
            "counterpart to the realized dispersion in SC5.\n\n"
            "**How it's computed.** The published Cboe S&P 500 Dispersion Index (DSPX), "
            "with full history from its inception; the tile ranks the latest level "
            "against that history.\n\n"
            "**Caveats.** Sourced from Bloomberg, with Cboe's end-of-day CSV as a "
            "fallback if the Terminal pull fails."
        ),
    })
    return True


def _bbg_to_massive(t: str) -> str:
    """'BRK/B UN Equity' → 'BRK.B'; 'AAPL UW Equity' → 'AAPL'."""
    return t.split(" ")[0].replace("/", ".")


def build_sc5() -> bool:
    """Realized cross-sectional dispersion: per day, the std-dev of SPX-member
    daily returns (§4 SC5). Massive grouped bars × current BBG membership.

    Survivorship caveat (displayed): membership is TODAY'S list applied
    backward until historical membership lands; recent readings are exact."""
    from ..pull import massive
    grouped = massive.read_grouped()
    members = store.read_all("bbg_spx_members")
    if grouped is None or members is None or grouped.empty:
        return False
    tickers = {_bbg_to_massive(t) for t in members["ticker"].unique()}
    g = grouped[grouped["ticker"].isin(tickers)][["date", "ticker", "close"]]
    wide = g.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    rets = wide.pct_change()
    # require a real cross-section: >= 400 members with a return that day
    counts = rets.notna().sum(axis=1)
    disp = (rets.std(axis=1) * 100.0)[counts >= 400].dropna()
    if disp.empty:
        return False
    s = disp.rename("value").reset_index().rename(columns={"index": "date"})
    s["date"] = pd.to_datetime(s["date"])

    store.write_display("SC5", {
        "id": "SC5", "name": "Realized cross-sectional dispersion", "panel": "other",
        "source": "Massive grouped bars × SPX membership", "cadence": "daily",
        "asof": s["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "%",
        "series": [_display_series(s, "Cross-sectional std-dev of member returns")],
        "tile": {"value": round(float(s["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(s["value"])},
        "provenance": "massive_cache",
        "tooltip": "Spread of same-day returns across S&P 500 members — realized "
                   "dispersion.",
        "status": {"level": "provisional", "label": "survivorship"},
        "notes": (
            "**What it shows.** Realized cross-sectional dispersion — how widely S&P 500 "
            "members' same-day returns spread out. It is the realized counterpart to "
            "DSPX (SC4): high readings mean big winners and losers on the same day, a "
            "stock-picker's tape; low readings mean the index moves as one.\n\n"
            "**How it's computed.** Each day, the standard deviation of member daily "
            "returns (×100), computed only on days with at least 400 members reporting a "
            "return, so a thin cross-section can't distort it.\n\n"
            "**Caveats.** Survivorship badge: the calculation uses today's membership "
            "applied backward until historical membership lands, so older readings carry "
            "that bias while recent ones are exact."
        ),
    })
    return True


def build() -> dict[str, bool]:
    # SC1-3 (concentration trio) killed 2026-07-10 per CIO — registry rows removed
    return {"SC4": build_sc4(), "SC5": build_sc5()}
