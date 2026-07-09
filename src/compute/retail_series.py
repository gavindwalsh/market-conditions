"""retail_series.py — Panel 3 + MH9 computes from the daily classifier
aggregates (massive.RETAIL_TABLE). §5.1 floor/trend caveat renders on every
tile. These soft-skip until the first process_tape_day run lands.

RF1  net retail flow ($) aggregate     RF2  retail participation (of tape $)
RF5  avg retail trade size             MH9  off-exchange + odd-lot share
RF3/RF4 (concentration, buy-the-dip) land after the first real tape days —
they join memberships and SPX returns onto the same aggregates.
"""
from __future__ import annotations

import pandas as pd

from .. import store, util
from ..pull import massive
from .ownership import _display_series

FLOOR_NOTE = ("Quote-midpoint classifier (§5.1): identifies ~1/3 of retail trades. "
              "Gated 'uncalibrated' until RF9 >= 0.6 (§7.2).")
SCALE_NOTE = ("SCALED x{f:.1f} to estimated TOTAL retail (identified floor x {f:.1f}; "
              "factor from the BHJOS ~1/3 capture rate, confirmed vs ~20% consensus "
              "participation; provisional until RF9 fits it empirically). Unscaled "
              "floor = shown value / {f:.1f}.")


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

    def _emit(mid, name, panel, series_defs, tile_col, unit, fmt=2, extra_note="", bars=False):
        series = []
        for c, label, role in series_defs:
            sd = _display_series(day[["date", c]].rename(columns={c: "value"}), label, role=role)
            if bars:
                sd["kind"] = "bar"
            series.append(sd)
        store.write_display(mid, {
            "id": mid, "name": name, "panel": panel,
            "source": "Massive SIP tape (classifier)", "cadence": "daily",
            "asof": asof, "unit": unit, "series": series,
            "tile": {"value": round(float(day[tile_col].iloc[-1]), fmt), "delta": None,
                     "percentile": util.trailing_percentile(day[tile_col])},
            "provenance": "massive_tape", "notes": (FLOOR_NOTE + " " + extra_note).strip(),
        })

    from .. import config as _cfg
    F = _cfg.RETAIL_SCALE_FACTOR
    scale_note = SCALE_NOTE.format(f=F)
    signed = day[day["signing"] == "midpoint"].copy()
    rf1 = not signed.empty
    if rf1:
        signed["net_b"] = signed["net"] * F / 1e9
        wk = signed.set_index("date")["net_b"].resample("W-FRI").agg(["sum", "count"])
        wk = wk[wk["count"] >= 3]  # only weeks with >=3 signed days
        wkdf = wk["sum"].rename("value").reset_index()
        s_wk = _display_series(wkdf, "Weekly est. total retail net flow ($B)", unit="$B")
        s_wk["kind"] = "bar"
        store.write_display("RF1", {
            "id": "RF1", "name": "Retail net flow — weekly (est. total)", "panel": "retail",
            "source": "Massive tape (classifier)", "cadence": "weekly",
            "asof": asof, "unit": " $B/wk", "series": [s_wk],
            "tile": {"value": round(float(wkdf["value"].iloc[-1]), 1), "delta": None,
                     "percentile": util.trailing_percentile(wkdf["value"], min_history=52)},
            "provenance": "massive_tape",
            "notes": FLOOR_NOTE + " " + scale_note + " Weekly sum of midpoint-signed "
                     "daily net flow; weeks with <3 signed days omitted. NOTE: no "
                     "official retail NET flow series exists (FINRA OTC data is "
                     "unsigned volume) — this is our estimate at weekly cadence. "
                     "Daily granularity: RF1D.",
        })
        day.loc[signed.index, "net_b"] = signed["net_b"]
        _emit("RF1D", "Retail net flow — daily (est. total)", "retail",
              [("net_b", "Est. total retail net flow ($B/day)", "avos")], "net_b", " $B",
              extra_note=scale_note + " Quote-midpoint signed days only.", bars=True)
    # RF2: weekly headline (FINRA official anchor pending dataset relocation —
    # their weeklySummary VOL_STATS series ended 2023-11) + RF2D daily bars
    day["particip"] = day["ident_usd"] / day["tape_usd"] * 100.0 * F
    wk2 = day.set_index("date")["particip"].resample("W-FRI").agg(["mean", "count"])
    wk2 = wk2[wk2["count"] >= 3]
    wkdf2 = wk2["mean"].rename("value").reset_index()
    # FINRA splice (CIO 2026-07-09): official non-ATS weekly volume share as
    # the anchor; our estimate extends the ~2-3wk publication lag (lighter bars)
    series2 = []
    fin = store.read_latest("finra_weekly_otc")
    grouped = massive.read_grouped()
    last_fin_week = None
    if fin is not None and grouped is not None:
        otc = fin[fin["summaryTypeCode"] == "OTC_W_FIRM"].copy()
        wk_otc = otc.groupby("weekStartDate")["totalWeeklyShareQuantity"].sum()
        g = grouped.copy()
        g["date"] = pd.to_datetime(g["date"])
        g["week"] = (g["date"] - pd.to_timedelta(g["date"].dt.weekday, unit="D"))
        wk_tot = g.groupby("week")["volume"].sum()
        wk_otc.index = pd.to_datetime(wk_otc.index)
        both = (wk_otc / wk_tot.reindex(wk_otc.index) * 100.0).dropna()
        if not both.empty:
            last_fin_week = both.index.max()
            fdf = both.rename("value").reset_index().rename(columns={"index": "week", "week": "date"})
            fdf.columns = ["date", "value"]
            s_fin = _display_series(fdf, "FINRA non-ATS share of volume (official)", unit="%")
            s_fin["kind"] = "bar"
            series2.append(s_fin)
    ext = wkdf2 if last_fin_week is None else wkdf2[wkdf2["date"] > last_fin_week]
    s_wk2 = _display_series(ext, "Our est. total participation (extension)",
                            role="nowcast", unit="%")
    s_wk2["kind"] = "bar"
    series2.append(s_wk2)
    # overlap comparison line (validation view, §7 spirit)
    if last_fin_week is not None:
        s_ours = _display_series(wkdf2, "Our estimate (full, context)", role="benchmark", unit="%")
        series2.append(s_ours)
    store.write_display("RF2", {
        "id": "RF2", "name": "Retail participation — weekly (est. total)", "panel": "retail",
        "source": "Massive tape (classifier)", "cadence": "weekly",
        "asof": asof, "unit": "%", "series": series2,
        "tile": {"value": round(float(wkdf2["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(wkdf2["value"], min_history=52)},
        "provenance": "finra+massive",
        "notes": FLOOR_NOTE + " " + scale_note + " SPLICED: solid bars = FINRA official "
                 "non-ATS (wholesaler) share of consolidated share volume, weekly, "
                 "~2-3wk publication lag; lighter bars = our est. total retail "
                 "participation extending the lag window. DEFINITIONAL SEAM: FINRA "
                 "counts ALL internalized share volume (retail-dominant but not pure); "
                 "ours is identified-retail dollars x3. The context line overlays our "
                 "estimate across the full window for calibration. Daily: RF2D.",
    })
    _emit("RF2D", "Retail participation — daily (est. total)", "retail",
          [("particip", "Est. total retail $ / tape $ (%)", "avos")], "particip", "%",
          extra_note=scale_note, bars=True)

    day["avg_size"] = (day["ident_usd"] / day["ident_trades"].clip(lower=1))
    _emit("RF5", "Avg retail trade size", "retail",
          [("avg_size", "Identified retail $ / trade", "avos")], "avg_size", " $", 0)

    day["moc_pct"] = day["moc"] / day["tape_vol"] * 100.0
    _emit("OP8", "MOC auction share", "ownership",
          [("moc_pct", "Closing-auction volume / total (%)", "avos")], "moc_pct", "%",
          extra_note="SIP conditions 8+19 (volume-updating closing prints); "
                     "official-close price prints (15) excluded.")

    day["offexch_pct"] = day["offexch"] / day["tape_vol"] * 100.0
    day["oddlot_pct"] = day["oddlot"] / day["tape_trades"] * 100.0
    _emit("MH9", "Off-exchange + odd-lot share", "health",
          [("offexch_pct", "TRF share of volume (%)", "avos"),
           ("oddlot_pct", "Odd-lot share of trades (%)", "context")],
          "offexch_pct", "%",
          extra_note="Off-exchange = FINRA TRF prints / total volume; odd-lot = trades <100sh.")

    rf3 = _build_rf3(df)
    rf4 = _build_rf4(day)
    return {"RF1": rf1, "RF2": True, "RF5": True, "MH9": True, "OP8": True,
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
        "notes": FLOOR_NOTE + " >1 = retail buys dips harder than its average day.",
    })
    return True


def _bbg_to_massive(t: str) -> str:
    return t.split(" ")[0].replace("/", ".")


def _build_rf3(df: pd.DataFrame) -> bool:
    """RF3: share of identified retail $ in the top-10 SPX names and in semis
    (§4). Leveraged-ETF slice lands with the OP7 fund screen."""
    members = store.read_all("bbg_spx_members")
    if members is None or members.empty:
        return False
    latest = members[members["date"] == members["date"].max()].copy()
    latest["sym"] = latest["ticker"].map(_bbg_to_massive)
    top10 = set(latest.nlargest(10, "weight")["sym"])
    semis = set(latest[latest["gics_industry"].fillna(0).astype(int) == 453010]["sym"])

    g = df.groupby("date").apply(lambda d: pd.Series({
        "top10_pct": d.loc[d["ticker"].isin(top10), "retail_ident_usd"].sum() / max(d["retail_ident_usd"].sum(), 1) * 100,
        "semi_pct": d.loc[d["ticker"].isin(semis), "retail_ident_usd"].sum() / max(d["retail_ident_usd"].sum(), 1) * 100,
    }), include_groups=False).reset_index()
    g["date"] = pd.to_datetime(g["date"])
    g = g.sort_values("date")

    store.write_display("RF3", {
        "id": "RF3", "name": "Retail concentration", "panel": "retail",
        "source": "Massive tape × SPX membership", "cadence": "daily",
        "asof": g["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "% (tile: top-10 share)",
        "series": [
            _display_series(g[["date", "top10_pct"]].rename(columns={"top10_pct": "value"}),
                            "Retail $ in top-10 SPX names (%)"),
            _display_series(g[["date", "semi_pct"]].rename(columns={"semi_pct": "value"}),
                            "Retail $ in semis (%)", role="context"),
        ],
        "tile": {"value": round(float(g["top10_pct"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(g["top10_pct"])},
        "provenance": "massive_tape",
        "notes": FLOOR_NOTE + " Membership = latest BBG snapshot; leveraged-ETF slice "
                              "lands with the OP7 fund screen.",
    })
    return True
