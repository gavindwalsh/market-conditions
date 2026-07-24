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
        "notes": (
            "**What it shows.** The average correlation between S&P 500 members "
            "that index-options prices imply for the coming month. Low readings mean "
            "index options are cheap relative to single-name options — the market "
            "expects stocks to move on their own news (a dispersion, stock-picker's "
            "regime). High readings mean stocks are priced to move together, an "
            "index-like, macro-driven tape.\n\n"
            "**How it's computed.** We plot Cboe's published COR1M (1-month) with "
            "COR3M (3-month) alongside; the tile ranks COR1M against its own trailing "
            "history. Cboe derives implied correlation from the identity that links "
            "index variance to member variances: index options price the index "
            "variance `σ_index²`, single-name options price each member's variance "
            "`σᵢ²`, and the implied average correlation `ρ` is the value that "
            "reconciles the two — `σ_index² = Σ wᵢ² σᵢ² + ρ · ΣΣ wᵢ wⱼ σᵢ σⱼ` (the "
            "double sum runs over distinct member pairs, `wᵢ` are index weights). "
            "Solving for `ρ` gives the index.\n\n"
            "**Caveats.** This is an implied, forward-looking measure read from "
            "options prices, not correlation that has actually occurred. The gap "
            "between implied and realized correlation — the correlation risk premium "
            "— is charted separately as VC2."
        ),
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
        "notes": (
            "**What it shows.** The slope of the S&P 500's implied-volatility term "
            "structure — near-term expected vol (VIX, 30-day) divided by 3-month "
            "expected vol (VIX3M). Above 1 the curve is inverted: near-term fear "
            "exceeds the medium term, the classic stress signature. Below 1 the curve "
            "is in contango (upward-sloping), the calm-market norm that makes selling "
            "short-dated vol profitable to carry.\n\n"
            "**How it's computed.** The daily ratio `VIX ÷ VIX3M` of closing index "
            "levels. The tile ranks the ratio against its trailing history.\n\n"
            "**Caveats.** A ratio of two Cboe indices measuring 30-day and 3-month "
            "expected S&P 500 volatility. Per-name term-structure slopes for the "
            "largest single names are a possible future extension."
        ),
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
        "notes": (
            "**What it shows.** The rates backdrop for equities on one chart — bond-market "
            "volatility (the MOVE index) alongside the 10-year Treasury yield and the "
            "2s10s curve slope. Rising rates vol or a sharply moving curve is a headwind "
            "for risk assets, so this is the cross-asset context for everything else on "
            "the dashboard.\n\n"
            "**How it's computed.** The MOVE index is plotted on its own left (level) "
            "axis; the 10-year yield and the 2s10s slope — the 10-year minus 2-year "
            "yield, in percentage points — share the right (%) axis.\n\n"
            "**Caveats.** The dollar index (DXY) was dropped from this chart on "
            "2026-07-10: its narrow range flatlined beneath MOVE on the shared axis and "
            "added the least as context."
        ),
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
        "notes": (
            "**What it shows.** How far implied correlation (VC1's COR1M) sits above "
            "the correlation S&P 500 members actually realized. That excess is the "
            "correlation risk premium — what dispersion sellers (short index vol, long "
            "single-name vol) are paid. A wide positive spread means dispersion trades "
            "were richly compensated.\n\n"
            "**How it's computed.** We compute realized average pairwise correlation "
            "from member returns using the index-variance identity, solved for a "
            "single correlation: `ρ = (σ_idx² − Σ wᵢ² σᵢ²) / ((Σ wᵢ σᵢ)² − Σ wᵢ² "
            "σᵢ²)`, where `σ_idx` is the 21-day rolling volatility of the S&P 500, "
            "`σᵢ` the 21-day rolling volatility of member `i`, and `wᵢ` its index "
            "weight. The numerator strips each stock's own variance out of index "
            "variance, leaving the covariance term; the denominator normalizes by the "
            "same quantity under an all-pairs-equal-correlation assumption, so `ρ` is "
            "the one correlation that reproduces the observed index variance. The "
            "plotted spread is `COR1M − 100·ρ`; a realized line is shown for context. "
            "The 21-day window follows the realized-volatility conventions above, "
            "matched to COR1M's one-month tenor.\n\n"
            "**Caveats.** Realized correlation is clipped to the [−1, 1] range before "
            "scaling. The calculation uses today's index membership and weights "
            "applied backward through history, so older values carry a survivorship "
            "bias — the same limitation noted for the realized-dispersion chart."
        ),
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
        "notes": (
            "**What it shows.** Three-month at-the-money implied volatility, averaged "
            "across the names in each sector basket (semiconductors, hyperscalers, "
            "healthcare, staples). Because every basket is built the same way, the "
            "levels are directly comparable — you can read straight off the chart "
            "which part of the market options traders expect to be most volatile.\n\n"
            "**How it's computed.** For each name we take Bloomberg's 3-month ATM "
            "implied vol, then equal-weight average across the basket: `IV_basket = "
            "mean(IVᵢ)`. A basket renders on a given day only if at least half its "
            "names — and no fewer than three — have data, so a thin feed can't distort "
            "the average. The tile ranks the semis basket.\n\n"
            "**Caveats.** This is equal-weight single-name implied vol, deliberately "
            "not sector-ETF implied vol: an ETF's option vol embeds the correlation "
            "across its holdings and prints materially lower, which would not be "
            "comparable to a basket of individual names."
        ),
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
        note = (
            f"**What it shows.** The implied volatility of 10%-out-of-the-money puts "
            f"(90% moneyness) and calls (110% moneyness) on {idx_name}, with realized "
            "vol for reference. The put wing sits above the call wing in normal "
            "markets — the standing cost of downside protection. The distance between "
            "the two wings is the skew, and it widens as hedging demand rises.\n\n"
            "**How it's computed.** Both wings are 30-day implied vols read at fixed "
            "moneyness — strikes set at 90% and 110% of spot. The realized leg is "
            "360-day realized volatility built per the realized-volatility conventions "
            "above (daily log returns of index closes, rolling 360-trading-day window, "
            "annualized by √260 to tie to Bloomberg's VOLATILITY_360D field). The tile "
            "ranks the put wing.\n\n"
            "**Caveats.** These are moneyness-based wings, not delta-based: the strikes "
            "are fixed percentages of spot rather than a fixed option delta, so they do "
            "not re-strike as volatility changes. As with the VIX/VXN charts, the "
            "30-day wings are compared against a 360-day realized window, so part of "
            "any level gap is the tenor difference."
        )
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
        note = (
            f"**What it shows.** {idx_name}'s 30-day implied volatility ({vlabel}) "
            f"against the volatility {idx_name} has actually realized. Implied sitting "
            "above realized is the normal state — the variance risk premium that "
            "sellers of options earn; the gap narrowing, or the two lines crossing, "
            "marks stress.\n\n"
            f"**How it's computed.** The implied leg is the {vlabel} index — 30-day, "
            "skew-inclusive expected volatility. The realized leg is 360-day realized "
            "volatility built per the realized-volatility conventions above: daily log "
            "returns of index closes, a rolling 360-trading-day window, annualized by "
            "√260 to tie to Bloomberg's VOLATILITY_360D field. The tile ranks "
            f"{vlabel} against its trailing history.\n\n"
            "**Caveats.** The implied tenor (30 days) is far shorter than the realized "
            "window (360 days), so part of the level gap reflects that tenor mismatch "
            "rather than the risk premium alone — read the direction and size of "
            "changes in the gap, not the raw level."
        )
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
