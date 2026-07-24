"""retail_series.py — Panel 3 + MH9 computes from the daily classifier
aggregates (massive.RETAIL_TABLE). §5.1 floor/trend caveat renders on every
tile. These soft-skip until the first process_tape_day run lands.

RF1  net retail flow ($, daily)        RF2  retail participation (of tape $)
RF5  avg retail trade size             MH9  off-exchange + odd-lot share
RF3/RF4 (concentration, buy-the-dip) land after the first real tape days —
they join memberships and SPX returns onto the same aggregates.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import store, util
from ..pull import massive
from .ownership import _display_series

FLOOR_NOTE = ("Quote-midpoint classifier (§5.1) identifies roughly a third of retail "
              "trades; gated uncalibrated until RF9 >= 0.6.")
SCALE_NOTE = ("Dollar values scaled ×{f:.0f} from the identified floor to estimated "
              "total retail; factor provisional until RF9 fits it empirically.")


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
              tooltip=None, status=None, shared_note=True):
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
            "notes": ((FLOOR_NOTE + " " + note) if shared_note else note).strip(),
        }
        if tooltip:
            payload["tooltip"] = tooltip
        if status:
            payload["status"] = status
        store.write_display(mid, payload)

    from .. import config as _cfg
    F = _cfg.RETAIL_SCALE_FACTOR
    scale_note = SCALE_NOTE.format(f=F)
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
            "notes": FLOOR_NOTE + " " + scale_note,
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
        "notes": FLOOR_NOTE + " Splice: FINRA T1+T2 non-ATS weekly volume (OTCE excluded) "
                 "rescaled onto our definition by k = mean(ours/FINRA) over overlap weeks — "
                 "FINRA per-firm rows count both sides of trades, ~2x retail. T1-only weeks "
                 "in FINRA's publication lag are rescaled separately; our ×3 classifier "
                 "estimate covers weeks FINRA has not published.",
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
        "notes": FLOOR_NOTE + " Gross dollar volume, not net direction — FINRA weekly OTC has no "
                 "buy/sell split, so RF1 remains the net view. History = FINRA T1+T2 non-ATS share "
                 "volume × weekly tape $/share, rescaled onto our estimated-total definition by "
                 "k = mean(ours/FINRA) over overlap weeks (FINRA per-firm rows count both sides, "
                 "~2×); recent weeks in FINRA's publication lag are the ×3 classifier estimate.",
    })

    day["avg_size"] = (day["ident_usd"] / day["ident_trades"].clip(lower=1))
    _emit("RF5", "Avg retail trade size", "retail",
          [("avg_size", "Identified retail $ / trade", "avos")], "avg_size", " $", 0,
          bars=True, tooltip="Average dollar size of an identified retail trade.",
          note="Identified prints only — the ratio is invariant to the ×3 scale factor.")

    # OP8 (MOC auction share) killed 2026-07-10 — not interesting (CIO).
    day["offexch_pct"] = day["offexch"] / day["tape_vol"] * 100.0
    day["oddlot_pct"] = day["oddlot"] / day["tape_trades"] * 100.0
    _emit("MH9", "Off-exchange + odd-lot share", "health",
          [("offexch_pct", "TRF share of volume (%)", "avos", "bar", "% of volume"),
           ("oddlot_pct", "Odd-lot share of trades (%)", "context", "line", "% of trades")],
          "offexch_pct", "%", shared_note=False,
          tooltip="Share of volume printed off-exchange (bars, left) and share of trades "
                  "under 100 shares (line, right).",
          note="Off-exchange = FINRA TRF prints / total volume; odd-lot = trades <100sh.")

    rf3 = _build_rf3(df, F)
    rf4 = _build_rf4(day)
    return {"RF1": rf1, "RF2": True, "RF10": True, "RF5": True, "MH9": True,
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


def _build_rf4(day: pd.DataFrame) -> bool:
    """RF4 buy-the-dip sensitivity: rolling OLS slope of daily retail net flow
    ($B, identified floor) on the SPX daily % return, sign-flipped so a positive
    reading = retail buys into declines. Two windows — 63d (3-month, the primary
    trend) and 21d (1-month, faster but noisier context). Contemporaneous; the
    shape is scale-invariant. Unlocks once >=63 midpoint-signed days carrying an
    SPX return have accumulated (the 63d window fills)."""
    signed = day[day["signing"] == "midpoint"].copy()
    spx = store.read_latest("bbg_spx")
    if spx is None or len(signed) < 63:
        return False
    spx = spx.copy()
    spx["date"] = pd.to_datetime(spx["date"])
    spx = spx.sort_values("date")                       # pct_change needs date order
    spx["ret"] = spx["value"].pct_change() * 100.0
    m = (signed.merge(spx[["date", "ret"]], on="date", how="inner")
               .sort_values("date").dropna(subset=["ret"]))
    if len(m) < 63:
        return False
    m["net_b"] = m["net"] / 1e9
    ret, net_b = m["ret"].to_numpy(float), m["net_b"].to_numpy(float)
    m["b63"] = _roll_dipbuy_beta(ret, net_b, 63)
    m["b21"] = _roll_dipbuy_beta(ret, net_b, 21)
    d63 = m[["date", "b63"]].rename(columns={"b63": "value"}).dropna()
    d21 = m[["date", "b21"]].rename(columns={"b21": "value"}).dropna()
    if d63.empty:
        return False
    store.write_display("RF4", {
        "id": "RF4", "name": "Buy-the-dip sensitivity", "panel": "retail",
        "source": "Massive tape × BBG SPX", "cadence": "daily",
        "asof": d63["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " $B/1%",
        "series": [_display_series(d63, "3-month (63d) — trend", role="avos", unit="$B/1%"),
                   _display_series(d21, "1-month (21d) — faster, noisier",
                                   role="context", unit="$B/1%")],
        "tile": {"value": round(float(d63["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(d63["value"])},
        "provenance": "massive_tape",
        "status": {"level": "provisional", "label": "classifier floor"},
        "tooltip": "Net retail $B bought per 1% SPX decline — higher = retail buys dips harder.",
        "notes": FLOOR_NOTE + " Rolling OLS slope of daily identified retail net flow ($B, "
                 "unscaled floor) regressed on the SPX daily % return, sign-flipped so a "
                 "positive value = buying into declines. Solid line is the 63d (3-month) "
                 "trend; faint line the 21d (1-month) window — faster but ~4× noisier. "
                 "Contemporaneous; identified floor, not ×3-scaled.",
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
        "notes": FLOOR_NOTE + f" Bars ×{scale:.0f}-scaled to est. total $; shares use "
                 "identified $ only; membership = latest BBG SPX snapshot.",
    })
    return True
