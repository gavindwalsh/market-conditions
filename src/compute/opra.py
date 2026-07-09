"""opra.py — Phase-3 computes.

From OPRA trades aggregates: LV2 (0DTE whole-market), LV3 (DTE buckets),
RF7/RF8 (small-lot premium — §5.2 proxy, labeled).
From EOD snapshots (server-side IV/greeks): LV5 OI-convention GEX (§5.3
revised), LV10 call-wing richness, LV9 synthetic financing vs SOFR (§5.5,
no per-name dividend adjustment in v1 — labeled).
VC4 member-breadth extension = same snapshot loop over members (itemized).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, store, util
from ..pull import massive
from .ownership import _display_series

PROXY_NOTE = "Small-lot (<10 contracts) = retail PROXY (§5.2) — observed regularity, not identification."


def _opra() -> pd.DataFrame | None:
    df = massive.read_opra_daily()
    return None if df is None or df.empty else df


def build_lv2_lv3() -> dict[str, bool]:
    df = _opra()
    if df is None:
        return {"LV2": False, "LV3": False}
    day = df.groupby("date")[["contracts", "c_0dte", "c_1_5", "c_6_30", "c_over30"]].sum()
    day.index = pd.to_datetime(day.index)
    asof = day.index[-1].strftime("%Y-%m-%d")

    v = (day["c_0dte"] / day["contracts"] * 100.0).rename("value").reset_index()
    spx = df[df["underlying"].isin(["SPX", "SPXW"])].groupby("date")[["contracts", "c_0dte"]].sum()
    spx.index = pd.to_datetime(spx.index)
    sv = (spx["c_0dte"] / spx["contracts"] * 100.0).rename("value").reset_index()
    store.write_display("LV2", {
        "id": "LV2", "name": "0DTE share — whole market", "panel": "leverage",
        "source": "Massive OPRA trades", "cadence": "daily", "asof": asof, "unit": "%",
        "series": [_display_series(v, "All underlyings 0DTE share"),
                   _display_series(sv, "SPX complex (LV1 cross-check)", role="benchmark")],
        "tile": {"value": round(float(v["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(v["value"])},
        "provenance": "massive_opra",
        "notes": "Volume where expiry = trade date. SPX-complex series doubles as the "
                 "§7.3 LV1 (Cboe) cross-check once LV1 is wired.",
    })

    series = []
    for col, label in (("c_0dte", "0 DTE"), ("c_1_5", "1–5"), ("c_6_30", "6–30"), ("c_over30", ">30")):
        raw = day[col] / day["contracts"] * 100.0
        s = raw.rolling(5, min_periods=2).mean().rename("value").reset_index()
        series.append(_display_series(s, f"{label} days (5d avg)",
                                      role="avos" if col == "c_0dte" else "context"))
    store.write_display("LV3", {
        "id": "LV3", "name": "Volume by DTE bucket", "panel": "leverage",
        "source": "Massive OPRA trades", "cadence": "daily", "asof": asof,
        "unit": "% (tile: 0DTE)", "series": series,
        "tile": {"value": round(float((day["c_0dte"] / day["contracts"]).iloc[-1] * 100), 1),
                 "delta": None, "percentile": None},
        "provenance": "massive_opra", "notes": "Share of contract volume by days-to-expiry, 5-day rolling average (raw daily is choppy — expiry cycles).",
    })
    return {"LV2": True, "LV3": True}


def build_rf78() -> dict[str, bool]:
    df = _opra()
    if df is None:
        return {"RF7": False, "RF8": False}
    day = df.groupby("date")[["premium", "smalllot_prem", "smalllot_call_prem"]].sum()
    day.index = pd.to_datetime(day.index)
    asof = day.index[-1].strftime("%Y-%m-%d")

    v = (day["smalllot_prem"] / 1e9).rename("value").reset_index()
    share = (day["smalllot_prem"] / day["premium"] * 100.0).rename("value").reset_index()
    store.write_display("RF7", {
        "id": "RF7", "name": "Small-lot options premium (proxy)", "panel": "retail",
        "source": "Massive OPRA trades", "cadence": "daily", "asof": asof,
        "unit": " $B (tile)", "series": [
            _display_series(v, "Small-lot premium ($B/day)", unit="$B"),
            _display_series(share, "Share of all premium (%)", role="context", unit="%")],
        "tile": {"value": round(float(v["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(v["value"])},
        "provenance": "massive_opra", "notes": PROXY_NOTE,
    })

    callsh = (day["smalllot_call_prem"] / day["smalllot_prem"] * 100.0).rename("value").reset_index()
    semis = df[df["underlying"].isin(set(config.SEMI_TOP10))]
    semi_p = (semis.groupby("date")["smalllot_prem"].sum() / 1e9)
    semi_p.index = pd.to_datetime(semi_p.index)
    store.write_display("RF8", {
        "id": "RF8", "name": "Small-lot call share / semi premium", "panel": "retail",
        "source": "Massive OPRA trades", "cadence": "daily", "asof": asof,
        "unit": "% (tile: call share)", "series": [
            _display_series(callsh, "Call share of small-lot premium (%)", unit="%"),
            _display_series(semi_p.rename("value").reset_index(), "Semi small-lot premium ($B)",
                            role="benchmark", unit="$B")],
        "tile": {"value": round(float(callsh["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(callsh["value"])},
        "provenance": "massive_opra", "notes": PROXY_NOTE,
    })
    return {"RF7": True, "RF8": True}


def _snap_latest() -> pd.DataFrame | None:
    df = massive.read_snapshots()
    if df is None or df.empty:
        return None
    return df[df["date"] == df["date"].max()]


def build_lv5() -> bool:
    """LV5: OI-convention dealer GEX (§5.3 revised 2026-07-08)."""
    snap = _snap_latest()
    hist = massive.read_snapshots()
    if snap is None:
        return False
    s = snap.dropna(subset=["gamma", "oi", "und_price"]).copy()
    s["sign"] = np.where(s["cp"] == "call", 1.0, -1.0)
    s["gex"] = s["sign"] * s["gamma"] * s["oi"] * 100.0 * s["und_price"] ** 2 * 0.01 / 1e9
    per_u = s.groupby("underlying")["gex"].sum().sort_values()
    total_by_day = (hist.dropna(subset=["gamma", "oi", "und_price"])
                    .assign(sign=lambda d: np.where(d["cp"] == "call", 1.0, -1.0))
                    .assign(gex=lambda d: d["sign"] * d["gamma"] * d["oi"] * 100.0
                            * d["und_price"] ** 2 * 0.01 / 1e9)
                    .groupby("date")["gex"].sum())
    tdf = total_by_day.rename("value").reset_index()
    tdf["date"] = pd.to_datetime(tdf["date"])
    top = pd.concat([per_u.head(3), per_u.tail(3)])
    store.write_display("LV5", {
        "id": "LV5", "name": "Dealer GEX (OI-convention)", "panel": "leverage",
        "source": "Massive snapshots (OI×γ)", "cadence": "daily",
        "asof": str(snap["date"].iloc[0]), "unit": " $B/1%",
        "series": [_display_series(tdf, "Aggregate OI-convention GEX ($B per 1%)")],
        "tile": {"value": round(float(tdf["value"].iloc[-1]), 1), "delta": None,
                 "percentile": None},
        "provenance": "massive_snapshot",
        "notes": "OI-convention estimator (dealers +γ calls / −γ puts) — §5.3 revised; "
                 "levels are estimates, CHANGES are the signal. Extremes today: "
                 + ", ".join(f"{k} {v:+.1f}" for k, v in top.items())
                 + ". Universe = retail top-25 + majors + semis (32 names).",
    })
    return True


def build_lv10() -> bool:
    """LV10: call-wing richness — 25Δ call IV − ATM IV per name (nearest 20-45d
    expiry), option-volume-weighted composite across the snapshot universe."""
    snap = _snap_latest()
    hist = massive.read_snapshots()
    if snap is None:
        return False

    def name_richness(g: pd.DataFrame) -> float | None:
        g = g.dropna(subset=["iv", "delta", "expiry"])
        g = g[g["cp"] == "call"]
        if g.empty:
            return None
        g = g.copy()
        g["dte"] = (pd.to_datetime(g["expiry"]) - pd.to_datetime(g["date"])).dt.days
        g = g[g["dte"].between(20, 45)]
        if g.empty:
            return None
        atm = g.loc[(g["delta"] - 0.50).abs().idxmin()]
        wing = g.loc[(g["delta"] - 0.25).abs().idxmin()]
        if abs(atm["delta"] - 0.5) > 0.15 or abs(wing["delta"] - 0.25) > 0.10:
            return None
        return float((wing["iv"] - atm["iv"]) * 100.0)

    rows = []
    for d, day_snap in hist.groupby("date"):
        vals, wts = [], []
        for u, g in day_snap.groupby("underlying"):
            rich = name_richness(g)
            vol = g["day_volume"].sum()
            if rich is not None and vol > 0:
                vals.append(rich); wts.append(vol)
        if vals:
            rows.append({"date": d, "value": float(np.average(vals, weights=wts))})
    df = pd.DataFrame(rows)
    if df.empty:
        return False
    df["date"] = pd.to_datetime(df["date"])
    store.write_display("LV10", {
        "id": "LV10", "name": "L2: Call-wing richness", "panel": "leverage",
        "source": "Massive snapshots", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " vol pts",
        "series": [_display_series(df, "25Δ call − ATM IV, volume-weighted (1M)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 2), "delta": None,
                 "percentile": None},
        "provenance": "massive_snapshot",
        "notes": "Positive = upside wings bid (call-demand regime, Citadel ch.20). "
                 "Universe = snapshot names (32); % SPX members inverted lands with "
                 "the member-breadth snapshot extension (itemized).",
    })
    return True


def build_lv9() -> bool:
    """LV9: single-name synthetic financing (§5.5) — put-call-parity implied
    forward from ATM C/P day closes, ~1M expiry; financing = ln(F/S)/T (no
    per-name dividend adj in v1 — labeled); spread vs SOFR, volume-weighted."""
    hist = massive.read_snapshots()
    f = store.read_latest("fred_sofr")
    if hist is None or f is None:
        return False
    sofr = float(f.sort_values("date")["value"].iloc[-1])

    rows = []
    for (d, u), g in hist.groupby(["date", "underlying"]):
        g = g.dropna(subset=["day_close", "strike", "expiry", "und_price"])
        if g.empty:
            continue
        S = float(g["und_price"].iloc[0])
        g = g.copy()
        g["dte"] = (pd.to_datetime(g["expiry"]) - pd.to_datetime(d)).dt.days
        g = g[g["dte"].between(20, 45)]
        if g.empty:
            continue
        exp = g.loc[(g["dte"] - 30).abs().idxmin(), "expiry"]
        ge = g[g["expiry"] == exp]
        strikes = ge[ge["cp"] == "call"].merge(
            ge[ge["cp"] == "put"], on="strike", suffixes=("_c", "_p"))
        if strikes.empty:
            continue
        strikes["k_dist"] = (strikes["strike"] - S).abs()
        atm = strikes.loc[strikes["k_dist"].idxmin()]
        T = float(atm["dte_c"]) / 365.0
        F = atm["strike"] + (atm["day_close_c"] - atm["day_close_p"]) * np.exp(sofr / 100 * T)
        if F <= 0 or S <= 0:
            continue
        fin = np.log(F / S) / T * 100.0
        vol = ge["day_volume"].sum()
        rows.append({"date": d, "underlying": u, "fin": fin, "vol": vol})
    if not rows:
        return False
    df = pd.DataFrame(rows)
    comp = df.groupby("date").apply(
        lambda g: np.average(g["fin"], weights=g["vol"].clip(lower=1)),
        include_groups=False).rename("value").reset_index()
    comp["date"] = pd.to_datetime(comp["date"])
    comp["value"] = (comp["value"] - sofr) * 100.0  # spread in bp
    # §7.4 sanity bound: parity from non-synchronous EOD closes must land within
    # a plausible financing band; outside it, suppress rather than ship.
    if abs(float(comp["value"].iloc[-1])) > 500:
        store.log_run("compute:LV9", "sanity_fail",
                      f"spread {comp['value'].iloc[-1]:.0f}bp outside ±500bp — suppressed; "
                      "needs synchronous quote pairing (itemized)")
        return False
    store.write_display("LV9", {
        "id": "LV9", "name": "L2: Single-name synthetic financing", "panel": "leverage",
        "source": "Massive snapshots (parity)", "cadence": "daily",
        "asof": comp["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " bp vs SOFR",
        "series": [_display_series(comp, "Volume-weighted implied financing − SOFR (bp)")],
        "tile": {"value": round(float(comp["value"].iloc[-1]), 0), "delta": None,
                 "percentile": None},
        "provenance": "massive_snapshot",
        "notes": "Parity forward from ATM C/P EOD closes (non-synchronous closes add "
                 "noise — labeled estimate); NO per-name dividend adjustment in v1 "
                 "(biases financing DOWN for dividend payers; retail favorites are "
                 "mostly low-div). Compare vs LV7 box for the demand premium (§5.5).",
    })
    return True


def build() -> dict[str, bool]:
    out = build_lv2_lv3()
    out.update(build_rf78())
    out.update({"LV5": build_lv5(), "LV10": build_lv10(), "LV9": build_lv9()})
    return out
