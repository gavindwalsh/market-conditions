"""retail_series.py — Panel 3 + MH9 computes from the daily classifier
aggregates (massive.RETAIL_TABLE). §5.1 floor/trend caveat renders on every
tile. These soft-skip until the first process_tape_day run lands.

RF1  net retail flow ($, daily)        RF2  retail participation (of tape $)
RF5  avg retail trade size             MH9  off-exchange + odd-lot share
RF3/RF4 (concentration, buy-the-dip) land after the first real tape days —
they join memberships and SPX returns onto the same aggregates.
"""
from __future__ import annotations

import pandas as pd

from .. import store, util
from ..pull import massive
from .ownership import _display_series

# Per-metric appendix prose is self-contained and follows the What/How/Caveats
# template, referencing the shared "Retail identification and scaling" section
# by name rather than prepending a shared caveat blob.


def _daily() -> pd.DataFrame | None:
    df = massive.read_retail_daily()
    if df is None or df.empty:
        return None
    return df


def build() -> dict[str, bool]:
    df = _daily()
    if df is None:
        return {"RF1": False, "RF2": False, "RF5": False, "MH9": False}

    for c, default in (("retail_ident_usd", None), ("retail_ident_trades", None),
                       ("moc_volume", 0), ("signing", "midpoint")):
        if c not in df.columns:      # aggregates written before 2026-07-08
            if default is None:
                df[c] = df["retail_usd"] if c == "retail_ident_usd" else df["retail_trades"]
            else:
                df[c] = default
    day = df.groupby("date").agg(
        net=("retail_net_usd", "sum"), gross=("retail_usd", "sum"),
        trades=("retail_trades", "sum"), tape_usd=("tape_usd", "sum"),
        tape_vol=("tape_volume", "sum"), tape_trades=("tape_trades", "sum"),
        offexch=("offexch_volume", "sum"), oddlot=("oddlot_trades", "sum"),
        ident_usd=("retail_ident_usd", "sum"), ident_trades=("retail_ident_trades", "sum"),
        moc=("moc_volume", "sum"), signing=("signing", "first"),
    ).reset_index()
    day["date"] = pd.to_datetime(day["date"])
    day = day.sort_values("date")
    asof = day["date"].iloc[-1].strftime("%Y-%m-%d")

    def _emit(mid, name, panel, series_defs, tile_col, unit, fmt=2, note="", bars=False,
              tooltip=None, status=None):
        # series_defs tuples: (col, label, role[, kind[, unit]]) — kind 'bar'/'line'
        series = []
        for sdef in series_defs:
            c, label, role = sdef[:3]
            kind = sdef[3] if len(sdef) > 3 else ("bar" if bars else "line")
            s_unit = sdef[4] if len(sdef) > 4 else None
            sd = _display_series(day[["date", c]].rename(columns={c: "value"}), label,
                                 role=role, unit=s_unit)
            if kind == "bar":
                sd["kind"] = "bar"
            series.append(sd)
        payload = {
            "id": mid, "name": name, "panel": panel,
            "source": "Massive SIP tape (classifier)", "cadence": "daily",
            "asof": asof, "unit": unit, "series": series,
            "tile": {"value": round(float(day[tile_col].iloc[-1]), fmt), "delta": None,
                     "percentile": util.trailing_percentile(day[tile_col])},
            "provenance": "massive_tape",
            "notes": note.strip(),
        }
        if tooltip:
            payload["tooltip"] = tooltip
        if status:
            payload["status"] = status
        store.write_display(mid, payload)

    from .. import config as _cfg
    F = _cfg.RETAIL_SCALE_FACTOR
    signed = day[day["signing"] == "midpoint"].copy()
    rf1 = not signed.empty
    if rf1:
        signed["net_b"] = signed["net"] * F / 1e9
        s_rf1 = _display_series(signed[["date", "net_b"]].rename(columns={"net_b": "value"}),
                                "Est. total retail net flow ($B/day)", unit="$B")
        s_rf1["kind"] = "bar"
        store.write_display("RF1", {
            "id": "RF1", "name": "Retail net flow — daily (est. total)", "panel": "retail",
            "source": "Massive tape (classifier)", "cadence": "daily",
            "asof": asof, "unit": " $B/day", "series": [s_rf1],
            "tile": {"value": round(float(signed["net_b"].iloc[-1]), 2), "delta": None,
                     "percentile": util.trailing_percentile(signed["net_b"])},
            "provenance": "massive_tape",
            "status": {"level": "uncalibrated", "label": "×3 est. · uncalibrated"},
            "tooltip": "Estimated total retail net buying per day — classifier floor ×3 "
                       "until the RF9 calibration lands.",
            "notes": (
                "**What it shows.** Estimated net dollar flow from retail traders each "
                "day — buys minus sells — scaled to a whole-market figure. Positive "
                "bars are net buying, negative bars net selling; together they track "
                "the daily push and pull of the retail crowd.\n\n"
                "**How it's computed.** Every retail trade is identified and signed by "
                "the quote-midpoint classifier described in \"Retail identification and "
                "scaling\" above — off-exchange sub-penny prints, signed buy or sell "
                "against the NBBO midpoint. Each day's net identified dollars are summed "
                "and multiplied by the ×3 scale factor to estimate the market-wide "
                "total: `RF1 = 3.0 × Σ(signed identified retail $)`, plotted in billions "
                "of dollars per day.\n\n"
                "**Caveats.** The ×3 factor is provisional, so the series carries an "
                "*uncalibrated* badge until it is fit against Nasdaq's Retail Activity "
                "Tracker and clears the calibration gate (see the shared section). "
                "Trades executing exactly at the midpoint are excluded because their "
                "direction is ambiguous, so this is a net-direction estimate, not a "
                "gross-volume one — for gross retail dollar volume see RF10."
            ),
        })
    # RF2: weekly participation splice — FINRA T1+T2 non-ATS anchor, a T1-only
    # bridge over FINRA's publication lag, then our classifier estimate.
    # Every weekly series is labeled on the FRIDAY week-end date.
    day["particip"] = day["ident_usd"] / day["tape_usd"] * 100.0 * F
    wk2 = day.set_index("date")["particip"].resample("W-FRI").agg(["mean", "count"])
    wk2 = wk2[wk2["count"] >= 3]
    wkdf2 = wk2["mean"].rename("value").reset_index()
    series2 = []
    fin = store.read_latest("finra_weekly_otc")
    grouped = massive.read_grouped()
    fin_cut = None  # last week covered by an emitted FINRA-anchored series
    ours_w = wkdf2.set_index("date")["value"]
    if fin is not None and grouped is not None:
        otc = fin[fin["summaryTypeCode"] == "OTC_W_FIRM"].copy()
        # T1+T2 only: OTCE is pink-sheet volume absent from our NMS denominator
        otc = otc[otc["tierIdentifier"].isin(["T1", "T2"])]
        # FINRA weekStartDate is Monday -> label on the Friday week-end
        otc["week"] = pd.to_datetime(otc["weekStartDate"]) + pd.Timedelta(days=4)
        has_both = otc.groupby("week")["tierIdentifier"].agg(lambda t: {"T1", "T2"} <= set(t))
        complete = has_both[has_both].index  # T1 ~2wk lag, T2 ~4wk — need both tiers
        g = grouped.copy()
        g["date"] = pd.to_datetime(g["date"])
        g["week"] = g["date"] + pd.to_timedelta(4 - g["date"].dt.weekday, unit="D")
        wk_tot = g.groupby("week")["volume"].sum()
        wk_otc = otc[otc["week"].isin(complete)].groupby("week")["totalWeeklyShareQuantity"].sum()
        both = (wk_otc / wk_tot.reindex(wk_otc.index) * 100.0).dropna()
        wk_t1 = otc[otc["tierIdentifier"] == "T1"].groupby("week")["totalWeeklyShareQuantity"].sum()
        both_t1 = (wk_t1 / wk_tot.reindex(wk_t1.index) * 100.0).dropna()
        # CALIBRATED SPLICE: rescale FINRA's trend onto our participation
        # definition (FINRA per-firm rows count both sides, ~2x retail; one
        # multiplicative factor k = mean(ours/FINRA) over common weeks)
        overlap = both.index.intersection(ours_w.index)
        if not both.empty and len(overlap) >= 3:
            k = float((ours_w.loc[overlap] / both.loc[overlap]).mean())
            fin_cut = both.index.max()
            fdf = (both * k).rename("value").reset_index()
            fdf.columns = ["date", "value"]
            s_fin = _display_series(fdf, f"FINRA-anchored (official trend, rescaled x{k:.2f})",
                                    unit="%", ds="none")
            s_fin["kind"] = "bar"
            series2.append(s_fin)
            # T1 publishes ~2wk before T2: rescale the T1-only tail (same
            # overlap window, T1-only denominator) to fill the lag
            t1_overlap = both_t1.index.intersection(overlap)
            t1_lag = both_t1[both_t1.index > fin_cut]
            if len(t1_overlap) >= 3 and not t1_lag.empty:
                k_t1 = float((ours_w.loc[t1_overlap] / both_t1.loc[t1_overlap]).mean())
                tdf = (t1_lag * k_t1).rename("value").reset_index()
                tdf.columns = ["date", "value"]
                s_t1 = _display_series(tdf, "FINRA T1-only, rescaled (lag window)",
                                       role="context", unit="%", ds="none")
                s_t1["kind"] = "bar"
                series2.append(s_t1)
                fin_cut = t1_lag.index.max()
    ext = wkdf2 if fin_cut is None else wkdf2[wkdf2["date"] > fin_cut]
    s_wk2 = _display_series(ext, "Our estimate (extension)", role="nowcast", unit="%", ds="none")
    s_wk2["kind"] = "bar"
    series2.append(s_wk2)
    # tile: last COMPLETE week of our estimate (exclude the in-progress week)
    done = wkdf2[wkdf2["date"] <= day["date"].iloc[-1]]
    if done.empty:
        done = wkdf2
    store.write_display("RF2", {
        "id": "RF2", "name": "Retail participation — weekly (est. total)", "panel": "retail",
        "source": "FINRA weekly OTC × Massive tape (classifier)", "cadence": "weekly",
        "asof": asof, "unit": "%", "series": series2,
        "tile": {"value": round(float(done["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(done["value"], min_history=52)},
        "provenance": "finra+massive",
        "status": {"level": "uncalibrated", "label": "×3 est. · uncalibrated"},
        "tooltip": "Retail share of tape volume — solid bars are the FINRA-anchored official "
                   "trend, lighter bars our estimate covering FINRA's publication lag.",
        "notes": (
            "**What it shows.** Retail's share of total tape volume each week, scaled "
            "to a market-wide estimate. A rising line means retail is accounting for a "
            "larger slice of everything that trades.\n\n"
            "**How it's computed.** Our weekly reading is identified retail dollars ÷ "
            "total tape dollars, ×3-scaled to an estimated total, averaged over the "
            "week (weeks with fewer than three trading days are dropped). The official "
            "trend anchor, though, is FINRA's own data: weekly non-ATS (T1+T2) share "
            "volume divided by our tape volume, rescaled onto our definition per the "
            "FINRA participation anchor described in Shared methodology. Because FINRA "
            "T1 tiers publish about two weeks ahead of T2, a T1-only segment is "
            "rescaled separately to bridge that gap, and our own ×3 classifier estimate "
            "fills the most recent weeks FINRA has not yet published. The chart shows "
            "the three segments in sequence: FINRA-anchored (solid), the T1-only "
            "bridge, then our estimate.\n\n"
            "**Caveats.** The classifier captures only about a third of retail, so the "
            "level depends on the provisional ×3 factor and the series carries an "
            "*uncalibrated* badge until that factor clears the RTAT calibration gate "
            "(see Shared methodology). The tile reads the last complete week, excluding "
            "the in-progress one."
        ),
    })

    # RF10: total retail DOLLAR volume — weekly, 2023->. Gross activity in $
    # (FINRA weekly OTC has no buy/sell split -> NOT net direction; RF1 is the
    # net view). History = FINRA T1+T2 non-ATS shares dollarized by the weekly
    # tape $/share and rescaled onto our estimated-total definition; recent
    # weeks (FINRA's ~4wk publication lag) = the Massive classifier estimate.
    day["ret_usd_b"] = day["ident_usd"] * F / 1e9
    wk_usd = day.set_index("date")["ret_usd_b"].resample("W-FRI").agg(["sum", "count"])
    wk_usd = wk_usd[wk_usd["count"] >= 3]["sum"]
    series10, fin_cut10, cal10 = [], None, None
    if fin is not None and grouped is not None:
        o = fin[fin["summaryTypeCode"] == "OTC_W_FIRM"].copy()
        o = o[o["tierIdentifier"].isin(["T1", "T2"])]
        o["week"] = pd.to_datetime(o["weekStartDate"]) + pd.Timedelta(days=4)
        cmpl = o.groupby("week")["tierIdentifier"].agg(lambda t: {"T1", "T2"} <= set(t))
        cmpl = cmpl[cmpl].index  # both tiers published (T1 ~2wk lag, T2 ~4wk)
        g10 = grouped.copy()
        g10["date"] = pd.to_datetime(g10["date"])
        g10["week"] = g10["date"] + pd.to_timedelta(4 - g10["date"].dt.weekday, unit="D")
        g10["notional"] = g10["close"] * g10["volume"]
        px = g10.groupby("week")["notional"].sum() / g10.groupby("week")["volume"].sum()
        fin_sh = o[o["week"].isin(cmpl)].groupby("week")["totalWeeklyShareQuantity"].sum()
        fin_usd = (fin_sh * px.reindex(fin_sh.index)).dropna() / 1e9  # ~2x gross, uncalibrated
        ov = fin_usd.index.intersection(wk_usd.index)
        if not fin_usd.empty and len(ov) >= 3:
            k10 = float((wk_usd.loc[ov] / fin_usd.loc[ov]).mean())
            cal10 = (fin_usd * k10).sort_index()
            fin_cut10 = cal10.index.max()
            hdf = cal10.rename("value").reset_index()
            hdf.columns = ["date", "value"]
            s_h = _display_series(hdf, f"FINRA-anchored history (rescaled x{k10:.2f})",
                                  unit="$B", ds="none")
            s_h["kind"] = "bar"
            series10.append(s_h)
    ext10 = wk_usd if fin_cut10 is None else wk_usd[wk_usd.index > fin_cut10]
    edf = ext10.rename("value").reset_index()
    edf.columns = ["date", "value"]
    s_m = _display_series(edf, "Massive tape (recent, est. total)", role="nowcast",
                          unit="$B", ds="none")
    s_m["kind"] = "bar"
    series10.append(s_m)
    # tile: latest COMPLETE week (exclude in-progress); percentile vs full splice
    done10 = wk_usd[wk_usd.index <= day["date"].iloc[-1]]
    tile10 = float(done10.iloc[-1]) if not done10.empty else float(wk_usd.iloc[-1])
    full10 = (pd.concat([cal10[cal10.index <= fin_cut10], ext10])
              if cal10 is not None else wk_usd)
    store.write_display("RF10", {
        "id": "RF10", "name": "Retail dollar volume — weekly (est. total)", "panel": "retail",
        "source": "FINRA weekly OTC × Massive tape", "cadence": "weekly",
        "asof": asof, "unit": "$B", "series": series10,
        "tile": {"value": round(tile10, 1), "delta": None,
                 "percentile": util.trailing_percentile(
                     full10.reset_index(drop=True), value=tile10, min_history=52)},
        "provenance": "finra+massive",
        "status": {"level": "uncalibrated", "label": "×3 est. · uncalibrated"},
        "tooltip": "Estimated total retail dollars traded per week — gross activity, not net "
                   "(see RF1 for net). FINRA-anchored history spliced with the recent Massive tape.",
        "notes": (
            "**What it shows.** Estimated total dollars retail traded each week — gross "
            "activity, buys and sells added together, not a net direction. For the net "
            "buy-minus-sell view see RF1.\n\n"
            "**How it's computed.** Our recent weeks are the identified retail dollars "
            "×3-scaled to an estimated total and summed by week (weeks with fewer than "
            "three trading days dropped). The history is anchored to FINRA: FINRA T1+T2 "
            "non-ATS share volume is turned into dollars using that week's "
            "volume-weighted tape price (`$/share`), then rescaled onto our "
            "estimated-total definition per the FINRA participation anchor in Shared "
            "methodology. The most recent weeks — inside FINRA's roughly four-week "
            "publication lag — use the ×3 classifier estimate. The two segments are "
            "spliced into one series.\n\n"
            "**Caveats.** This is gross volume, not net flow — FINRA's weekly OTC data "
            "carries no buy/sell split, which is why RF1 remains the only net view. The "
            "×3 factor is provisional, so the series carries an *uncalibrated* badge "
            "until it clears the RTAT calibration gate (see Shared methodology)."
        ),
    })

    day["avg_size"] = (day["ident_usd"] / day["ident_trades"].clip(lower=1))
    _emit("RF5", "Avg retail trade size", "retail",
          [("avg_size", "Identified retail $ / trade", "avos")], "avg_size", " $", 0,
          bars=True, tooltip="Average dollar size of an identified retail trade.",
          note=(
              "**What it shows.** The average dollar size of a single retail trade — a "
              "texture read on whether retail is trading in bigger or smaller tickets, "
              "which tends to shift with confidence and with the mix of names in play.\n\n"
              "**How it's computed.** Identified retail dollars ÷ the count of identified "
              "retail trades, each day, using the quote-midpoint classifier described in "
              "Retail identification and scaling above.\n\n"
              "**Caveats.** Built on identified prints only. Because it is a ratio — "
              "dollars per trade — it is unaffected by the ×3 scale factor, so no scaling "
              "is applied here."
          ))

    # OP8 (MOC auction share) killed 2026-07-10 — not interesting (CIO).
    day["offexch_pct"] = day["offexch"] / day["tape_vol"] * 100.0
    day["oddlot_pct"] = day["oddlot"] / day["tape_trades"] * 100.0
    _emit("MH9", "Off-exchange + odd-lot share", "health",
          [("offexch_pct", "TRF share of volume (%)", "avos", "bar", "% of volume"),
           ("oddlot_pct", "Odd-lot share of trades (%)", "context", "line", "% of trades")],
          "offexch_pct", "%",
          tooltip="Share of volume printed off-exchange (bars, left) and share of trades "
                  "under 100 shares (line, right).",
          note=(
              "**What it shows.** Two market-structure gauges that have climbed alongside "
              "retail and internalization: the share of volume printing away from the lit "
              "exchanges (bars, left axis) and the share of trades that are odd lots — "
              "fewer than 100 shares (line, right axis).\n\n"
              "**How it's computed.** Off-exchange share is FINRA TRF print volume ÷ total "
              "tape volume; odd-lot share is the count of sub-100-share trades ÷ total "
              "trade count. Both are daily.\n\n"
              "**Caveats.** Off-exchange share captures all internalized and dark volume, "
              "not retail alone, so read it as a structure indicator rather than a pure "
              "retail gauge."
          ))

    rf3 = _build_rf3(df, F)
    rf4 = _build_rf4(day)
    return {"RF1": rf1, "RF2": True, "RF10": True, "RF5": True, "MH9": True,
            "RF3": rf3, "RF4": rf4}


def _build_rf4(day: pd.DataFrame) -> bool:
    """RF4 buy-the-dip: rolling 60d mean(retail net on SPX down days) /
    mean(retail net, all days). Needs >=20 midpoint-signed days incl >=5 down
    days — unlocks automatically as the tape backfill accumulates."""
    signed = day[day["signing"] == "midpoint"].copy()
    spx = store.read_latest("bbg_spx")
    if spx is None or len(signed) < 20:
        return False
    spx = spx.copy()
    spx["date"] = pd.to_datetime(spx["date"])
    spx["down"] = spx["value"].pct_change() < 0
    m = signed.merge(spx[["date", "down"]], on="date", how="inner").sort_values("date")
    if len(m) < 20 or m["down"].sum() < 5:
        return False
    m["net_m"] = m["net"] / 1e6
    roll_down = m["net_m"].where(m["down"]).rolling(60, min_periods=20).mean()
    roll_all = m["net_m"].rolling(60, min_periods=20).mean()
    m["value"] = roll_down / roll_all
    df = m[["date", "value"]].dropna()
    if df.empty:
        return False
    store.write_display("RF4", {
        "id": "RF4", "name": "Buy-the-dip ratio", "panel": "retail",
        "source": "Massive tape × BBG SPX", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "×",
        "series": [_display_series(df, "Retail net on down days ÷ all-day avg (60d roll)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "massive_tape",
        "status": {"level": "provisional", "label": "classifier floor"},
        "tooltip": ">1 = retail buys dips harder than its average day.",
        "notes": (
            "**What it shows.** Whether retail leans into weakness. Above 1 means "
            "retail's net buying on S&P 500 down days runs stronger than on its average "
            "day — active dip-buying; below 1 means retail pulls back when the market "
            "falls.\n\n"
            "**How it's computed.** A 60-day rolling ratio: `mean(retail net $ on SPX "
            "down days) ÷ mean(retail net $, all days)`. Retail net flow is the "
            "midpoint-signed flow from the classifier (see Retail identification and "
            "scaling above); a down day is any close-to-close decline in the S&P 500. "
            "The window needs at least 20 signed days, including at least 5 down days, "
            "before it renders.\n\n"
            "**Caveats.** Being a ratio, it is scale-invariant — the ×3 factor cancels — "
            "but it is still built on the identified third of retail, so it carries the "
            "*classifier floor* badge marking it as provisional until calibration."
        ),
    })
    return True


def _bbg_to_massive(t: str) -> str:
    return t.split(" ")[0].replace("/", ".")


def _build_rf3(df: pd.DataFrame, scale: float) -> bool:
    """RF3: est. total retail $ in the top-10 SPX names (bars) plus top-10 and
    semis shares of identified retail $ (§4). Leveraged-ETF slice lands with
    the OP7 fund screen."""
    members = store.read_all("bbg_spx_members")
    if members is None or members.empty:
        return False
    latest = members[members["date"] == members["date"].max()].copy()
    latest["sym"] = latest["ticker"].map(_bbg_to_massive)
    top10 = set(latest.nlargest(10, "weight")["sym"])
    semis = set(latest[latest["gics_industry"].fillna(0).astype(int) == 453010]["sym"])

    g = df.groupby("date").apply(lambda d: pd.Series({
        "top10_usd": d.loc[d["ticker"].isin(top10), "retail_ident_usd"].sum() * scale / 1e9,
        "top10_pct": d.loc[d["ticker"].isin(top10), "retail_ident_usd"].sum() / max(d["retail_ident_usd"].sum(), 1) * 100,
        "semi_pct": d.loc[d["ticker"].isin(semis), "retail_ident_usd"].sum() / max(d["retail_ident_usd"].sum(), 1) * 100,
    }), include_groups=False).reset_index()
    g["date"] = pd.to_datetime(g["date"])
    g = g.sort_values("date")

    s_usd = _display_series(g[["date", "top10_usd"]].rename(columns={"top10_usd": "value"}),
                            "Est. total retail $ in top-10 SPX names ($B)", unit="$B")
    s_usd["kind"] = "bar"
    store.write_display("RF3", {
        "id": "RF3", "name": "Retail concentration", "panel": "retail",
        "source": "Massive tape × SPX membership", "cadence": "daily",
        "asof": g["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "% (tile: top-10 share)",
        "series": [
            s_usd,
            _display_series(g[["date", "top10_pct"]].rename(columns={"top10_pct": "value"}),
                            "Retail $ in top-10 SPX names (%)", role="context", unit="%"),
            _display_series(g[["date", "semi_pct"]].rename(columns={"semi_pct": "value"}),
                            "Retail $ in semis (%)", role="context", unit="%"),
        ],
        "tile": {"value": round(float(g["top10_pct"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(g["top10_pct"])},
        "provenance": "massive_tape",
        "status": {"level": "provisional", "label": "classifier floor"},
        "tooltip": "Where retail dollars concentrate — bars = est. retail $ in the top-10 "
                   "names, lines = shares of all retail $.",
        "notes": (
            "**What it shows.** Where retail dollars pile up. The bars are estimated "
            "total retail dollars flowing into the ten largest S&P 500 names; the lines "
            "are the share of all retail dollars going to those top-10 names and to "
            "semiconductors. A rising line means retail is crowding into a narrower set "
            "of names.\n\n"
            "**How it's computed.** Bars: identified retail dollars in the top-10 names, "
            "×3-scaled to an estimated total ($B). Lines: top-10 retail $ ÷ all retail "
            "$, and semis retail $ ÷ all retail $ — shares computed on identified "
            "dollars only. Index membership is the latest Bloomberg S&P 500 snapshot; "
            "semis are GICS sub-industry 453010. See Retail identification and scaling "
            "above for the classifier.\n\n"
            "**Caveats.** The bars depend on the provisional ×3 factor; the share lines "
            "are ratios and so are scale-invariant. Membership is applied backward "
            "through history, so the name list carries a survivorship bias. The "
            "*classifier floor* badge marks the metric provisional until calibration."
        ),
    })
    return True
