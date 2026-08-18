"""ipo.py — IPO issuance pace + tracker tables (Issuance panel, §4 IS9/IS10).

build_pace() produces the year-to-date issuance pace comparison: cumulative
gross proceeds (and deal count) by day-of-year, prior years overlaid on the
current year, on the Ritter-comparable operating-company universe (CIO
2026-07-21). Each comparison year's universe is anchored to the Jay Ritter
roster; dollar figures come from Bloomberg (the saved EQS export, plus a live
per-ticker gap-fill for roster deals the export missed — pull.ipo).

Mega-deals (> $20B, e.g. SpaceX ~$75B in 2026) are held in a separate series so
one deal doesn't flatten the rest (spec §2.6 / Part 1 §7.4).

Day-of-year alignment maps each pricing date onto a common reference calendar
(leap year 2000) by month-day; Feb 29 folds into Feb 28. All years are truncated
at the current day-of-year so the comparison is like-for-like "through today".
"""
from __future__ import annotations

import os as _os
from datetime import date

import pandas as pd

from .. import store

# CPI adjustment to 2026 dollars, applied to the IS9 dollar series only (not the
# IS10 count). Current year = 1.0.
INFLATION_TO_2026 = {2000: 1.95, 2021: 1.24}
COMPARISON_YEARS = [2000, 2021]  # historical; current year appended live
REF_YEAR = 2000                  # leap reference calendar for day-of-year overlay


def _ref_date(ts: pd.Timestamp) -> date:
    """Map a pricing date onto the REF_YEAR calendar by month-day (Feb 29 → 28)."""
    m, d = ts.month, ts.day
    if m == 2 and d == 29:
        d = 28
    return date(REF_YEAR, m, d)


def _historical_deals(year: int, roster: pd.DataFrame):
    """Ritter-anchored deal rows for a historical year. Prefers a hand-built
    Ritter × Bloomberg reconciliation file (complete universe, gap-filled
    proceeds) when present; otherwise falls back to the raw EQS export joined to
    the roster. Returns (deals_df, calib)."""
    from ..pull import ipo as pull_ipo
    recon = pull_ipo.read_reconciled(year)
    if recon is not None:
        deals, meta = recon
        return deals, {"year": year, **meta}
    kept, _excl = pull_ipo.read_historical(year)
    kept, calib = pull_ipo.ritter_comparable(kept, year, roster)
    return kept, calib


def _priced_rows() -> pd.DataFrame:
    """Current-year priced deals in tracker Master schema. The live BEQS pull
    (lake table ipo_priced, refreshed by run.pull_all) wins; the cached tracker
    workbook is the fallback when the Terminal was unavailable."""
    from ..pull import ipo as pull_ipo
    live = pull_ipo.latest_priced()
    if live is not None and not live.empty:
        return live.copy()
    m = pull_ipo.read_tracker_master()
    m.columns = [str(c).strip() for c in m.columns]
    return m[m["Status"].astype(str).str.strip() == "Priced"].copy()


def _current_year_deals():
    """Current-year priced lane for the pace charts. Ritter-comparable ≈ Vehicle
    Type 'Operating Co' (the BEQS name-pattern classification, pull.ipo)."""
    p = _priced_rows()
    p["ipo_dt"] = pd.to_datetime(p["Key Date"], errors="coerce")
    p["proceeds_mm"] = pd.to_numeric(p["Raise ($mm)"], errors="coerce")
    p = p[p["ipo_dt"].notna() & p["proceeds_mm"].notna()].copy()
    p["ritter_comparable"] = p["Vehicle Type"].astype(str).str.strip() == "Operating Co"
    return p[["ipo_dt", "proceeds_mm", "ritter_comparable"]]


def _cumulative_series(deals: pd.DataFrame, cutoff_md: tuple[int, int]):
    """Ritter-comparable cumulative-$ and cumulative-count points, truncated at
    cutoff month-day. Every deal is included — SpaceX and other mega-deals show as
    real steps (this is an accurate reflection of realized issuance, not smoothed).
    Returns (dollar_points, count_points)."""
    d = deals[deals["ritter_comparable"]].copy()
    d = d[d["ipo_dt"].notna()]
    # truncate at cutoff month-day
    md = list(zip(d["ipo_dt"].dt.month, d["ipo_dt"].dt.day))
    keep = [(m, day) <= cutoff_md for (m, day) in md]
    d = d[pd.Series(keep, index=d.index)].sort_values("ipo_dt")
    d["ref"] = d["ipo_dt"].map(_ref_date)
    # one cumulative point per distinct ref date
    cum_usd, cum_cnt = 0.0, 0
    dpts, cpts = [], []
    for ref, grp in d.groupby("ref", sort=True):
        cum_usd += grp["proceeds_mm"].sum()
        cum_cnt += len(grp)
        label = ref.strftime("%m-%d")  # month-day; sorts chronologically, no fake year
        dpts.append({"date": label, "value": round(cum_usd / 1000, 3)})  # $B
        cpts.append({"date": label, "value": cum_cnt})
    return dpts, cpts


