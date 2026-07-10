"""volatility.py — Panel 5 computes from BBG index histories (§4 VC1, VC3).

VC1  Implied correlation — COR1M level + percentile, COR3M alongside.
VC3  Vol term structure — VIX/VIX3M ratio (>1 = inverted, stress signal).
VC2/VC4/VC5/VC6 need member returns / OVDV / engine work — later per §8.
Also emits MH7 (cross-asset context) since it shares the same pull.
"""
from __future__ import annotations

import pandas as pd

from .. import store, util
from .ownership import _display_series


def _lake_series(mnemonic: str) -> pd.DataFrame | None:
    df = store.read_latest(f"bbg_{mnemonic}")
    if df is None or df.empty:
        return None
    df = df.sort_values("date")
    return df[["date", "value"]].assign(date=pd.to_datetime(df["date"]))


def build_vc1() -> bool:
    c1, c3 = _lake_series("cor1m"), _lake_series("cor3m")
    if c1 is None or c3 is None:
        return False
    store.write_display("VC1", {
        "id": "VC1", "name": "Implied correlation", "panel": "volatility",
        "source": "BBG COR1M/COR3M Index", "cadence": "daily",
        "asof": c1["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "",
        "series": [_display_series(c1, "COR1M (1-month implied corr)"),
                   _display_series(c3, "COR3M (3-month)", role="context")],
        "tile": {"value": round(float(c1["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(c1["value"])},
        "provenance": "bloomberg_cache",
        "tooltip": "Implied correlation — low means single-name moves dominate the index "
                   "(dispersion regime).",
        "notes": "Cboe implied correlation indices; tile ranks COR1M. The spread vs "
                 "realized correlation is VC2.",
    })
    return True


def build_vc3() -> bool:
    vix, v3m = _lake_series("vix"), _lake_series("vix3m")
    if vix is None or v3m is None:
        return False
    m = vix.rename(columns={"value": "vix"}).merge(
        v3m.rename(columns={"value": "vix3m"}), on="date", how="inner")
    m["value"] = m["vix"] / m["vix3m"]
    ratio = m[["date", "value"]]
    store.write_display("VC3", {
        "id": "VC3", "name": "Vol term structure (VIX/VIX3M)", "panel": "volatility",
        "source": "BBG VIX/VIX3M Index", "cadence": "daily",
        "asof": ratio["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "×",
        "series": [_display_series(ratio, "VIX ÷ VIX3M (>1 = inverted)")],
        "tile": {"value": round(float(ratio["value"].iloc[-1]), 3), "delta": None,
                 "percentile": util.trailing_percentile(ratio["value"])},
        "provenance": "bloomberg_cache",
        "tooltip": "VIX/VIX3M above 1 = stress inversion; low = carry-friendly contango.",
        "notes": "Daily close ratio of VIX to VIX3M. Per-name term-structure slopes for "
                 "top names are a noted extension.",
    })
    return True


def build_mh7() -> bool:
    # DXY dropped 2026-07-10: its 73-112 range flatlines under MOVE's 39-148
    # on the shared 'level' axis and it added the least as context
    parts = {m: _lake_series(m) for m in ("move", "ust10y", "ust2y")}
    if any(v is None for v in parts.values()):
        return False
    curve = parts["ust10y"].rename(columns={"value": "y10"}).merge(
        parts["ust2y"].rename(columns={"value": "y2"}), on="date", how="inner")
    curve["value"] = curve["y10"] - curve["y2"]  # pct-pts (right axis pairs with 10y %)
    move = parts["move"]
    store.write_display("MH7", {
        "id": "MH7", "name": "Cross-asset context", "panel": "internals",
        "source": "BBG MOVE/UST", "cadence": "daily",
        "asof": move["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " (tile: MOVE)",
        "series": [_display_series(move, "MOVE (rates vol)", unit="level"),
                   _display_series(parts["ust10y"], "UST 10y (%)", role="context", unit="%"),
                   _display_series(curve[["date", "value"]], "2s10s slope (pct-pts)", role="benchmark", unit="%")],
        "tile": {"value": round(float(move["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(move["value"])},
        "provenance": "bloomberg_cache",
        "tooltip": "Rates vol (left) with the 10-year yield and curve slope (right) — "
                   "the rates backdrop for equities.",
        "notes": "MOVE on its own level axis; UST 10y and the 2s10s slope (10y − 2y, "
                 "pct-pts) share the % axis. DXY dropped 2026-07-10.",
    })
    return True


def build_vc2() -> bool:
    """VC2: implied − realized correlation spread. Realized avg pairwise member
    correlation via the index-variance identity:
      rho = (sig_idx^2 - SUM w_i^2 sig_i^2) / ((SUM w_i sig_i)^2 - SUM w_i^2 sig_i^2)
    21d rolling, current weights (survivorship caveat as SC5)."""
    from ..pull import massive
    from .structure import _bbg_to_massive
    grouped = massive.read_grouped()
    members = store.read_all("bbg_spx_members")
    spx = _lake_series("spx")
    c1 = _lake_series("cor1m")
    if any(x is None or (hasattr(x, "empty") and x.empty) for x in (grouped, members, spx, c1)):
        return False
    latest = members[members["date"] == members["date"].max()].copy()
    latest = latest.sort_values("pulled_at").drop_duplicates("ticker", keep="last")
    latest["sym"] = latest["ticker"].map(_bbg_to_massive)
    w = latest.drop_duplicates("sym").set_index("sym")["weight"] / 100.0

    g = grouped[grouped["ticker"].isin(set(w.index))]
    wide = g.pivot_table(index="date", columns="ticker", values="close", aggfunc="last").sort_index()
    rets = wide.pct_change()
    sig = rets.rolling(21).std()                      # per-member daily vol
    w = w.reindex(sig.columns).fillna(0.0)
    sum_w2s2 = (sig ** 2).mul(w ** 2, axis=1).sum(axis=1)
    sum_ws = sig.mul(w, axis=1).sum(axis=1)

    spx_ret = spx.set_index("date")["value"].pct_change()
    sig_idx = spx_ret.rolling(21).std()
    sig_idx = sig_idx.reindex(pd.to_datetime(sig.index)).dropna()

    idx = sig_idx.index.intersection(pd.to_datetime(sum_ws.index))
    sum_ws.index = pd.to_datetime(sum_ws.index); sum_w2s2.index = pd.to_datetime(sum_w2s2.index)
    rho = ((sig_idx.loc[idx] ** 2 - sum_w2s2.loc[idx])
           / (sum_ws.loc[idx] ** 2 - sum_w2s2.loc[idx])).clip(-1, 1) * 100.0
    rho = rho.dropna()
    if rho.empty:
        return False
    spread = c1.set_index("date")["value"].reindex(rho.index) - rho
    df = spread.dropna().rename("value").reset_index().rename(columns={"index": "date"})
    realized = rho.rename("value").reset_index().rename(columns={"index": "date"})

    store.write_display("VC2", {
        "id": "VC2", "name": "Implied − realized correlation spread", "panel": "volatility",
        "source": "BBG COR1M − Massive member returns", "cadence": "daily",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " pts",
        "series": [_display_series(df, "COR1M − realized 21d avg pairwise corr"),
                   _display_series(realized, "Realized (21d)", role="context")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(df["value"])},
        "provenance": "derived",
        "tooltip": "Implied minus realized correlation — the premium dispersion sellers "
                   "are earning.",
        "notes": "Realized correlation from the index-variance identity over SPX members, "
                 "21d rolling, current membership applied backward.",
    })
    return True


def build_vc5() -> bool:
    """VC5: spot-up/vol-up frequency — % of NDX up-days where VXN also rose,
    monthly (Citadel ch.19). VXN = 1M NDX implied vol index."""
    ndx, vxn = _lake_series("ndx"), _lake_series("vxn")
    if ndx is None or vxn is None:
        return False
    m = ndx.rename(columns={"value": "ndx"}).merge(
        vxn.rename(columns={"value": "vxn"}), on="date", how="inner").sort_values("date")
    m["up_day"] = m["ndx"].pct_change() > 0
    m["vol_up"] = m["vxn"].diff() > 0
    m["month"] = m["date"].dt.to_period("M")
    mo = m[m["up_day"]].groupby("month").agg(
        n=("up_day", "size"), both=("vol_up", "sum")).reset_index()
    mo = mo[mo["n"] >= 5]
    mo["value"] = mo["both"] / mo["n"] * 100.0
    mo["date"] = mo["month"].dt.end_time.dt.normalize()
    df = mo[["date", "value"]]
    store.write_display("VC5", {
        "id": "VC5", "name": "Spot-up / vol-up frequency (NDX)", "panel": "volatility",
        "source": "BBG NDX + VXN", "cadence": "daily (monthly agg)",
        "asof": df["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": "%",
        "series": [_display_series(df, "% of NDX up-days with VXN up (monthly)")],
        "tile": {"value": round(float(df["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(df["value"], min_history=12)},
        "provenance": "bloomberg_cache",
        "notes": "Elevated spot-up/vol-up marks call-demand-driven tape (dealers short "
                 "upside). Monthly aggregation of daily joint moves; months with <5 "
                 "up-days dropped. Current month is partial until month-end.",
    })
    return True


def build_vc4() -> bool:
    """VC4 (Phase-1 slice): SPX 30d put skew (90%mny − ATM) + semi call
    richness (110%mny − ATM avg across top semis). Moneyness-based — the §4
    delta-based fields are OVDV-only, dropped under Massive-first (§4.0).
    Member-inverted-skew breadth is Phase 3 (OPRA snapshots)."""
    put_w = _lake_series("spx_iv_put_wing")
    atm = _lake_series("spx_iv_atm")
    if put_w is None or atm is None:
        return False
    skew = put_w.rename(columns={"value": "pw"}).merge(
        atm.rename(columns={"value": "atm"}), on="date")
    skew["value"] = skew["pw"] - skew["atm"]
    from .. import config
    semi_rich = []
    for t in config.SEMI_TOP10:
        c = store.read_latest(f"bbg_iv_call30_{t.lower()}")
        a = store.read_latest(f"bbg_iv_atm30_{t.lower()}")
        if c is None or a is None or c.empty or a.empty:
            continue
        m = c.merge(a, on="date", suffixes=("_c", "_a"))
        m["date"] = pd.to_datetime(m["date"])
        semi_rich.append(m.set_index("date").eval("value_c - value_a"))
    series = [_display_series(skew[["date", "value"]], "SPX 30d put skew (90%−ATM mny)")]
    if semi_rich:
        avg = pd.concat(semi_rich, axis=1).mean(axis=1).dropna().rename("value").reset_index()
        series.append(_display_series(avg, "Semi call richness (110%−ATM, avg)", role="benchmark"))
    store.write_display("VC4", {
        "id": "VC4", "name": "Skew panel", "panel": "volatility",
        "source": "BBG moneyness IV", "cadence": "daily",
        "asof": skew["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " vol pts (tile: SPX put skew)",
        "series": series,
        "tile": {"value": round(float(skew["value"].iloc[-1]), 2), "delta": None,
                 "percentile": util.trailing_percentile(skew["value"])},
        "provenance": "bloomberg_cache",
        "notes": "Moneyness-based skew (90/110% vs ATM), not delta-based (§4.0 note). "
                 "Positive semi call richness = upside chased. % members inverted "
                 "lands with Phase-3 OPRA snapshots.",
    })
    return True


def build_vc6() -> bool:
    """VC6: equal-weight 3M ATM IV baskets — semis vs hyperscalers, healthcare,
    staples (CIO 2026-07-10). All four lines share the SAME construction
    (equal-weight single-name IV) so levels are directly comparable; sector-ETF
    IV was rejected — it embeds cross-name correlation and reads far lower."""
    from .. import config

    def _basket_avg(names: list[str]) -> tuple[pd.DataFrame | None, int]:
        parts = []
        for t in names:
            df = store.read_latest(f"bbg_iv3m_{t.lower()}")
            if df is None or df.empty:
                continue
            d = df[["date", "value"]].copy()
            d["date"] = pd.to_datetime(d["date"])
            parts.append(d.set_index("date")["value"].rename(t))
        if len(parts) < max(3, len(names) // 2):
            return None, 0
        avg = pd.concat(parts, axis=1).mean(axis=1).dropna().rename("value").reset_index()
        return avg, len(parts)

    semis, n_semis = _basket_avg(config.IV_BASKETS["semis"])
    if semis is None:
        return False
    series = [_display_series(semis, f"Semis ({n_semis} names)", role="context")]
    for basket in ("hyperscalers", "healthcare", "staples"):
        avg, n = _basket_avg(config.IV_BASKETS[basket])
        if avg is not None:
            series.append(_display_series(avg, f"{basket.capitalize()} ({n} names)",
                                          role="context"))
    store.write_display("VC6", {
        "id": "VC6", "name": "3M IV by sector basket", "panel": "volatility",
        "source": "BBG per-name 3M ATM IV", "cadence": "daily",
        "asof": semis["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": " vol pts",
        "series": series,
        "tile": {"value": round(float(semis["value"].iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(semis["value"])},
        "provenance": "bloomberg_cache",
        "tooltip": "Equal-weight single-name 3M implied vol by sector basket — same "
                   "construction, comparable levels.",
        "notes": "Per-name 3M ATM IV averaged equally within each basket; a basket only "
                 "renders with at least half its names (min 3). Tile: semis.",
    })
    return True


def _realized(px: pd.DataFrame, window: int, annualize: float) -> pd.DataFrame:
    """Rolling realized vol (annualized %) from index closes, log returns.
    BBG convention (VOLATILITY_360D etc.): trading-day window, sqrt(260)
    annualization — verified 2026-07-10 vs Terminal (17.61 vs BBG 17.62)."""
    import numpy as np
    s = np.log(px.set_index("date")["value"]).diff()
    rv = (s.rolling(window).std() * (annualize ** 0.5) * 100.0).dropna()
    return rv.rename("value").reset_index()


def _build_iv_pair(mid: str, idx_name: str, iv_prefix: str, px_mnemonic: str,
                   wings: bool, vol_idx: str | None = None) -> bool:
    """VC7/VC8 (vol index vs realized) and VC9/VC10 (10% OTM wings vs realized)."""
    px = _lake_series(px_mnemonic)
    if px is None:
        return False
    rv360 = _realized(px, 360, 260)   # BBG VOLATILITY_360D convention
    src = "BBG moneyness IV + index closes"
    if wings:
        call = store.read_latest(f"bbg_{iv_prefix}_call_wing")
        put = store.read_latest(f"bbg_{iv_prefix}_put_wing")
        if call is None or put is None:
            return False
        call = call.sort_values("date").assign(date=lambda d: pd.to_datetime(d["date"]))
        put = put.sort_values("date").assign(date=lambda d: pd.to_datetime(d["date"]))
        series = [
            _display_series(put[["date", "value"]], "10% OTM put IV (90% mny)", unit="vol"),
            _display_series(call[["date", "value"]], "10% OTM call IV (110% mny)",
                            role="context", unit="vol"),
            _display_series(rv360, "Realized vol (360d, BBG conv.)", role="benchmark", unit="vol"),
        ]
        latest = float(put["value"].iloc[-1])
        pctile = util.trailing_percentile(put["value"])
        asof = put["date"].iloc[-1].strftime("%Y-%m-%d")
        name = f"{idx_name} 10% OTM call/put IV"
        tip = ("Downside and upside 10% OTM wing vol vs realized — the gap between "
               "wings is the skew.")
        note = ("90%/110% moneyness 30d IV; realized leg uses log returns over 360 "
                "trading days, sqrt(260) annualized. Tile: put wing.")
    else:
        vi = _lake_series(vol_idx)
        if vi is None:
            return False
        vlabel = vol_idx.upper()   # VIX / VXN
        series = [
            _display_series(vi[["date", "value"]], f"{vlabel} (30d implied)", unit="vol"),
            _display_series(rv360, "Realized vol (360d, BBG conv.)", role="benchmark", unit="vol"),
        ]
        latest = float(vi["value"].iloc[-1])
        pctile = util.trailing_percentile(vi["value"])
        asof = vi["date"].iloc[-1].strftime("%Y-%m-%d")
        src = f"BBG {vlabel} + index closes"
        name = f"{idx_name} implied ({vlabel}) vs realized vol"
        tip = (f"{vlabel} (30-day implied vol, skew-inclusive) vs 360-day realized "
               "(Bloomberg convention).")
        note = (f"{vlabel} implied-vol index vs realized vol from log returns over 360 "
                f"trading days, sqrt(260) annualized. Tile: {vlabel}.")
    store.write_display(mid, {
        "id": mid, "name": name, "panel": "volatility",
        "source": src, "cadence": "daily",
        "asof": asof, "unit": "vol pts",
        "series": series,
        "tile": {"value": round(latest, 2), "delta": None, "percentile": pctile},
        "provenance": "bloomberg_cache", "tooltip": tip, "notes": note,
    })
    return True


def build_vc7():
    return _build_iv_pair("VC7", "SPX", "spx_iv", "spx", wings=False, vol_idx="vix")


def build_vc8():
    return _build_iv_pair("VC8", "NDX", "ndx_iv", "ndx", wings=False, vol_idx="vxn")


def build_vc9():
    return _build_iv_pair("VC9", "SPX", "spx_iv", "spx", wings=True)


def build_vc10():
    return _build_iv_pair("VC10", "NDX", "ndx_iv", "ndx", wings=True)


def build() -> dict[str, bool]:
    # VC5 (spot-up/vol-up) dropped 2026-07-09 per CIO
    # VC4 (skew panel) dropped 2026-07-10 per CIO — combined two unrelated reads
    return {"VC7": build_vc7(), "VC8": build_vc8(), "VC9": build_vc9(),
            "VC10": build_vc10(), "VC1": build_vc1(), "VC2": build_vc2(),
            "VC3": build_vc3(), "VC6": build_vc6(),
            "MH7": build_mh7()}
