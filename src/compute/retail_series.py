"""retail_series.py — Panel 3 + MH9 computes from the daily classifier
aggregates (massive.RETAIL_TABLE). §5.1 floor/trend caveat renders on every
tile. These soft-skip until the first process_tape_day run lands.

RF1  retail breadth (% of names bought)  RF2  retail participation (of tape $)
MH9  off-exchange + odd-lot share
RF3/RF4 (concentration, buy-the-dip) land after the first real tape days —
they join memberships and SPX returns onto the same aggregates.
"""
from __future__ import annotations

import numpy as np
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


# ---- arithmetic-impossibility alarm (added 2026-07-31) -----------------------
# |scaled net| can never exceed ~half a symbol's consolidated dollar volume:
# net <= gross by definition, and identified retail gross <= tape, so a net
# above half the tape would require retail to be more than the whole market on
# one side. Breaches are therefore proof of non-retail contamination.
#
# This ALARMS, it does not correct. Clamping cannot recover a clean number,
# because both inputs to any clamp are already contaminated — on 2026-07-30 even
# clamping SPY to its own historical median one-sidedness still left -$15.5B of
# a -$34.7B print. The correction is the per-print cap
# (config.RETAIL_MAX_PRINT_USD); this guard exists so that if the cap ever fails
# to hold the line, an impossible number is flagged instead of rendered.
CEILING_FRAC = 0.50
# Materiality floor. Without it the alarm is useless: on 2026-07-30 it fired on
# 477 symbol-days, 472 of them under $1M of daily tape — illiquid names where the
# classifier happens to catch nearly every print, so the ×3 scaling alone pushes
# the ratio past 100%. Those 477 were 0.005% of the day's tape and $0.008B of
# RF1. Real contamination shows up in liquid names (SPY, QQQ), so require enough
# tape for the ratio to mean anything.
CEILING_MIN_TAPE_USD = 100_000_000.0


def _ceiling_breaches(df: pd.DataFrame, scale: float) -> pd.DataFrame:
    """Symbol-days whose scaled net retail flow exceeds CEILING_FRAC of that
    symbol's consolidated dollar volume, restricted to symbols with at least
    CEILING_MIN_TAPE_USD of tape. Empty frame = clean."""
    d = df[df["tape_usd"] >= CEILING_MIN_TAPE_USD].copy()
    d["ceiling_ratio"] = d["retail_net_usd"].abs() * scale / d["tape_usd"]
    out = d[d["ceiling_ratio"] > CEILING_FRAC]
    return out[["date", "ticker", "retail_net_usd", "tape_usd", "ceiling_ratio"]] \
        .sort_values("ceiling_ratio", ascending=False)


ALL_OFF = {"RF1": False, "RF2": False, "RF10": False, "MH9": False,
           "RF3": False, "RF4": False}


def _wholesaler_otc(fin: pd.DataFrame | None) -> pd.DataFrame | None:
    """FINRA weekly non-ATS rows for the RETAIL WHOLESALERS only, labelled on
    the Friday week-end date (the feed's weekStartDate is a Monday).

    Previously RF2/RF10 anchored on every reporting firm, which counts the bank
    facilitation desks — Goldman, Morgan Stanley, BofA and JPM were ~14% of
    non-ATS in one sampled week and none of it is retail."""
    import re
    if fin is None or fin.empty:
        return None
    from .. import config as _cfg
    o = fin[fin["summaryTypeCode"] == "OTC_W_FIRM"].copy()
    o = o[o["tierIdentifier"].isin(["T1", "T2"])]   # OTCE is pink-sheet, no NMS denominator
    pat = "|".join(re.escape(w) for w in _cfg.RETAIL_WHOLESALERS)
    o = o[o["marketParticipantName"].fillna("").str.upper().str.contains(pat)]
    if o.empty:
        return None
    o["week"] = pd.to_datetime(o["weekStartDate"]) + pd.Timedelta(days=4)
    return o


