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


def _display_series(df: pd.DataFrame, name: str, role: str = "avos", unit: str = None,
                    ds: str = "auto"):
    """One display series. ds='auto' applies the §6 downsample (daily last year,
    monthly last-obs before); ds='none' keeps every point — REQUIRED for bar
    series that are already aggregated (weekly/monthly/quarterly), because
    monthly last-obs sampling breaks bar widths and lies about aggregates."""
    disp = util.downsample_display(df) if ds == "auto" else df.copy()
    return {"name": name, "role": role, "estimated_from": None, "unit": unit,
            "points": [{"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "value": round(float(v), 3)}
                       for d, v in zip(disp["date"], disp["value"])
                       if pd.notna(v)]}


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
        "notes": (
            "**What it shows.** How much corporate equity US households own, split into "
            "four wealth cohorts — bottom 50%, 50th–90th, 90th–99th, and top 1%. The "
            "tile is the bottom-50%'s share of the total, a direct read on how "
            "concentrated equity ownership is.\n\n"
            "**How it's computed.** Federal Reserve Distributional Financial Accounts "
            "(DFA) equity levels, shown in trillions of dollars per cohort; the tile "
            "ranks the bottom-50% share of aggregate household equity against its own "
            "history.\n\n"
            "**Caveats.** The DFA is released about 11 weeks after quarter-end; OP2 is "
            "the daily nowcast that bridges that lag."
        ),
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
        "notes": (
            "**What it shows.** How much of household financial assets sit in cash-like "
            "holdings — a dry-powder and risk-appetite gauge. A rising line means "
            "households are holding back; a falling one means cash is being put to "
            "work.\n\n"
            "**How it's computed.** `(checkable deposits + currency + time & savings "
            "deposits + money-market funds) ÷ total household financial assets`, from the "
            "Fed's Z.1 Financial Accounts (table B.101), quarterly.\n\n"
            "**Caveats.** Quarterly; the weekly nowcast (OP4) that would bridge the "
            "reporting lag is pending ICI and Fed H.8 data."
        ),
    })
    return True