def build_pace() -> bool:
    from ..pull import ipo as pull_ipo
    roster = pull_ipo.read_ritter_roster()

    today = date.today()
    cutoff_md = (today.month, today.day)

    year_deals = {}
    calib = {}
    for y in COMPARISON_YEARS:
        deals, rep = _historical_deals(y, roster)
        year_deals[y] = deals
        calib[y] = rep
    cur_year = today.year
    year_deals[cur_year] = _current_year_deals()

    usd_series, cnt_series = [], []
    # role: current year is the 'avos' highlight; priors are neutral palette.
    # Comparison years run the FULL calendar (see the whole-year shape); only the
    # current year stops at today's day-of-year.
    for y in sorted(year_deals):
        cut = cutoff_md if y == cur_year else (12, 31)
        dpts, cpts = _cumulative_series(year_deals[y], cut)
        role = "avos" if y == cur_year else "benchmark" if y == max(COMPARISON_YEARS) else None
        # inflate prior-year dollars to 2026 (IS9 only; count series untouched)
        infl = INFLATION_TO_2026.get(y, 1.0)
        udpts = ([{"date": p["date"], "value": round(p["value"] * infl, 3)} for p in dpts]
                 if infl != 1.0 else dpts)
        usd_series.append({"name": str(y), "role": role or "series", "kind": "line",
                           "unit": "$B", "estimated_from": None, "points": udpts})
        cnt_series.append({"name": str(y), "role": role or "series", "kind": "line",
                           "unit": "deals", "estimated_from": None, "points": cpts})

    asof = today.isoformat()
    latest_usd = usd_series[-1]["points"][-1]["value"] if usd_series[-1]["points"] else None

    # provenance for reconciled (Ritter × Bloomberg) years with an estimated tail
    plug_txt = ""
    for y in COMPARISON_YEARS:
        c = calib.get(y, {})
        if c.get("estimated_plug_bn"):
            plug_txt += (f" {y} is Ritter-anchored (${c['operating_co_proceeds_bn']:.1f}B "
                         f"nominal across {c['operating_co_deals']} operating-cos), of which "
                         f"${c['estimated_plug_bn']:.1f}B across {c['estimated_plug_deals']} "
                         f"tail deals is estimated.")

    store.write_display("IS9", {
        "id": "IS9", "name": "IPO issuance pace — cumulative $ (YTD vs prior years)",
        "panel": "issuance", "source": "Bloomberg EQS + Ritter roster",
        "cadence": "daily", "asof": asof, "unit": " $B",
        "series": usd_series,
        "tile": {"value": latest_usd, "delta": None, "percentile": None},
        "provenance": "ipo_pace",
        "tooltip": ("Cumulative operating-company IPO proceeds by day-of-year, in real "
                    "2026 dollars — this year (through today) vs prior years full-year."),
        "notes": (
            "**What it shows.** The pace of IPO issuance this year set against prior "
            "years — cumulative operating-company IPO proceeds by day of the year, in "
            "real 2026 dollars. This year runs through today while the comparison years "
            "show the full calendar, so you can read at a glance whether the current "
            "year is running ahead of or behind past cycles.\n\n"
            "**How it's computed.** A Ritter-comparable operating-company universe — "
            "excluding SPACs, ADRs, REITs, closed-end funds, banks, and unit offerings, "
            "with an offer price of at least $5. Each year is anchored to the Jay Ritter "
            "IPO roster, and proceeds are offer price × shares from Bloomberg — this "
            "year's deals refresh live from the Terminal on every run, so the curve "
            "picks up a new listing the day after it prices. Prior-year "
            "dollars are inflation-adjusted to 2026 (×1.95 for 2000, ×1.24 for 2021) so "
            "every curve is in real 2026 dollars, and every deal is included — mega-deals "
            "such as SpaceX (~$75B) show as real steps rather than being smoothed "
            "away.\n\n"
            "**Caveats.** The *real 2026 $* badge marks the inflation adjustment; older "
            "Ritter-anchored years carry an estimated tail for deals the roster leaves "
            "incomplete." + plug_txt),
        "status": {"level": "uncalibrated", "label": "real 2026 $"},
    })

    store.write_display("IS10", {
        "id": "IS10", "name": "IPO issuance pace — cumulative deal count (YTD vs prior years)",
        "panel": "issuance", "source": "Bloomberg EQS + Ritter roster",
        "cadence": "daily", "asof": asof, "unit": " deals",
        "series": cnt_series,
        "tile": {"value": (cnt_series[-1]["points"][-1]["value"]
                           if cnt_series[-1]["points"] else None),
                 "delta": None, "percentile": None},
        "provenance": "ipo_pace",
        "tooltip": ("Cumulative operating-company IPO count by day-of-year — this year "
                    "(through today) vs prior years shown full-year."),
        "notes": (
            "**What it shows.** The same issuance-pace comparison as IS9, but counting "
            "deals rather than dollars — cumulative operating-company IPO count by day of "
            "the year, this year against prior years. Reading it next to IS9 separates a "
            "few mega-deals from a genuinely broad issuance wave.\n\n"
            "**How it's computed.** The companion count view to IS9, built on the same "
            "Ritter-comparable universe; comparison years run the full calendar and the "
            "current year stops at today.\n\n"
            "**Caveats.** Same universe and the same Ritter-anchoring caveats as IS9."
        ),
    })

    def _ncomp(c):
        return c.get("operating_co_deals", c.get("ritter_comparable", "?"))
    store.log_run("compute:ipo", "detail",
                  " ".join(f"{y}:{_ncomp(calib.get(y, {}))}comp" for y in COMPARISON_YEARS))
    return True


