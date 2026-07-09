"""ownership.py — Panel 2 computes from FRED lake data (§4 OP1/OP3).

OP1  Household equity by wealth cohort — DFA levels, all four cohorts, with the
     bottom-50% share as the tile (the Citadel-replication highlight).
OP2/OP4 nowcasts need BBG SPX TR / ICI weekly — land later (§5.6).
OP3  Household cash % of financial assets — (checkable + time/savings + MMF)
     / total financial assets, Z.1 B.101 quarterly.

Every displayed number carries as-of + source; percentile via util (1yr gate —
quarterly series easily clear it on count only after ~63 years, so for
quarterly cadence we gate on 40 observations ≈ 10yr instead, still honest)."""
from __future__ import annotations

import pandas as pd

from .. import store, util

MIN_QUARTERLY_OBS = 40  # ~10yr of quarterly prints — percentile gate for Q series


def _display_series(df: pd.DataFrame, name: str, role: str = "avos", unit: str = None):
    disp = util.downsample_display(df)
    return {"name": name, "role": role, "estimated_from": None, "unit": unit,
            "points": [{"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "value": round(float(v), 3)}
                       for d, v in zip(disp["date"], disp["value"])]}


def build_op1() -> bool:
    cohorts = [
        ("dfa_eq_bottom50", "Bottom 50%"),
        ("dfa_eq_next40", "50th–90th"),
        ("dfa_eq_next9", "90th–99th"),
        ("dfa_eq_top1", "Top 1%"),
    ]
    frames = {m: store.read_latest(f"fred_{m}") for m, _ in cohorts}
    if any(f is None for f in frames.values()):
        return False  # leave last-good in place (§2)

    share_b50 = store.read_latest("fred_dfa_eqsh_bottom50")
    series = []
    for (m, label) in cohorts:
        df = frames[m][["date", "value"]].copy()
        df["value"] = df["value"] / 1e6  # $M → $T for readability
        series.append(_display_series(df, label,
                                      role="avos" if m == "dfa_eq_bottom50" else "context"))

    tile_pct, tile_val, asof = None, None, "—"
    if share_b50 is not None and not share_b50.empty:
        s = share_b50.sort_values("date")
        tile_val = round(float(s["value"].iloc[-1]), 2)
        asof = str(s["date"].iloc[-1])[:10]
        tile_pct = util.trailing_percentile(s["value"], min_history=MIN_QUARTERLY_OBS)

    store.write_display("OP1", {
        "id": "OP1", "name": "Household equity by wealth cohort", "panel": "ownership",
        "source": "FRED DFA [verified 2026-07-08]", "cadence": "quarterly",
        "asof": asof, "unit": "% (tile: bottom-50% share)",
        "series": series,
        "tile": {"value": tile_val, "delta": None, "percentile": tile_pct},
        "provenance": "fred_cache",
        "notes": "Levels in $T per cohort; tile = bottom-50% share of aggregate. "
                 "DFA lands ~11 weeks after quarter-end; OP2 nowcast (dashed) pending BBG.",
    })
    return True


def build_op3() -> bool:
    parts = {m: store.read_latest(f"fred_{m}")
             for m in ("hh_fin_assets", "hh_checkable", "hh_time_savings", "hh_mmf")}
    if any(f is None for f in parts.values()):
        return False

    merged = None
    for m, df in parts.items():
        d = df[["date", "value"]].rename(columns={"value": m})
        merged = d if merged is None else merged.merge(d, on="date", how="inner")
    merged = merged.sort_values("date").reset_index(drop=True)
    merged["value"] = (merged["hh_checkable"] + merged["hh_time_savings"] + merged["hh_mmf"]) \
        / merged["hh_fin_assets"] * 100.0

    ratio = merged[["date", "value"]]
    tile_val = round(float(ratio["value"].iloc[-1]), 2)
    asof = str(ratio["date"].iloc[-1])[:10]
    pct = util.trailing_percentile(ratio["value"], min_history=MIN_QUARTERLY_OBS)

    store.write_display("OP3", {
        "id": "OP3", "name": "Household cash % of financial assets", "panel": "ownership",
        "source": "FRED Z.1 B.101 [verified 2026-07-08]", "cadence": "quarterly",
        "asof": asof, "unit": "%",
        "series": [_display_series(ratio, "Cash % of financial assets")],
        "tile": {"value": tile_val, "delta": None, "percentile": pct},
        "provenance": "fred_cache",
        "notes": "(checkable+currency + time/savings + MMF) / total financial assets, "
                 "households (Z.1 B.101). OP4 weekly nowcast (dashed) pending ICI+H.8.",
    })
    return True


def build_op2() -> bool:
    """OP2: household-equity nowcast (§5.6) — last DFA cohort levels rolled
    forward with SPX total return since quarter-end; cohort shares frozen.
    Official prints solid, nowcast segment dashed (role='nowcast')."""
    b50 = store.read_latest("fred_dfa_eq_bottom50")
    spx_tr = store.read_latest("bbg_spx_tr")
    if b50 is None or spx_tr is None:
        return False
    cohorts = {m: store.read_latest(f"fred_dfa_eq_{m}")
               for m in ("bottom50", "next40", "next9", "top1")}
    if any(v is None for v in cohorts.values()):
        return False
    total = None
    for name, df in cohorts.items():
        d = df[["date", "value"]].rename(columns={"value": name})
        total = d if total is None else total.merge(d, on="date", how="inner")
    total["value"] = total[list(cohorts)].sum(axis=1)
    total = total[["date", "value"]].sort_values("date")
    total["value"] /= 1e6  # $M → $T
    total["date"] = pd.to_datetime(total["date"])

    tr = spx_tr.copy()
    tr["date"] = pd.to_datetime(tr["date"])
    # DFA dates quarter-START convention: level is as of quarter-END (+3mo)
    last_row = total.iloc[-1]
    qend = last_row["date"] + pd.offsets.QuarterEnd(1)
    base = tr[tr["date"] <= qend]
    if base.empty:
        return False
    base_tr = base["value"].iloc[-1]
    fwd = tr[tr["date"] > qend].copy()
    fwd["value"] = last_row["value"] * fwd["value"] / base_tr
    now_val = float(fwd["value"].iloc[-1]) if not fwd.empty else float(last_row["value"])

    store.write_display("OP2", {
        "id": "OP2", "name": "Household equity — nowcast", "panel": "ownership",
        "source": "FRED DFA × BBG SPTR (§5.6)", "cadence": "daily",
        "asof": (fwd["date"].iloc[-1].strftime("%Y-%m-%d") if not fwd.empty
                 else last_row["date"].strftime("%Y-%m-%d")),
        "unit": " $T",
        "series": [_display_series(total, "Household corporate equities (DFA, official)"),
                   {**_display_series(fwd[["date", "value"]], "Nowcast (SPX-TR rolled)", role="nowcast"),
                    "estimated_from": qend.strftime("%Y-%m-%d")}],
        "tile": {"value": round(now_val, 1), "delta": None, "percentile": None},
        "provenance": "derived",
        "notes": "Last DFA print rolled with SPX total return; cohort shares frozen "
                 "(§5.6). Dashed = nowcast; flagged until the next DFA print "
                 "(Q2 2026 lands 2026-09-11). Percentile suppressed on nowcast.",
    })
    return True


def _etf_flows() -> pd.DataFrame | None:
    """Per-ticker daily flows via the shares-outstanding method:
    flow_t = ΔSH_OUT_t × NAV_t ($M). Returns long frame [date, ticker, category,
    flow, aum]."""
    from .. import config
    frames = []
    for t, (cat, _L) in config.ETF_UNIVERSE.items():
        df = store.read_latest(f"bbg_etf_{t.lower()}")
        if df is None or df.empty:
            continue
        df = df.sort_values("date").copy()
        df["date"] = pd.to_datetime(df["date"])
        df["flow"] = df["sh_out"].diff() * df["nav"]  # both per-share ($) × M shares → $M
        df["ticker"], df["category"] = t, cat
        frames.append(df[["date", "ticker", "category", "flow", "aum"]])
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def build_op567() -> dict[str, bool]:
    from .. import config
    f = _etf_flows()
    if f is None:
        return {"OP5": False, "OP6": False, "OP7": False}
    f = f.dropna(subset=["flow"])
    coverage_note = (f"Curated {f['ticker'].nunique()}-fund universe (top of complex, "
                     "§A3 coverage label) — not all US ETFs. Flows = Δshares × NAV.")

    # OP5 — aggregate daily net flow + cumulative-YTD vs prior years (Citadel ch.7)
    daily = f.groupby("date")["flow"].sum() / 1e3  # $B
    d = daily.reset_index().rename(columns={"flow": "value"})
    years = sorted(d["date"].dt.year.unique())[-4:]
    ytd_series = []
    for y in years:
        dy = d[d["date"].dt.year == y].copy()
        dy["value"] = dy["value"].cumsum()
        dy["doy"] = dy["date"].dt.dayofyear
        # plot against a common current-year axis so years overlay
        base = pd.Timestamp(f"{years[-1]}-01-01")
        dy["date"] = base + pd.to_timedelta(dy["doy"] - 1, unit="D")
        ytd_series.append(_display_series(
            dy[["date", "value"]], f"Cumulative {y} ($B)",
            role="avos" if y == years[-1] else "context"))
    store.write_display("OP5", {
        "id": "OP5", "name": "ETF net flows", "panel": "flows",
        "source": "BBG Δshares × NAV", "cadence": "daily (T+1)",
        "asof": d["date"].max().strftime("%Y-%m-%d"), "unit": " $B (tile: 20d sum)",
        "series": ytd_series,
        "tile": {"value": round(float(daily.rolling(20).sum().iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(daily.rolling(20).sum().dropna())},
        "provenance": "bloomberg_cache", "notes": coverage_note + " Overlaid cumulative-YTD by year.",
    })

    # OP6 — flows by category (20d rolling sum per category)
    cat = f.pivot_table(index="date", columns="category", values="flow", aggfunc="sum") / 1e3
    cat = cat.rolling(20).sum().dropna(how="all")
    series = [_display_series(cat[c].dropna().rename("value").reset_index(), f"{c} (20d, $B)",
                              role="avos" if c == "leveraged" else "context")
              for c in cat.columns]
    lev20 = cat["leveraged"].dropna() if "leveraged" in cat.columns else None
    store.write_display("OP6", {
        "id": "OP6", "name": "ETF flows by category", "panel": "flows",
        "source": "BBG Δshares × NAV", "cadence": "daily",
        "asof": cat.index.max().strftime("%Y-%m-%d"), "unit": " $B (tile: leveraged 20d)",
        "series": series,
        "tile": {"value": round(float(lev20.iloc[-1]), 2) if lev20 is not None else None,
                 "delta": None,
                 "percentile": util.trailing_percentile(lev20) if lev20 is not None else None},
        "provenance": "bloomberg_cache", "notes": coverage_note,
    })

    # OP7 — leveraged AUM total + single-stock slice
    lev = f[f["category"] == "leveraged"]
    tot = lev.groupby("date")["aum"].sum() / 1e3
    single = lev[lev["ticker"].isin([t for t, u in config.LEV_ETF_UNDERLYING.items()
                                     if u not in ("QQQ", "SPY", "SMH")])]
    stot = single.groupby("date")["aum"].sum() / 1e3
    store.write_display("OP7", {
        "id": "OP7", "name": "Leveraged ETF AUM", "panel": "ownership",
        "source": "BBG FUND_TOTAL_ASSETS", "cadence": "daily",
        "asof": tot.index.max().strftime("%Y-%m-%d"), "unit": " $B",
        "series": [_display_series(tot.rename("value").reset_index(), "Leveraged complex AUM ($B)"),
                   _display_series(stot.rename("value").reset_index(), "of which single-stock ($B)",
                                   role="context")],
        "tile": {"value": round(float(tot.iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(tot)},
        "provenance": "bloomberg_cache", "notes": coverage_note,
    })
    return {"OP5": True, "OP6": True, "OP7": True}


def build() -> dict[str, bool]:
    out = {"OP1": build_op1(), "OP2": build_op2(), "OP3": build_op3()}
    out.update(build_op567())
    return out