def build_op2() -> bool:
    """OP2: household-equity nowcast (§5.6) as a share of nominal GDP — the full
    DFA history (1989→) divided by FRED GDP, with the last print rolled forward
    with SPX total return since quarter-end. Official prints solid, nowcast
    segment dashed (role='nowcast').

    Changed 2026-07-27 (CIO): was the last 12 quarters in dollars. Dollars grow
    mechanically with the economy, so the level was only readable against its
    own recent past; scaling by GDP makes the whole 1989→ history comparable and
    shows where today sits versus the 2000 and 2007 peaks."""
    spx_tr = store.read_latest("bbg_spx_tr")
    gdp = store.read_latest("fred_gdp")
    if spx_tr is None or gdp is None or gdp.empty:
        return False
    cohorts = {m: store.read_latest(f"fred_dfa_eq_{m}")
               for m in ("bottom50", "next40", "next9", "top1")}
    if any(v is None for v in cohorts.values()):
        return False
    total = None
    for name, df in cohorts.items():
        d = df[["date", "value"]].rename(columns={"value": name})
        total = d if total is None else total.merge(d, on="date", how="inner")
    total["bn"] = total[list(cohorts)].sum(axis=1) / 1e3  # $M → $B (GDP's unit)
    total = total[["date", "bn"]].sort_values("date")
    total["date"] = pd.to_datetime(total["date"])
    # DFA dates quarter-START convention: the level is as of quarter-END, so
    # shift for display (and the roll anchor reads straight off the last row)
    total["date"] = total["date"] + pd.offsets.QuarterEnd(1)

    # nominal GDP is quarterly, $B SAAR, dated quarter-START; merge_asof backward
    # onto the quarter-END equity dates pairs each print with its OWN quarter.
    gdp = gdp[["date", "value"]].rename(columns={"value": "gdp_bn"}).sort_values("date").copy()
    gdp["date"] = pd.to_datetime(gdp["date"])
    total = pd.merge_asof(total, gdp, on="date", direction="backward").dropna(subset=["gdp_bn"])
    total["value"] = total["bn"] / total["gdp_bn"] * 100.0
    if total.empty:
        return False

    tr = spx_tr.copy()
    tr["date"] = pd.to_datetime(tr["date"])
    tr = tr.sort_values("date")
    last_row = total.iloc[-1]
    qend = last_row["date"]  # already quarter-end after the shift
    base = tr[tr["date"] <= qend]
    if base.empty:
        return False
    base_tr = base["value"].iloc[-1]
    fwd = tr[tr["date"] > qend].copy()
    # roll the DOLLAR level with SPX TR, then divide by the latest GDP print held
    # flat (GDP for the current quarter is not published yet).
    gdp_now = float(gdp["gdp_bn"].iloc[-1])
    fwd["value"] = last_row["bn"] * fwd["value"] / base_tr / gdp_now * 100.0
    now_val = float(fwd["value"].iloc[-1]) if not fwd.empty else float(last_row["value"])
    asof = (fwd["date"].iloc[-1] if not fwd.empty else qend).strftime("%Y-%m-%d")
    # anchor the dashed nowcast at the last official point so the two segments join
    fwd = pd.concat([pd.DataFrame({"date": [qend], "value": [last_row["value"]]}),
                     fwd[["date", "value"]]], ignore_index=True)
    official = total[["date", "value"]]

    store.write_display("OP2", {
        "id": "OP2", "name": "Household equity — nowcast (% of GDP)", "panel": "ownership",
        "source": "FRED DFA × BBG SPTR · FRED GDP", "cadence": "daily",
        "asof": asof,
        "unit": " %",
        "series": [_display_series(official, "Household corporate equities (DFA, official)"),
                   {**_display_series(fwd, "Nowcast (SPX-TR rolled)", role="nowcast"),
                    "estimated_from": qend.strftime("%Y-%m-%d")}],
        "tile": {"value": round(now_val, 1), "delta": None,
                 "percentile": util.trailing_percentile(
                     pd.concat([official["value"], pd.Series([now_val])], ignore_index=True),
                     min_history=MIN_QUARTERLY_OBS)},
        "provenance": "derived",
        "tooltip": "Household equity holdings as a share of nominal GDP, rolled forward "
                   "daily with SPX total return (dashed = nowcast).",
        "notes": (
            "**What it shows.** How large US households' equity holdings are relative to "
            "the economy, brought up to date daily. The official quarterly print is rolled "
            "forward with the S&P 500's total return, so you can see roughly where "
            "household equity stands today rather than a quarter ago. Because the level is "
            "scaled by GDP rather than left in dollars, the full history back to 1989 is "
            "directly comparable — today's reading can be read against the 2000 and 2007 "
            "peaks instead of only against the last few years.\n\n"
            "**How it's computed.** The four DFA wealth-cohort equity levels are summed "
            "(billions of dollars) and divided by nominal GDP — FRED series GDP, quarterly "
            "in billions at a seasonally-adjusted annual rate — times 100. DFA levels are "
            "dated to quarter-end and paired with their own quarter's GDP. For the nowcast, "
            "the last dollar level is grown by the S&P 500 total return since quarter-end, "
            "holding each cohort's share fixed, and divided by the most recent published "
            "GDP print. The official prints are drawn solid; the rolled-forward segment is "
            "dashed.\n\n"
            "**Caveats.** A nowcast, not data — cohort shares are frozen between DFA "
            "prints (the next, Q2 2026, is expected 2026-09-11). The current quarter's GDP "
            "is not published yet, so the nowcast holds the last GDP print flat; while the "
            "economy grows, that slightly overstates the ratio. History starts in 1989 Q3, "
            "the first DFA observation."
        ),
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
    n_funds = f["ticker"].nunique()

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
        "provenance": "bloomberg_cache",
        "tooltip": "Cumulative ETF net flows year-to-date, overlaid by year.",
        "notes": (
            "**What it shows.** Cumulative net flows into the ETF universe over the year "
            "to date, with the last few years overlaid so the current year's pace stands "
            "against its predecessors.\n\n"
            "**How it's computed.** Daily net flow via the shares-outstanding method "
            "(`flow = Δshares × NAV`, see ETF flow universe in Shared methodology), "
            "summed across the universe and accumulated within each calendar year. Each "
            "year is plotted on a common day-of-year axis so the curves line up. The tile "
            "is the trailing 20-day sum.\n\n"
            f"**Caveats.** Covers the curated {n_funds}-fund universe — the largest of "
            "each complex — not every US ETF."
        ),
    })

    # OP6 — flows by category, stacked WEEKLY bars (CIO 2026-07-10). Ghost rows
    # (e.g. a holiday with one stray ticker print) NaN-poisoned the old 20d
    # rolling window and froze the tile 3 weeks stale — drop them first.
    cat = f.pivot_table(index="date", columns="category", values="flow", aggfunc="sum") / 1e3
    cat = cat[cat.notna().sum(axis=1) >= 3].fillna(0.0)
    weekly = cat.resample("W-FRI").sum()
    weekly = weekly[weekly.index <= cat.index.max() + pd.Timedelta(days=4)]
    if len(weekly) > 1:
        weekly = weekly.iloc[:-1]  # drop the in-progress week
    weekly = weekly.tail(156)      # ~3 years
    series = []
    for c in weekly.columns:
        sd = _display_series(weekly[c].rename("value").reset_index(),
                             f"{c} ($B/wk)", role="context", unit="$B", ds="none")
        sd["kind"], sd["stack"] = "bar", True
        series.append(sd)
    lev_w = weekly["leveraged"] if "leveraged" in weekly.columns else None
    store.write_display("OP6", {
        "id": "OP6", "name": "ETF flows by category", "panel": "flows",
        "source": "BBG Δshares × NAV", "cadence": "weekly",
        "asof": weekly.index.max().strftime("%Y-%m-%d"), "unit": " $B/wk (tile: leveraged)",
        "series": series,
        "tile": {"value": round(float(lev_w.iloc[-1]), 2) if lev_w is not None else None,
                 "delta": None,
                 "percentile": (util.trailing_percentile(lev_w, min_history=52)
                                if lev_w is not None else None)},
        "provenance": "bloomberg_cache",
        "tooltip": "Weekly net ETF flows stacked by category — positives up, redemptions down.",
        "notes": (
            "**What it shows.** Weekly net ETF flows stacked by category (equity, "
            "leveraged, and so on) — creations up, redemptions down. It shows where money "
            "is rotating across the fund complex.\n\n"
            "**How it's computed.** The same shares-outstanding flows (see ETF flow "
            "universe in Shared methodology) summed per category per Friday-ended week, "
            "with about three years shown. The tile is the latest complete leveraged-"
            "category week.\n\n"
            f"**Caveats.** Covers the curated {n_funds}-fund universe, not every US ETF; "
            "the in-progress week is dropped."
        ),
    })

    # OP7 — leveraged AUM total + single-stock slice
    lev = f[f["category"] == "leveraged"]
    tot = lev.groupby("date")["aum"].sum() / 1e3
    single = lev[lev["ticker"].isin([t for t, u in config.LEV_ETF_UNDERLYING.items()
                                     if u not in ("QQQ", "SPY", "SMH")])]
    stot = single.groupby("date")["aum"].sum() / 1e3
    store.write_display("OP7", {
        "id": "OP7", "name": "Leveraged ETF AUM", "panel": "flows",
        "source": "BBG FUND_TOTAL_ASSETS", "cadence": "daily",
        "asof": tot.index.max().strftime("%Y-%m-%d"), "unit": " $B",
        "series": [_display_series(tot.rename("value").reset_index(), "Leveraged complex AUM ($B)"),
                   _display_series(stot.rename("value").reset_index(), "of which single-stock ($B)",
                                   role="context")],
        "tile": {"value": round(float(tot.iloc[-1]), 1), "delta": None,
                 "percentile": util.trailing_percentile(tot)},
        "provenance": "bloomberg_cache",
        "tooltip": "Assets in the leveraged-ETF complex; second line = the single-stock slice.",
        "notes": (
            "**What it shows.** Total assets in the leveraged-ETF complex, with the "
            "single-stock-leveraged slice broken out — a gauge of how much levered "
            "exposure investors are holding, and how fast the single-stock corner is "
            "growing.\n\n"
            "**How it's computed.** Bloomberg FUND_TOTAL_ASSETS summed across the curated "
            "leveraged universe (see ETF flow universe in Shared methodology); the "
            "single-stock line is the funds that track individual names rather than "
            "QQQ, SPY, or SMH.\n\n"
            "**Caveats.** Covers the curated leveraged universe, not every US leveraged "
            "ETF."
        ),
    })
    return {"OP5": True, "OP6": True, "OP7": True}


