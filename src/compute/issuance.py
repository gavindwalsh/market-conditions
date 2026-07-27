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
        "notes": (
            "**What it shows.** The IPO pipeline forming — the number of new S-1 and F-1 "
            "registration statements filed each month. A rising count signals more "
            "companies queuing to go public, typically well ahead of the deals "
            "themselves.\n\n"
            "**How it's computed.** Calendar-month counts of new S-1 and F-1 filings from "
            "the SEC EDGAR form index, deduplicated per filing by CIK; the partial "
            "current month is dropped so it can't read artificially low.\n\n"
            "**Caveats.** Counts only for now — the amendment (S-1/A) share and an "
            "offering-dollar figure parsed from the filings are available extensions."
        ),
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
        "notes": (
            "**What it shows.** Whether investors are rewarding recent IPOs — the "
            "Renaissance IPO ETF measured against SPY. A rising line means the "
            "recent-issue basket is outperforming the broad market, a sign of healthy "
            "aftermarket appetite for new deals; a falling one means new issues are out "
            "of favor.\n\n"
            "**How it's computed.** The daily close of the Renaissance IPO ETF (ticker "
            "IPO) ÷ the SPY close, indexed to 100 on 2024-01-02. The anchor date is "
            "fixed so the level's meaning doesn't drift as the data window moves.\n\n"
            "**Caveats.** A rebased relative-strength ratio — read moves against the "
            "anchor date, not the absolute number."
        ),
    })
    return True


def _ray_cashflow() -> pd.DataFrame | None:
    """Russell 3000 index-level buyback/issuance TTM → dollars + % of market cap.

    The cash-flow fields return INDEX POINTS; `INDX_DIVISOR` is not exposed for
    RAY, so reconstruct it per observation date as `CUR_MKT_CAP / PX_LAST` and
    apply the contemporaneous value (it drifted $20.4bn/pt in 1998 to $18.1bn/pt
    in 2026 — using today's on 2009 would be ~10% wrong).

    The percent-of-market-cap columns are deliberately NOT computed from the
    dollar columns: substituting the reconstruction gives
    `pts × (mktcap/px) ÷ mktcap = pts ÷ px`, so the ratio is algebraically free
    of the divisor and of the full-cap/float-cap question that biases the dollar
    level ~5-7% high. Computing it directly keeps that immunity visible.

    Sign convention: CF_DECR_CAP_STOCK arrives NEGATIVE (buybacks are a cash
    outflow) and is kept negative, so `net = issuance + buybacks` is an algebraic
    sum and the two components straddle zero on the chart without a sign flip.
    """
    df = store.read_latest("bbg_ray_cashflow")
    if df is None or df.empty:
        return None
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    d = d.sort_values("date").drop_duplicates("date", keep="last")
    divisor_bn = d["mktcap_mn"] / 1e3 / d["px"]        # $bn per index point
    d["buybacks_bn"] = d["buybacks_pts"] * divisor_bn  # negative
    d["issuance_bn"] = d["issuance_pts"] * divisor_bn
    d["net_bn"] = d["issuance_bn"] + d["buybacks_bn"]
    d["buybacks_pct"] = d["buybacks_pts"] / d["px"] * 100.0    # negative, as above
    d["issuance_pct"] = d["issuance_pts"] / d["px"] * 100.0
    d["net_pct"] = d["buybacks_pct"] + d["issuance_pct"]
    return d