def _fit_capture(day: pd.DataFrame, fin: pd.DataFrame | None,
                 grouped: pd.DataFrame | None) -> tuple[float, str]:
    """Capture factor MEASURED against FINRA's reported wholesaler volume.

    The classifier identifies only a fraction of retail activity. Until 2026-08
    that fraction was asserted (×3) and slated for calibration against Nasdaq
    RTAT — but RTAT is itself BJZZ-derived and so probably carries the same
    institutional contamination we just removed, meaning calibrating to it would
    re-import the error. FINRA publishes actual wholesaler share volume, which
    is reported rather than estimated, so fit against that instead:

        factor = mean(FINRA wholesaler $ ÷ our identified $) over common weeks

    Returns (factor, source) where source is 'fitted' or 'assumed'. Falling back
    to the assumed factor keeps the 'uncalibrated' badge on; a fitted factor is
    what retires it.
    """
    from .. import config as _cfg
    assumed = (float(_cfg.RETAIL_SCALE_FACTOR), "assumed")
    o = _wholesaler_otc(fin)
    if o is None or grouped is None or grouped.empty:
        return assumed
    # need BOTH tiers published for a week to be complete (T1 ~2wk lag, T2 ~4wk)
    both_tiers = o.groupby("week")["tierIdentifier"].agg(lambda t: {"T1", "T2"} <= set(t))
    weeks = both_tiers[both_tiers].index
    if len(weeks) < _cfg.RETAIL_FIT_MIN_WEEKS:
        return assumed
    g = grouped.copy()
    g["date"] = pd.to_datetime(g["date"])
    g["week"] = g["date"] + pd.to_timedelta(4 - g["date"].dt.weekday, unit="D")
    g["notional"] = g["close"] * g["volume"]
    px = g.groupby("week")["notional"].sum() / g.groupby("week")["volume"].sum()
    fin_sh = o[o["week"].isin(weeks)].groupby("week")["totalWeeklyShareQuantity"].sum()
    fin_usd = (fin_sh * px.reindex(fin_sh.index)).dropna()
    ours = day.set_index("date")["ident_usd"].resample("W-FRI").agg(["sum", "count"])
    ours = ours[ours["count"] >= 3]["sum"]
    ov = fin_usd.index.intersection(ours.index)
    if len(ov) < _cfg.RETAIL_FIT_MIN_WEEKS:
        return assumed
    ratio = (fin_usd.loc[ov] / ours.loc[ov]).replace([float("inf")], pd.NA).dropna()
    if ratio.empty or not (0 < float(ratio.mean()) < 100):
        return assumed
    return float(ratio.mean()), "fitted"


def _version_guard() -> str | None:
    """Refuse to build while the lake mixes classifier methodologies.

    A reprocess rewrites history day by day, so for a couple of hours the lake
    legitimately holds both the old and new methodology. Rendering across that
    boundary splices two different definitions into one chart — the exact
    failure this whole change exists to prevent. Returns a reason string when
    the panel must stay dark, None when the lake is uniform.
    """
    import os
    from .. import config as _cfg
    tdir = os.path.join(store.LAKE_DIR, massive.RETAIL_TABLE)
    if not os.path.isdir(tdir):
        return None
    seen: dict[int, int] = {}
    for f in os.listdir(tdir):
        if not f.endswith(".parquet"):
            continue
        p = os.path.join(tdir, f)
        if massive._is_holiday_marker(p):
            continue          # holidays carry no methodology
        v = massive._day_method_version(p)
        seen[v] = seen.get(v, 0) + 1
    if not seen:
        return None
    cur = _cfg.RETAIL_METHOD_VERSION
    if set(seen) == {cur}:
        return None
    return (f"lake holds mixed classifier versions {dict(sorted(seen.items()))} "
            f"(current={cur}) — retail panel withheld until the reprocess "
            f"completes; a spliced chart would mix two methodologies")