def _build_fred_line(mid: str, mnemonic: str, name: str, unit: str, cadence: str,
                     source: str, tooltip: str, note: str, fmt: int = 1,
                     min_hist: int = MIN_QUARTERLY_OBS, status: dict | None = None,
                     clip_pandemic: bool = False, pct_of: str | None = None) -> bool:
    """One FRED series → one-line chart (Households panel). Monthly/quarterly
    levels or ratios; percentile ranks the latest print vs its own history.
    clip_pandemic caps the y-axis to the pre/post-COVID range so the 2020-21
    stimulus spikes run off-chart instead of flattening the rest of the series.
    pct_of names a second FRED mnemonic to divide by (×100) — used to scale
    dollar levels by nominal GDP; a lower-cadence denominator is carried forward
    onto the numerator's dates, so the ratio steps at the denominator's
    boundaries."""
    df = store.read_latest(f"fred_{mnemonic}")
    if df is None or df.empty:
        return False
    s = df[["date", "value"]].sort_values("date").copy()
    s["date"] = pd.to_datetime(s["date"])
    if pct_of:
        den = store.read_latest(f"fred_{pct_of}")
        if den is None or den.empty:
            return False
        den = den[["date", "value"]].rename(columns={"value": "_den"}).sort_values("date").copy()
        den["date"] = pd.to_datetime(den["date"])
        s = pd.merge_asof(s, den, on="date", direction="backward").dropna(subset=["_den"])
        if s.empty:
            return False
        s["value"] = s["value"] / s["_den"] * 100.0
        s = s[["date", "value"]]
    payload = {
        "id": mid, "name": name, "panel": "ownership",
        "source": source, "cadence": cadence,
        "asof": s["date"].iloc[-1].strftime("%Y-%m-%d"), "unit": unit,
        "series": [_display_series(s, name)],
        "tile": {"value": round(float(s["value"].iloc[-1]), fmt), "delta": None,
                 "percentile": util.trailing_percentile(s["value"], min_history=min_hist)},
        "provenance": "fred_cache", "tooltip": tooltip, "notes": note,
    }
    if clip_pandemic:
        ex = s[(s["date"] < "2020-03-01") | (s["date"] > "2021-12-31")]
        if not ex.empty:
            payload["y_max"] = round(float(ex["value"].max()) * 1.05, fmt)
    if status:
        payload["status"] = status
    store.write_display(mid, payload)
    return True