def build_is6() -> bool:
    """IS6 (Chart A): US net corporate equity issuance, % of nominal GDP.

    The Fed's definitive level — the only measure here that captures cash-M&A
    retirement and employee share issuance — as published, quarterly.

    Shown as a share of GDP, not dollars: the published history runs to 1947 and
    a dollar axis makes the first fifty years invisible next to today (same
    reason OP2/OP10/LV15 are normalized).

    Changed 2026-07-27 (CIO): the trailing-12-month SMOOTHING LINE is gone; the
    daily Bloomberg-carried extension stays. It now hangs off the last published
    QUARTERLY print rather than off a 4-quarter mean, which is a weaker anchor —
    on the spec's 85-quarter backtest the carry has skill on a TTM basis (MAE 51
    vs 57 naive) but none on raw quarterly prints (177 vs 171), because a single
    quarter's annualized flow is volatile and the buyback delta is a
    twelve-month quantity. It is therefore rendered dashed, badged an estimate,
    and described as directional in the notes. IS6B is the measurement-grade
    daily read.
    """
    fred = store.read_latest("fred_ncb_equity_issuance")
    gdp = store.read_latest("fred_gdp")
    ray = _ray_cashflow()
    if fred is None or fred.empty or gdp is None or gdp.empty:
        return False
    q = fred[["date", "value"]].sort_values("date").copy()
    q["date"] = pd.to_datetime(q["date"])
    q["saar_bn"] = q["value"] / 1e3                       # $mn → $bn, already SAAR
    # Z.1 dates are quarter-START; the level is as of quarter-END (as in OP2).
    q["date"] = q["date"] + pd.offsets.QuarterEnd(1)

    g = gdp[["date", "value"]].rename(columns={"value": "gdp_bn"}).sort_values("date").copy()
    g["date"] = pd.to_datetime(g["date"])
    q = pd.merge_asof(q, g, on="date", direction="backward").dropna(subset=["gdp_bn"])
    q["pct"] = q["saar_bn"] / q["gdp_bn"] * 100.0
    if q.empty:
        return False
    last = q.iloc[-1]

    series = [_display_series(q[["date", "pct"]].rename(columns={"pct": "value"}),
                             "Net issuance, as published (% of GDP)", unit="%", ds="none")]
    tile_val, asof, carried_note, badge = float(last["pct"]), last["date"], "", None

    if ray is not None:
        qend = last["date"]
        base, fwd = ray[ray["date"] <= qend], ray[ray["date"] > qend]
        if not base.empty and not fwd.empty:
            # Rising buybacks make net issuance MORE negative, so the buyback delta
            # carries its own sign (buybacks_pts is negative and falls as buybacks
            # rise).
            #
            # Take the delta in INDEX POINTS priced at the anchor date's divisor,
            # NOT as a difference of two dollar figures. The divisor is
            # CUR_MKT_CAP/PX_LAST and drifts daily with market cap: over
            # 2026-06-30→07-24 the index points never moved (flat at -71.8811,
            # nothing filed) yet the dollar buyback figure fell ~$8.5bn on divisor
            # drift alone. Differencing dollars would leak that in and make the
            # line wander between earnings seasons, when the whole point is that it
            # steps on filings and sits still otherwise.
            pts0 = float(base["buybacks_pts"].iloc[-1])
            div0_bn = float(base["mktcap_mn"].iloc[-1] / 1e3 / base["px"].iloc[-1])
            gdp_now = float(g["gdp_bn"].iloc[-1])   # current-quarter GDP unpublished
            carried = fwd[["date"]].copy()
            carried["value"] = (last["saar_bn"]
                                + (fwd["buybacks_pts"].values - pts0) * div0_bn) \
                / gdp_now * 100.0
            # anchor at the last published point so the two segments join
            carried = pd.concat([pd.DataFrame({"date": [qend], "value": [tile_val]}),
                                 carried], ignore_index=True)
            series.append({**_display_series(carried, "Carried forward with BBG buybacks",
                                             role="nowcast", unit="%", ds="none"),
                           "estimated_from": qend.strftime("%Y-%m-%d")})
            tile_val = float(carried["value"].iloc[-1])
            asof = carried["date"].iloc[-1]
            badge = {"level": "provisional", "label": "carried estimate"}
            carried_note = (f" As of this build it is carried {(asof - qend).days} days "
                            f"past the {qend.strftime('%Y-%m-%d')} quarter-end.")

    payload = {
        "id": "IS6", "name": "US net corporate equity issuance (% of GDP)",
        "panel": "issuance",
        "source": "FRED NCBCEBQ027S · FRED GDP · BBG RAY buybacks", "cadence": "daily",
        "asof": pd.Timestamp(asof).strftime("%Y-%m-%d"), "unit": " % of GDP",
        # Needs the renderer's time x-axis (line-only, date keys) on two counts: the
        # published series is ANNUAL 1947-1951 and quarterly from 1952, and the
        # carried segment is daily — equal-width category slots would give each of
        # the five early years a full quarter's width AND hand most of the chart to
        # the last few months.
        "series": series,
        "tile": {"value": round(tile_val, 2), "delta": None,
                 "percentile": util.trailing_percentile(q["pct"], value=tile_val,
                                                        min_history=40)},
        "provenance": "derived",
        "tooltip": "Fed measure of equity net-issued or retired by US nonfinancial "
                   "companies as a share of GDP — includes cash M&A and employee shares, "
                   "excludes financials; dashed segment is a daily estimate.",
        "notes": (
            "**What it shows.** How much equity the US nonfinancial corporate sector is "
            "issuing or retiring, as a share of the economy. Negative means companies are "
            "buying back more than they sell — equity is being withdrawn from the market. "
            "This is the definitive level: it is the only measure on the dashboard that "
            "captures **cash M&A retirement** (a company bought for cash retires its "
            "shares) and **shares issued to employees** through RSU vesting and option "
            "exercise. It is scaled by GDP rather than shown in dollars because the "
            "published history reaches 1947, and on a dollar axis the first fifty years "
            "are invisible against today's magnitudes.\n\n"
            "**How it's computed.** FRED series `NCBCEBQ027S` (Fed Z.1 Financial "
            "Accounts — Nonfinancial Corporate Business; Corporate Equities; Liability, "
            "Transactions) divided by nominal GDP. The solid line is plotted exactly as "
            "published, with no smoothing: each point is one quarter's flow quoted at a "
            "seasonally-adjusted annual rate, so it answers 'at the pace of that quarter, "
            "how much equity would leave the market in a year.' History is annual from "
            "1947 to 1951 and quarterly from 1952 onward.\n\n"
            "The Fed publishes about ten weeks after quarter-end, so the **dashed segment "
            "extends the last published print daily** using Bloomberg: it adds the change "
            "in Russell 3000 gross buybacks since that quarter-end, since rising buybacks "
            "make net issuance more negative. The buyback change is measured in index "
            "points priced at the anchor date's divisor, so the line steps when large "
            "filers report and sits still between earnings seasons rather than drifting "
            "with market cap." + carried_note + "\n\n"
            "**Caveats.** Carried-estimate badge on the dashed segment: it is "
            "**directional, not a measurement.** The carry has historically added skill "
            "when applied to a four-quarter average, but against a single quarter's "
            "print — which is what it extends here — it does no better than simply "
            "holding the last print flat, because one quarter's annualized flow is "
            "volatile and the buyback adjustment is a twelve-month quantity. Read it as "
            "the direction the next print is likely to move, and use the Russell 3000 "
            "chart alongside for a measured daily read. Quarter-to-quarter prints are "
            "genuinely volatile in their own right — recent quarters have ranged from "
            "about −$1.2tn to +$124bn at an annual rate — so read the shape across "
            "several quarters, not any single point. **Excludes financial-sector "
            "companies**, so bank and insurer buybacks are absent; the obvious FRED "
            "series for adding them counts ETF and closed-end-fund share creation as a "
            "financial equity liability and would swamp the measure. The Fed also revises "
            "prior quarters. This measure and the Russell 3000 chart are **not the same "
            "measure and should not be netted** — they differ by roughly 3× for reasons "
            "that are only partly resolved."
        ),
    }
    if badge:
        payload["status"] = badge
    store.write_display("IS6", payload)
    return True