def build() -> dict[str, bool]:
    df = _daily()
    if df is None:
        return {"RF1": False, "RF2": False, "MH9": False}

    stale = _version_guard()
    if stale:
        store.log_run("compute:retail", "warn", stale)
        return dict(ALL_OFF)

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
    # FINRA + grouped are read once here: the capture factor is fitted against
    # them BEFORE any dollar metric uses it (was a hardcoded 3.0).
    fin = store.read_latest("finra_weekly_otc")
    grouped = massive.read_grouped()
    F, F_SRC = _fit_capture(day, fin, grouped)
    _cal = ({"level": "provisional", "label": f"x{F:.1f} fitted"} if F_SRC == "fitted"
            else {"level": "uncalibrated", "label": f"x{F:.1f} est. - uncalibrated"})
    store.log_run("compute:retail", "detail",
                  f"capture factor {F:.2f} ({F_SRC}) vs FINRA retail wholesalers")
    # arithmetic-impossibility alarm — see _ceiling_breaches
    breaches = _ceiling_breaches(df, F)
    latest_breach = breaches[breaches["date"].astype(str) == asof]
    if not breaches.empty:
        worst = "; ".join(
            f"{r.date} {r.ticker} {r.ceiling_ratio * 100:.0f}% of tape"
            for r in breaches.head(5).itertuples())
        store.log_run("retail:RF1", "warn",
                      f"{len(breaches)} symbol-day(s) breach the "
                      f"{CEILING_FRAC:.0%} consolidated-volume ceiling — "
                      f"non-retail contamination is leaking past the "
                      f"per-print cap. Worst: {worst}",
                      breaches=int(len(breaches)),
                      latest_day_breaches=int(len(latest_breach)))
    # ---- RF1: retail breadth ------------------------------------------------
    # Share of actively-traded names where retail was a net buyer. Replaced the
    # dollar net-flow series 2026-08 — see config.RETAIL_BREADTH_MIN_USD for why
    # a count beats a sum here. Scale-invariant, so NO ×3 factor and no
    # calibration badge; contamination in one name moves it by ~1/N.
    bmin = _cfg.RETAIL_BREADTH_MIN_USD
    _d = df.copy()
    _d["date"] = pd.to_datetime(_d["date"])
    _sig = set(day.loc[day["signing"] == "midpoint", "date"])
    act = _d[(_d["retail_usd"] >= bmin) & (_d["date"].isin(_sig))]
    rf1 = not act.empty
    if rf1:
        _g = act.groupby("date")
        bdf = pd.DataFrame({
            "value": _g.apply(lambda x: float((x["retail_net_usd"] > 0).mean() * 100.0),
                              include_groups=False),
            "names": _g.size(),
        }).reset_index().sort_values("date")
        s_rf1 = _display_series(bdf[["date", "value"]], "Retail breadth (% of names bought)",
                                unit="%")
        store.write_display("RF1", {
            "id": "RF1", "name": "Retail breadth — daily", "panel": "retail",
            "source": "Massive tape (classifier)", "cadence": "daily",
            "asof": asof, "unit": "%", "series": [s_rf1],
            "y_min": 0, "y_max": 100, "ref_line": 50.0,
            "tile": {"value": round(float(bdf["value"].iloc[-1]), 1), "delta": None,
                     "percentile": util.trailing_percentile(bdf["value"])},
            "provenance": "massive_tape",
            "status": ({"level": "suspect", "label": "contamination flag"}
                       if not latest_breach.empty else
                       {"level": "provisional", "label": "classifier"}),
            "tooltip": (
                f"Share of names with at least ${bmin/1e6:.0f}M of retail activity where "
                f"retail bought more than it sold. Above 50% = buying in more names than "
                f"selling. Latest reading covers {int(bdf['names'].iloc[-1]):,} names."
                + ("  NOTE: the latest day breaches the consolidated-volume ceiling in "
                   + ", ".join(latest_breach["ticker"].head(4))
                   + " — non-retail flow is leaking into the classifier."
                   if not latest_breach.empty else "")),
            "notes": (
                "**What it shows.** How broadly retail leaned one way. For every stock "
                "with meaningful retail activity we ask a single question — did retail "
                "buy more than it sold? — and plot the share that answer yes. Above 50% "
                "means retail was a net buyer in more names than it was a net seller; "
                "below 50% means the reverse. It is the retail equivalent of an "
                "advance/decline line: a measure of how many, not how much.\n\n"
                "**How it's computed.** Each trading day, every stock with at least "
                f"${bmin/1e6:.0f} million of identified retail dollars is scored as a net "
                "buy or a net sell using the quote-midpoint classifier described in "
                "\"Retail identification and scaling\" above. The reading is the "
                "percentage scored as net buys. The activity floor matters: without it "
                "the count fills with thousands of barely-traded small caps whose "
                "direction is close to a coin flip, which pins the line near 50% and "
                "hides the signal.\n\n"
                "**Caveats.** This deliberately answers *how many* rather than *how "
                "much* — it cannot tell you the dollars retail traded, and a day when "
                "retail buys a few names heavily while selling many lightly will read "
                "below 50%. For dollar volume see RF10, which is anchored to FINRA's "
                "reported figures. The measure is a count, so it carries no scale "
                "factor and needs no calibration, and contamination in any single name "
                "can move it by only about one name's worth — but it still rests on the "
                "classifier correctly identifying which trades are retail. Days carrying "
                "a contamination flag should not be read."
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
    fin_cut = None  # last week covered by an emitted FINRA-anchored series
    ours_w = wkdf2.set_index("date")["value"]
    otc = _wholesaler_otc(fin)
    if otc is not None and grouped is not None:
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
    # Fewer than one full week in the lake (a fresh or partial reprocess) leaves
    # nothing to tile off. Bail out with RF1/MH9 already written rather than
    # raise: build() runs under _safe(), so an IndexError here would take the
    # whole retail panel down with it — including RF1, which needs none of this.
    if done.empty:
        store.log_run("compute:retail", "warn",
                      "RF2/RF10/RF3/RF4 skipped — no complete week in the lake yet")
        return {"RF1": rf1, "RF2": False, "RF10": False, "MH9": False,
                "RF3": False, "RF4": False}
    store.write_display("RF2", {
        "id": "RF2", "name": "Retail participation — weekly (est. total)", "panel": "retail",
        "source": "FINRA weekly OTC × Massive tape (classifier)", "cadence": "weekly",
        "asof": asof, "unit": "%", "series": series2,
        "tile": {"value": round(float(done["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(done["value"], min_history=52)},
        "provenance": "finra+massive",
        "status": _cal,
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
    o = _wholesaler_otc(fin)
    if o is not None and grouped is not None:
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
        "status": _cal,
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

    # RF5 (avg retail trade size) removed 2026-07-24 (CIO cleanup — not helpful).
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
    rf4 = _build_rf4(bdf) if rf1 else False
    return {"RF1": rf1, "RF2": True, "RF10": True, "MH9": True,
            "RF3": rf3, "RF4": rf4}


def _roll_dipbuy_beta(ret: np.ndarray, net_b: np.ndarray, win: int) -> list[float]:
    """Rolling OLS slope of net flow ($B) on SPX % return over `win` trailing
    days, SIGN-FLIPPED so a positive value = net buying rises as the market
    falls (buying the dip). NaN until the window is full or returns are flat."""
    out: list[float] = []
    for i in range(len(net_b)):
        lo = i + 1 - win
        if lo < 0 or float(np.nanstd(ret[lo:i + 1])) == 0.0:
            out.append(float("nan"))
            continue
        out.append(-float(np.polyfit(ret[lo:i + 1], net_b[lo:i + 1], 1)[0]))
    return out


def _build_rf4(bdf: pd.DataFrame) -> bool:
    """RF4 buy-the-dip sensitivity: rolling OLS slope of daily retail BREADTH on
    the SPX daily % return, sign-flipped so a positive reading = retail buys into
    declines. Two windows — 63d (3-month, the primary trend) and 21d (1-month,
    faster but noisier context). Contemporaneous.

    Rebuilt on breadth 2026-08 (was net flow in $B). Two reasons. Breadth is a
    count, so the reading no longer inherits the classifier's dollar-scale
    problems. More importantly, the old input carried a bias that pointed the
    answer the wrong way: institutional de-risking clusters on down days, so
    block selling misread as retail selling arrived disproportionately when SPX
    fell, giving a positive slope and therefore a NEGATIVE RF4 — retail rendered
    as panic sellers on exactly the days the metric exists to describe. Breadth
    is near-immune (contamination in one name moves it by ~1/N)."""
    spx = store.read_latest("bbg_spx")
    if spx is None or len(bdf) < 63:
        return False
    spx = spx.copy()
    spx["date"] = pd.to_datetime(spx["date"])
    spx = spx.sort_values("date")                       # pct_change needs date order
    spx["ret"] = spx["value"].pct_change() * 100.0
    m = (bdf[["date", "value"]].merge(spx[["date", "ret"]], on="date", how="inner")
               .sort_values("date").dropna(subset=["ret"]))
    if len(m) < 63:
        return False
    ret, net_b = m["ret"].to_numpy(float), m["value"].to_numpy(float)
    m["b63"] = _roll_dipbuy_beta(ret, net_b, 63)
    m["b21"] = _roll_dipbuy_beta(ret, net_b, 21)
    d63 = m[["date", "b63"]].rename(columns={"b63": "value"}).dropna()
    d21 = m[["date", "b21"]].rename(columns={"b21": "value"}).dropna()
    if d63.empty:
        return False
    store.write_display("RF4", {
        "id": "RF4", "name": "Buy-the-dip sensitivity", "panel": "retail",
        "source": "Massive tape × BBG SPX", "cadence": "daily",
        "asof": d63["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " pts/1%",
        "series": [_display_series(d63, "3-month", role="avos", unit="pts/1%"),
                   _display_series(d21, "1-month", role="context", unit="pts/1%")],
        "tile": {"value": round(float(d63["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(d63["value"])},
        "provenance": "massive_tape",
        "status": {"level": "provisional", "label": "classifier"},
        "tooltip": "Breadth points gained per 1% SPX decline — higher = retail buys dips "
                   "across more names.",
        "notes": (
            "**What it shows.** How hard retail leans into weakness — how much wider "
            "retail buying spreads across the market for each 1% the S&P 500 falls. A "
            "positive, rising reading means retail buys into more names as the market "
            "drops (dip-buying); a negative reading means retail retreats as it falls.\n\n"
            "**How it's computed.** A rolling ordinary-least-squares slope of daily "
            "retail breadth (RF1, in percentage points) against the S&P 500 daily percent "
            "return, sign-flipped so a positive value means buying into declines: "
            "`RF4 = −slope(breadth on SPX % return)`. Two contemporaneous windows are "
            "shown — a 63-day (3-month) primary trend and a 21-day (1-month) context line "
            "that reacts faster but is roughly 4× noisier.\n\n"
            "**Caveats.** Until August 2026 this was built on retail net flow in dollars, "
            "which carried a bias that pointed the answer the wrong way: institutional "
            "selling misidentified as retail arrives disproportionately on down days, so "
            "the metric read retail as selling into declines on exactly the days it "
            "exists to describe. Breadth is a count across names and is nearly immune to "
            "that. It still unlocks only once 63 signed days carrying an S&P 500 return "
            "have accumulated, and it measures how *widely* retail buys, not how much — "
            "a day when retail buys a few names very heavily registers weakly here."
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
