"""opra.py — Phase-3 computes.

From OPRA trades aggregates: LV3 (DTE buckets, Retail panel),
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


def _opra() -> pd.DataFrame | None:
    df = massive.read_opra_daily()
    if df is None or df.empty:
        return None
    # holiday placeholder rows produce 0/0=NaN points on ~10 market holidays
    df = df[df["underlying"] != "_HOLIDAY_"]
    return None if df.empty else df


def build_lv3() -> dict[str, bool]:
    # LV2 (0DTE share, whole-market) dropped 2026-07-10 — redundant given LV3's
    # 0DTE bucket. LV3 moved to the Retail panel same day (retail short-dated mix).
    df = _opra()
    if df is None:
        return {"LV3": False}
    day = df.groupby("date")[["contracts", "c_0dte", "c_1_5", "c_6_30", "c_over30"]].sum()
    day.index = pd.to_datetime(day.index)
    day = day[day["contracts"] > 0]
    asof = day.index[-1].strftime("%Y-%m-%d")

    # LV3 stacked weekly bars (CIO 2026-07-10) — buckets sum to 100 per week
    series = []
    for col, label in (("c_0dte", "0 DTE"), ("c_1_5", "1–5"), ("c_6_30", "6–30"), ("c_over30", ">30")):
        wk = (day[col] / day["contracts"] * 100.0).resample("W-FRI").mean().dropna()
        sd = _display_series(wk.rename("value").reset_index(), f"{label} days",
                             role="context", unit="%", ds="none")
        sd["kind"], sd["stack"] = "bar", True
        series.append(sd)
    store.write_display("LV3", {
        "id": "LV3", "name": "Volume by DTE bucket", "panel": "retail",
        "source": "Massive OPRA trades", "cadence": "daily", "asof": asof,
        "unit": "% (tile: 0DTE)", "series": series,
        "tile": {"value": round(float((day["c_0dte"] / day["contracts"]).iloc[-1] * 100), 1),
                 "delta": None, "percentile": None},
        "provenance": "massive_opra",
        "tooltip": "Composition of option volume by days-to-expiry — weekly average, "
                   "stacks to 100%.",
        "notes": (
            "**What it shows.** How option volume splits by time to expiry — same-day "
            "(0 DTE), 1–5, 6–30, and more than 30 days — as weekly stacked bars that sum "
            "to 100%. A growing 0 DTE stack is the signature of short-dated, "
            "retail-heavy speculation.\n\n"
            "**How it's computed.** Each day, contract volume is sorted into the four "
            "days-to-expiry buckets; each bucket's daily share of total volume is then "
            "averaged over the Friday-ended week. The tile shows the latest 0 DTE "
            "share.\n\n"
            "**Caveats.** This is whole-market OPRA volume, not retail-only, and the "
            "shares are of contract count, not premium dollars."
        ),
    })
    return {"LV3": True}


def build_rf78() -> dict[str, bool]:
    df = _opra()
    if df is None:
        return {"RF7": False, "RF8": False}
    day = df.groupby("date")[["premium", "smalllot_prem", "smalllot_call_prem"]].sum()
    day.index = pd.to_datetime(day.index)
    # zero prints are feed artifacts, not $0 of premium — null them out rather
    # than drawing the line to zero (CIO 2026-07-10); NaNs drop from display
    day.loc[(day["premium"] <= 0) | (day["smalllot_prem"] <= 0),
            ["premium", "smalllot_prem", "smalllot_call_prem"]] = float("nan")
    asof = day.index[-1].strftime("%Y-%m-%d")

    # Small-lot daily series saw hard (thin small-lot tape + the weekly-expiry
    # cycle), so smooth on a 5-trading-day (1wk) trailing mean — same house
    # 5-day house smoothing pattern. min_periods=2 skips over the nulled feed days.
    def _sm(s: pd.Series) -> pd.Series:
        return s.rolling(5, min_periods=2).mean()

    prem = _sm(day["smalllot_prem"] / 1e9)
    share = _sm(day["smalllot_prem"] / day["premium"] * 100.0)
    store.write_display("RF7", {
        "id": "RF7", "name": "Small-lot options premium (proxy)", "panel": "retail",
        "source": "Massive OPRA trades", "cadence": "daily", "asof": asof,
        "unit": " $B (tile)", "series": [
            _display_series(prem.rename("value").reset_index(),
                            "Small-lot premium — 5d avg ($B/day)", unit="$B"),
            _display_series(share.rename("value").reset_index(),
                            "Share of all premium — 5d avg (%)", role="context", unit="%")],
        "tile": {"value": round(float(prem.dropna().iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(prem.dropna())},
        "provenance": "massive_opra",
        "tooltip": "Small-lot (<10 contract) option premium per day (retail proxy), "
                   "smoothed on a 5-day trailing average.",
        "notes": (
            "**What it shows.** The dollar premium spent on small (fewer than 10 "
            "contracts) option trades each day — a proxy for retail options activity — "
            "with that small-lot premium also shown as a share of all option premium. A "
            "rising line means retail is leaning harder into options.\n\n"
            "**How it's computed.** Small-lot premium is summed each day and smoothed "
            "with a 5-day trailing mean; the share is small-lot premium ÷ total option "
            "premium. The under-10-contract cutoff is the retail proxy described in "
            "Small-lot options proxy above.\n\n"
            "**Caveats.** This is a proxy — an observed regularity, not a positive "
            "identification of retail. Days when the feed reports zero premium are drawn "
            "as gaps rather than dropped to zero, since they are feed artifacts, not "
            "genuine days of no activity."
        ),
    })

    callsh = _sm(day["smalllot_call_prem"] / day["smalllot_prem"] * 100.0)
    semis = df[df["underlying"].isin(set(config.SEMI_TOP10))]
    semi_p = semis.groupby("date")["smalllot_prem"].sum() / 1e9
    semi_p.index = pd.to_datetime(semi_p.index)
    semi_p = _sm(semi_p.sort_index())
    store.write_display("RF8", {
        "id": "RF8", "name": "Small-lot call share / semi premium", "panel": "retail",
        "source": "Massive OPRA trades", "cadence": "daily", "asof": asof,
        "unit": "% (tile: call share)", "series": [
            _display_series(callsh.rename("value").reset_index(),
                            "Call share of small-lot premium — 5d avg (%)", unit="%"),
            _display_series(semi_p.rename("value").reset_index(),
                            "Semi small-lot premium — 5d avg ($B)", role="benchmark", unit="$B")],
        "tile": {"value": round(float(callsh.dropna().iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(callsh.dropna())},
        "provenance": "massive_opra",
        "tooltip": "Call share of small-lot premium (5-day average); second line = "
                   "small-lot premium in semis.",
        "notes": (
            "**What it shows.** How much of retail's small-lot option spending goes to "
            "calls versus puts — the call share is a directional, speculative read — "
            "shown alongside the small-lot premium spent in semiconductors, a "
            "perennial retail favorite.\n\n"
            "**How it's computed.** Call share is small-lot call premium ÷ small-lot "
            "total premium; the semis line is small-lot premium summed across the top "
            "semiconductor names. Both are 5-day trailing means. The under-10-contract "
            "cutoff is the retail proxy described in Small-lot options proxy above.\n\n"
            "**Caveats.** A proxy, not an identification of retail flow."
        ),
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
        # live extremes carried as data (LVT table row), NOT baked into prose
        "extremes": {k: round(float(v), 1) for k, v in top.items()},
        "status": {"level": "provisional", "label": "OI-convention"},
        "tooltip": "Dealer gamma exposure estimate — changes are the signal, not levels.",
        "notes": (
            "**What it shows.** An estimate of dealer gamma exposure — how much dealers "
            "must buy or sell to stay delta-hedged as the market moves. Large negative "
            "readings imply dealers amplify moves (selling into falls); positive "
            "readings imply they dampen them. The direction of change matters more than "
            "the absolute level.\n\n"
            "**How it's computed.** For each option, `GEX = sign · γ · OI · 100 · S² · "
            "0.01`, where `γ` is gamma, `OI` open interest, `S` the underlying price, "
            "`100` the contract multiplier, and `sign` is +1 for calls and −1 for puts "
            "(the open-interest convention, taking dealers long calls and short puts). "
            "These are summed across the universe and expressed in billions of dollars "
            "per 1% move. The universe is roughly 32 names — the retail top-25 plus "
            "index majors and semiconductors.\n\n"
            "**Caveats.** This is the open-interest estimator, inferring dealer "
            "positioning from OI rather than signed order flow — hence the "
            "*OI-convention* badge. Read the trend, not the level."
        ),
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
        "status": {"level": "provisional", "label": "snapshot universe"},
        "tooltip": "Positive = upside call wings bid vs ATM (call-demand regime).",
        "notes": (
            "**What it shows.** How expensive upside calls are relative to "
            "at-the-money options. Positive means the call wing is bid up versus ATM — "
            "a call-demand, upside-chasing regime; near zero or negative means that "
            "demand has faded.\n\n"
            "**How it's computed.** For each name we take `25Δ call IV − ATM IV` at "
            "20–45 day expiry — the implied vol of the roughly 0.25-delta call minus "
            "the roughly 0.50-delta (ATM) call — then volume-weight across the ~32-name "
            "snapshot universe. Names where those deltas can't be matched closely are "
            "dropped so a bad strike can't skew the composite.\n\n"
            "**Caveats.** Computed on the snapshot universe (hence the badge). The "
            "breadth companion — the share of S&P 500 members with an inverted call "
            "skew — arrives with a later member-breadth extension."
        ),
    })
    return True


def build() -> dict[str, bool]:
    # LV9 (single-name synthetic financing) + the LVT snapshot table removed
    # 2026-07-24 (CIO cleanup). LV5/LV10 keep computing so their history accrues.
    out = build_lv3()
    out.update(build_rf78())
    out.update({"LV5": build_lv5(), "LV10": build_lv10()})
    return out