def build_is6b() -> bool:
    """IS6B (Chart B): Russell 3000 gross buybacks vs gross cash equity issuance,
    daily, decomposed. Bloomberg-only, timelier than IS6 but a narrower measure —
    no cash M&A, no non-cash employee issuance."""
    d = _ray_cashflow()
    if d is None or len(d) < 100:
        return False
    # Plotted as a share of market cap, not dollars, for two reasons: the dollar
    # series mostly tracks market cap (a record dollar level coincides with a
    # near-record-LOW share), and pts/px cancels the divisor, so the percent basis
    # carries none of the full-cap/float-cap bias in the dollar level. Dollars are
    # quoted in the notes so the headline magnitude is still on record.
    bb = d[["date", "buybacks_pct"]].rename(columns={"buybacks_pct": "value"})
    iss = d[["date", "issuance_pct"]].rename(columns={"issuance_pct": "value"})
    net = d[["date", "net_pct"]].rename(columns={"net_pct": "value"})
    bb_mag = -d["buybacks_pct"]           # positive magnitude, for the tile/percentile
    last = d.iloc[-1]

    store.write_display("IS6B", {
        "id": "IS6B", "name": "Corporate cash-equity flow (Russell 3000)",
        "panel": "issuance",
        "source": "BBG RAY CF_DECR/INCR_CAP_STOCK", "cadence": "daily",
        "asof": d["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " % of mkt cap",
        "series": [
            _display_series(bb, "Gross buybacks", unit="%"),
            _display_series(iss, "Gross equity issued for cash", role="context", unit="%"),
            _display_series(net, "Net", role="benchmark", unit="%"),
        ],
        "tile": {"value": round(float(bb_mag.iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(bb_mag)},
        "provenance": "bloomberg_cache",
        "tooltip": "Russell 3000 buybacks vs equity sold for cash, share of market cap "
                   "(trailing 12m) — not net issuance: excludes cash M&A and employee "
                   "share issuance.",
        "notes": (
            "**What it shows.** What Russell 3000 companies spent buying back their own "
            "stock, against what they raised selling stock, straight from company cash-flow "
            "statements. Buybacks plot below the axis as a cash outflow, issuance above it, "
            "and the net line between them. Trailing twelve months at every point, updated "
            "daily as companies file — so it steps during earnings season and is flat "
            "between.\n\n"
            "Everything is shown as a **share of market capitalization** rather than in "
            "dollars, and that choice matters: buybacks are at a record dollar level "
            f"(${-last['buybacks_bn'] / 1000:.2f}tn trailing twelve months, against "
            f"${last['issuance_bn']:.0f}bn of stock sold) and simultaneously near the low "
            "end of their range as a share of the market they have to absorb. Reading the "
            "dollars alone would report a record corporate bid at a moment when the bid is "
            "historically thin relative to the market it is bidding for.\n\n"
            "**How it's computed.** `CF_DECR_CAP_STOCK` (gross buybacks) and "
            "`CF_INCR_CAP_STOCK` (gross equity issued for cash) on `RAY Index`, daily from "
            "March 1998. These return index points, so each is divided by the index price "
            "to give a share of market cap. That ratio is deliberately **not** derived from "
            "the dollar figures: Bloomberg does not expose the Russell divisor, so dollars "
            "require reconstructing it as `CUR_MKT_CAP ÷ PX_LAST` (it drifted from about "
            "$20.4bn per index point in 1998 to $18.1bn in 2026), whereas index points ÷ "
            "index price cancels the divisor algebraically and carries none of its "
            "error.\n\n"
            "**Caveats.** **This is not net issuance.** It excludes **cash M&A**: when a "
            "company is acquired for cash it simply leaves the index, so the single largest "
            "form of share retirement is invisible here. It also excludes **shares issued "
            "to employees** through RSU vesting, which create shares but no cash flow. The "
            "issuance line combines follow-on offerings, at-the-market programs and "
            "option-exercise proceeds and cannot be broken out further. **Financials are "
            "included**, unlike the Fed measure alongside it. The dollar figures quoted "
            "above are likely biased about 5-7% high because the reconstructed divisor is "
            "full-cap while the true index divisor is float-adjusted; the plotted "
            "percent-of-market-cap series is immune to this, which is why it is the basis "
            "shown. A step means a large company reported, not that money moved that day "
            "— the lag is filing lag, four to six weeks after quarter-end. Steps in late "
            "June may be Russell reconstitution rather than corporate behaviour."
        ),
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
        "notes": (
            "**What it shows.** The fund-launch pipeline — weekly counts of new fund "
            "registrations. It is a proxy for how fast asset managers are bringing new "
            "products to market, a gauge of product-side risk appetite.\n\n"
            "**How it's computed.** Friday-ended weekly counts of 485APOS filings (new "
            "series of existing trusts) plus N-1A filings (brand-new funds) from SEC "
            "EDGAR, with insurance-product registrants — variable annuity and separate-"
            "account filers — excluded by company name. The partial current week is "
            "dropped.\n\n"
            "**Caveats.** A filing-pipeline proxy, not a count of actual launches by "
            "category; that breakdown, along with fund closures, arrives with the "
            "Bloomberg fund screen (the OP5–OP7 work)."
        ),
    })
    return True


def build() -> dict[str, bool]:
    return {"IS2": build_is2(), "IS4": build_is4(), "IS7": build_is7(),
            "IS6": build_is6(), "IS6B": build_is6b()}