# canonical column order for the tracker tables; unknown columns still ride
# along (spec §8.2 — a new run with extra columns must not break ingest).
_TRACKER_COLS = [
    "Company", "Status", "Stage", "Tier", "Ticker", "Sector", "Vehicle Type",
    "Key Date", "Offer/Target Window", "Raise ($mm)", "Valuation ($mm)",
    "Valuation Basis", "Since Offer (%)", "Since Open (%)", "Source", "As Of",
]
_MONEY_COLS = {"Raise ($mm)", "Valuation ($mm)"}
_FRAC_COLS = {"Since Offer (%)", "Since Open (%)"}
_DATE_COLS = {"Key Date", "As Of"}


def _ticker_root(t) -> str:
    s = str(t).replace(" Equity", "").replace(" US", "").strip().upper()
    return s.split("/")[0]


def _since_open_map() -> dict:
    """Return {ticker_root: since_open_fraction} derived from the tracker's
    Priced_<year> tab. Since-open = return from the first-day OPEN price (what a
    retail buyer at the open earned) = (1+since_offer)/(1+open_vs_offer) − 1. The
    Master tab lacks the first-day-open field, so we source it here."""
    from ..pull import ipo as pull_ipo
    path = pull_ipo._tracker_path()
    if not path:
        return {}
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = next((s for s in xl.sheet_names if s.lower().startswith("priced")), None)
    if not sheet:
        return {}
    pr = xl.parse(sheet)
    pr.columns = [str(c).strip() for c in pr.columns]
    so = pd.to_numeric(pr.get("Since Offer (%)"), errors="coerce")
    ovo = pd.to_numeric(pr.get("First-Day Open vs Offer (%)"), errors="coerce")
    since_open = (1 + so) / (1 + ovo) - 1
    out = {}
    for t, v in zip(pr.get("Ticker", []), since_open):
        if pd.notna(v):
            out[_ticker_root(t)] = float(v)
    return out