def build_households() -> dict[str, bool]:
    """OP9-11: household saving + debt-burden series from FRED."""
    return {
        "OP9": _build_fred_line(
            "OP9", "psavert", "Personal saving rate", "%", "monthly", "FRED PSAVERT",
            "Personal saving as a share of disposable personal income (monthly). "
            "The 2020-21 stimulus spikes run off the top so the rest is legible.",
            "**What it shows.** Personal saving as a share of disposable income — how "
            "much of what they earn households are setting aside. A lower rate can signal "
            "confidence or financial stretch; a higher one, caution.\n\n"
            "**How it's computed.** The BEA's PSAVERT series — personal saving ÷ "
            "disposable personal income — monthly, seasonally adjusted.\n\n"
            "**Caveats.** The y-axis is capped below the 2020–21 stimulus spikes so they "
            "run off-chart rather than flattening the rest of the history.",
            fmt=1, min_hist=120, clip_pandemic=True),
        # OP10: shown as a share of GDP since 2026-07-27 (CIO) — the dollar level
        # grows mechanically with the economy, so decades-apart readings weren't
        # comparable. OP9 already gives the share-of-income view; GDP is the
        # whole-economy denominator, which is the different read worth having.
        "OP10": _build_fred_line(
            "OP10", "pmsave", "Personal saving (% of GDP)", " %", "monthly",
            "FRED PMSAVE · FRED GDP",
            "Total personal saving as a share of nominal GDP (monthly). The 2020-21 "
            "stimulus spikes run off the top so the rest is legible.",
            "**What it shows.** How much households are saving relative to the size of the "
            "economy — the same household behavior as the saving rate (OP9), but measured "
            "against GDP rather than against disposable income. Scaling by GDP keeps the "
            "whole history comparable: the dollar level rises with the economy, so a "
            "1960s reading and a 2020s reading cannot be read side by side.\n\n"
            "**How it's computed.** The BEA's PMSAVE series — personal saving in billions "
            "of dollars at a seasonally-adjusted annual rate, monthly — divided by nominal "
            "GDP (FRED series GDP, quarterly, also in billions at a seasonally-adjusted "
            "annual rate), times 100. Both are annual-rate figures, so the ratio is "
            "directly meaningful; the quarterly GDP print is carried forward onto each "
            "month.\n\n"
            "**Caveats.** The y-axis is capped below the 2020–21 stimulus spikes so they "
            "run off-chart rather than flattening the rest of the history. GDP is "
            "quarterly, so the denominator steps at quarter boundaries.",
            fmt=1, min_hist=120, clip_pandemic=True, pct_of="gdp"),
        "OP11": _build_fred_line(
            "OP11", "tdsp", "Debt service ratio", "%", "quarterly", "FRED TDSP",
            "Household debt-service payments as a share of disposable income.",
            "**What it shows.** Required household debt payments — mortgage plus consumer "
            "— as a share of disposable income. It measures how burdened household "
            "balance sheets are by debt service; rising readings squeeze spending "
            "power.\n\n"
            "**How it's computed.** The Federal Reserve's TDSP series (total debt-service "
            "payments ÷ disposable personal income), quarterly.\n\n"
            "**Caveats.** Quarterly and released with a lag.", fmt=1),
    }


def build() -> dict[str, bool]:
    out = {"OP1": build_op1(), "OP2": build_op2(), "OP3": build_op3()}
    out.update(build_op567())
    out.update(build_households())
    return out
