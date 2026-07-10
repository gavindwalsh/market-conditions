"""issuance.py — Panel 7 computes available without a Terminal (§4 IS2).

IS2  Filing rate — new S-1/F-1 filings per calendar month (bars).
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

    new = all_f[all_f["form"].isin(["S-1", "F-1"])]
    mo = new.groupby(new["date"].dt.to_period("M")).size().rename("value")
    # drop the partial current month from chart AND tile
    mo = mo[mo.index < pd.Timestamp.today().to_period("M")]
    if mo.empty:
        return False
    df = mo.reset_index()
    df.columns = ["date", "value"]
    df["date"] = df["date"].dt.end_time.dt.normalize()

    bars = _display_series(df, "New S-1 + F-1 per month", unit="filings/mo", ds="none")
    bars["kind"] = "bar"

    store.write_display("IS2", {
        "id": "IS2", "name": "Filing rate (S-1/F-1)", "panel": "issuance",
        "source": "SEC EDGAR form index", "cadence": "monthly",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " filings/mo",
        "series": [bars],
        "tile": {"value": float(df["value"].iloc[-1]), "delta": None,
                 "percentile": util.trailing_percentile(df["value"], min_history=24)},
        "provenance": "edgar_lake",
        "tooltip": "New S-1/F-1 registration filings per month — the IPO pipeline forming.",
        "notes": "Calendar-month sums of new S-1/F-1 registrations from the EDGAR form "
                 "index; the partial current month is dropped. Amendment (S-1/A) share "
                 "and the offering-$ parse are available extensions.",
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
    ratio.index = pd.to_datetime(ratio.index)
    # FIXED rebase anchor: first trading day on/after 2024-01-02 — a mutable
    # cache-start anchor silently changes the level's meaning when the lake
    # window shifts
    anchor = ratio.index[ratio.index >= pd.Timestamp("2024-01-02")]
    if len(anchor) == 0:
        return False
    ratio = ratio / ratio.loc[anchor[0]] * 100.0
    df = ratio.rename("value").reset_index()
    df.columns = ["date", "value"]
    store.write_display("IS4", {
        "id": "IS4", "name": "Aftermarket appetite (IPO ETF vs SPY)", "panel": "issuance",
        "source": "Massive grouped bars", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " (rebased)",
        "series": [_display_series(df, "IPO ÷ SPY, 2024-01-02 = 100")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "massive_cache",
        "tooltip": "Recent-IPO basket vs SPY — rising = aftermarket appetite for new issues.",
        "notes": "Daily close of Renaissance IPO ETF (IPO) ÷ SPY close, indexed to 100 "
                 "on 2024-01-02.",
    })
    return True


# insurance-product registrants that pollute the 485APOS stream (verified:
# MetLife/Midland National RILA filings) — not fund launches
_NONFUND_RE = r"life insurance|separate account|variable|annuity|insurance co"


def build_is7() -> bool:
    """IS7 (EDGAR half): weekly fund-launch pipeline — 485APOS + N-1A filing
    counts (new-fund registrations/amendments). BBG screen half (actual
    launches by category + closures) lands with the OP5-7 fund work."""
    import os

    frames = []
    for t in sorted(os.listdir(store.LAKE_DIR)):
        if t.startswith("edgar_formidx_"):
            df = store.read_latest(t)
            if df is not None and not df.empty:
                frames.append(df[["date", "form", "cik", "company"]])
    if not frames:
        return False
    all_f = pd.concat(frames, ignore_index=True).drop_duplicates()
    funds = all_f[all_f["form"].isin(["485APOS", "N-1A"])].copy()
    funds = funds[~funds["company"].fillna("").str.contains(
        _NONFUND_RE, case=False, regex=True)]
    funds["date"] = pd.to_datetime(funds["date"])
    wk = funds.groupby(funds["date"].dt.to_period("W-FRI")).size().rename("value")
    # drop the partial current week from chart AND tile
    wk = wk[wk.index < pd.Timestamp.today().to_period("W-FRI")]
    if wk.empty:
        return False
    df = wk.reset_index()
    df.columns = ["date", "value"]
    df["date"] = df["date"].dt.end_time.dt.normalize()
    bars = _display_series(df, "485APOS + N-1A per week", unit="filings/wk", ds="none")
    bars["kind"] = "bar"
    store.write_display("IS7", {
        "id": "IS7", "name": "Fund registration filings (485APOS + N-1A)", "panel": "issuance",
        "source": "SEC EDGAR 485APOS + N-1A", "cadence": "weekly",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " filings/wk",
        "series": [bars],
        "tile": {"value": float(df["value"].iloc[-1]), "delta": None,
                 "percentile": util.trailing_percentile(df["value"], min_history=52)},
        "provenance": "edgar_lake",
        "tooltip": "Weekly new fund-registration filings — a launch-pipeline proxy (not "
                   "launches by category; that needs the BBG fund screen).",
        "notes": "Fri-ended weekly counts of 485APOS (new series of existing trusts) + "
                 "N-1A (new funds); insurance-product registrants (variable annuity / "
                 "separate account) excluded by company name. Partial current week "
                 "dropped; category split lands with the BBG fund screen (OP5-7 work).",
    })
    return True


def build() -> dict[str, bool]:
    return {"IS2": build_is2(), "IS4": build_is4(), "IS7": build_is7()}
