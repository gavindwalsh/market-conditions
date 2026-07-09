"""issuance.py — Panel 7 computes available without a Terminal (§4 IS2).

IS2  Filing rate — S-1/F-1 new filings per week + S-1/A amendment share.
     EDGAR form indexes, 2020→. ($ per filing needs the offering-amount parse
     from the filing documents — extension noted in §4; count ships first.)
IS7's EDGAR side (485APOS/N-1A) is captured in the same pull; the BBG ETF
     screen half lands with the Terminal work.
"""
from __future__ import annotations

import pandas as pd

from .. import store, util
from .ownership import _display_series


def build_is2() -> bool:
    # concatenate all cached quarters from the lake
    frames = []
    import os
    for t in sorted(os.listdir(store.LAKE_DIR)):
        if t.startswith("edgar_formidx_"):
            df = store.read_latest(t)
            if df is not None and not df.empty:
                # keep cik: dedup must be per-filing, not per (date, form) —
                # dropping cik collapsed all same-day same-form filings to one row
                frames.append(df[["date", "form", "cik"]])
    if not frames:
        return False
    all_f = pd.concat(frames, ignore_index=True).drop_duplicates()
    all_f["date"] = pd.to_datetime(all_f["date"])
    all_f["week"] = all_f["date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()

    new = all_f[all_f["form"].isin(["S-1", "F-1"])]
    amend = all_f[all_f["form"] == "S-1/A"]
    wk_new = new.groupby("week").size().rename("value")
    wk_amend = amend.groupby("week").size().rename("amend")
    wk = pd.concat([wk_new, wk_amend], axis=1).fillna(0).reset_index()
    wk = wk.rename(columns={"week": "date"}).sort_values("date")
    # drop the (partial) current week from the tile read, keep it on the chart
    complete = wk.iloc[:-1] if len(wk) > 1 else wk

    tile_val = float(complete["value"].iloc[-1])
    asof = complete["date"].iloc[-1].strftime("%Y-%m-%d")
    pct = util.trailing_percentile(complete["value"], min_history=52)

    amend_rate = wk.assign(value=lambda d: (d["amend"] / (d["value"] + d["amend"]).clip(lower=1)) * 100)

    store.write_display("IS2", {
        "id": "IS2", "name": "Filing rate (S-1/F-1)", "panel": "issuance",
        "source": "SEC EDGAR form index", "cadence": "weekly",
        "asof": asof, "unit": " filings/wk",
        "series": [
            _display_series(wk[["date", "value"]].assign(
                value=wk["value"].rolling(4, min_periods=2).mean()),
                "New S-1 + F-1 per week (4wk avg)", unit="count"),
            _display_series(wk[["date", "value"]], "weekly raw", role="context", unit="count"),
            _display_series(amend_rate[["date", "value"]], "S-1/A share of activity (%)", role="context", unit="%"),
        ],
        "tile": {"value": tile_val, "delta": None, "percentile": pct},
        "provenance": "edgar_lake",
        "notes": "Count of new S-1/F-1 registrations per week (Fri-ended); amendment share "
                 "= S-1/A / (S-1 + S-1/A). Offering-$ parse is a noted extension (§4 IS2). "
                 "Tile reads the last complete week.",
    })
    return True


def build_is4() -> bool:
    """IS4: aftermarket appetite — Renaissance IPO ETF vs SPY relative strength."""
    from ..pull import massive
    grouped = massive.read_grouped()
    if grouped is None or grouped.empty:
        return False
    etfs = grouped[grouped["ticker"].isin({"IPO", "SPY"})].pivot_table(
        index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    if not {"IPO", "SPY"} <= set(etfs.columns):
        return False
    ratio = (etfs["IPO"] / etfs["SPY"]).dropna()
    ratio = ratio / ratio.iloc[0] * 100.0
    df = ratio.rename("value").reset_index()
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    store.write_display("IS4", {
        "id": "IS4", "name": "Aftermarket appetite (IPO ETF vs SPY)", "panel": "issuance",
        "source": "Massive grouped bars", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " (rebased)",
        "series": [_display_series(df, "Renaissance IPO ETF / SPY, rebased=100")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "massive_cache",
        "notes": "Relative strength of recent-IPO complex vs SPY, rebased to 100 at "
                 "window start. Rising = aftermarket risk appetite for new issues.",
    })
    return True


def build_is7() -> bool:
    """IS7 (EDGAR half): weekly ETF-launch pipeline — 485APOS + N-1A filing
    counts (new-fund registrations/amendments). BBG screen half (actual
    launches by category + closures) lands with the OP5-7 fund work."""
    import os

    import pandas as pd
    frames = []
    for t in sorted(os.listdir(store.LAKE_DIR)):
        if t.startswith("edgar_formidx_"):
            df = store.read_latest(t)
            if df is not None and not df.empty:
                frames.append(df[["date", "form", "cik"]])
    if not frames:
        return False
    all_f = pd.concat(frames, ignore_index=True).drop_duplicates()
    funds = all_f[all_f["form"].isin(["485APOS", "N-1A"])].copy()
    funds["date"] = pd.to_datetime(funds["date"])
    funds["week"] = funds["date"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    wk = funds.groupby("week").size().rename("value").reset_index().rename(columns={"week": "date"})
    complete = wk.iloc[:-1] if len(wk) > 1 else wk
    store.write_display("IS7", {
        "id": "IS7", "name": "ETF/fund launch pipeline (filings)", "panel": "issuance",
        "source": "SEC EDGAR 485APOS + N-1A", "cadence": "weekly",
        "asof": complete["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " filings/wk",
        "series": [_display_series(wk, "485APOS + N-1A per week")],
        "tile": {"value": float(complete["value"].iloc[-1]), "delta": None,
                 "percentile": util.trailing_percentile(complete["value"], min_history=52)},
        "provenance": "edgar_lake",
        "notes": "Registration-pipeline proxy: 485APOS (new series of existing trusts) "
                 "+ N-1A (new funds). Category split + actual launches/closures land "
                 "with the BBG fund screen (OP5-7 work). Tile = last complete week.",
    })
    return True


def build() -> dict[str, bool]:
    return {"IS2": build_is2(), "IS4": build_is4(), "IS7": build_is7()}