def _tracker_frame() -> tuple[pd.DataFrame, dict]:
    """Master rows, best source per lane: live BEQS for priced, the live Claude
    web-search research for the forward pipeline, the cached tracker workbook for
    whichever of those two didn't pull (and for anything neither lane covers).
    Returns (frame, provenance) — provenance is stamped onto the display JSON so
    the page can say which lane is live."""
    from ..pull import ipo as pull_ipo
    wb = pull_ipo.read_tracker_master()
    wb.columns = [str(c).strip() for c in wb.columns]
    wb_status = wb["Status"].astype(str).str.strip()
    wb_name = _os.path.basename(pull_ipo._tracker_path() or "tracker workbook")
    prov = {}

    priced = pull_ipo.latest_priced()
    if priced is not None and not priced.empty:
        prov["priced"] = f"Bloomberg BEQS {pull_ipo.PRICED_SCREEN} (live)"
    else:
        priced = wb[wb_status == "Priced"]
        prov["priced"] = wb_name

    pipeline = pull_ipo.latest_pipeline()
    if pipeline is not None and not pipeline.empty:
        prov["pipeline"] = f"Claude web-search research ({pull_ipo.PIPELINE_MODEL})"
    else:
        pipeline = wb[wb_status.isin(["Pipeline", "Withdrawn"])]
        prov["pipeline"] = wb_name

    # cross-lane check: a name can't be both forward-looking and already priced.
    # The research pass works from press coverage and lags the tape, so it can
    # carry a company the Terminal already shows as priced (PayPay, 2026-07-31).
    # The priced lane is the harder fact, so it wins.
    from ..pull.ipo import roster_key
    priced_keys = set(priced["Company"].map(roster_key)) - {""}
    if priced_keys and not pipeline.empty:
        clash = pipeline["Company"].map(roster_key).isin(priced_keys)
        for name in pipeline.loc[clash, "Company"]:
            store.log_run("compute:ipo", "check",
                          f"dropped '{name}' from the pipeline board — already priced")
        pipeline = pipeline[~clash]

    other = wb[~wb_status.isin(["Priced", "Pipeline", "Withdrawn"])]
    frame = pd.concat([priced, pipeline, other], ignore_index=True)
    # lake bookkeeping, not a tracker field — the display layer passes unknown
    # columns straight through (§8.2), so drop it before it reaches the page
    frame = frame.drop(columns=["pulled_at"], errors="ignore")
    return frame, prov


def build_ipo_tables() -> bool:
    """Emit the IPO tracker rows (build_data/ipo_tracker.json) for the collapsible
    tabular section — priced table + forward pipeline board. One row per company;
    KPIs / filtering / sorting happen client-side so a new run with extra columns
    needs no code change (spec §8.2). Empty → null, never 0; Since Offer stays a
    fraction; money is USD millions (§3)."""
    m, prov = _tracker_frame()

    cols = [c for c in _TRACKER_COLS if c in m.columns]
    cols += [c for c in m.columns if c not in cols]  # schema-growth passthrough

    def _clean(col, v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        if isinstance(v, str) and not v.strip():
            return None
        if col in _MONEY_COLS or col in _FRAC_COLS:
            n = pd.to_numeric(v, errors="coerce")
            return None if pd.isna(n) else float(n)
        if col in _DATE_COLS:
            t = pd.to_datetime(v, errors="coerce")
            return None if pd.isna(t) else t.strftime("%Y-%m-%d")
        return str(v).strip()

    rows = [{c: _clean(c, r.get(c)) for c in cols} for _, r in m.iterrows()]
    # Since Open (%) — the BEQS pull derives it per row; the workbook's Master tab
    # doesn't carry it, so fall back to the Priced_<year> tab's first-day-open field.
    open_map = _since_open_map()
    for row in rows:
        if row.get("Status") != "Priced":
            row["Since Open (%)"] = None
        elif row.get("Since Open (%)") is None:
            row["Since Open (%)"] = open_map.get(_ticker_root(row.get("Ticker")))
    if "Since Open (%)" not in cols:  # derived col — keep the manifest accurate
        i = cols.index("Since Offer (%)") + 1 if "Since Offer (%)" in cols else len(cols)
        cols.insert(i, "Since Open (%)")
    asof_vals = [row["As Of"] for row in rows if row.get("As Of")]
    asof = max(asof_vals) if asof_vals else date.today().isoformat()

    store.write_display("ipo_tracker", {
        "id": "ipo_tracker", "kind": "ipo_tracker", "asof": asof,
        "columns": cols, "rows": rows,
        "stage_order": ["Terms set", "Public S-1", "Confidential filing",
                        "Stated intent", "Rumored", "Withdrawn"],
        "source": f"priced: {prov['priced']} · pipeline: {prov['pipeline']}",
        "provenance_by_lane": prov,
    })
    store.log_run("compute:ipo", "detail",
                  f"tracker priced={prov['priced']} pipeline={prov['pipeline']}")
    return True


def build() -> dict[str, bool]:
    return {"IS9/IS10": build_pace(), "ipo_tracker": build_ipo_tables()}
