"""ipo.py — IPO tracker inputs (issuance pace chart + tabular section).

Sourcing model (CIO 2026-07-21):
  * HISTORICAL comparison years (2000, 2021, …) — deal-level Bloomberg EQS
    exports saved once in data/IPO_<YYYY>.xlsx. Cleaned per spec §2.4 and joined
    to the Jay Ritter roster (data/IPO-age.xlsx) to define the operating-company
    "Ritter-comparable" universe (§2.5). Computed once; cached.
  * 2026 PRICED deals — pulled LIVE from the Terminal (saved BEQS screen), same
    universe the historical exports came from. Soft-fails to the cached tracker
    workbook when the Terminal/screen is unavailable.
  * 2026 forward PIPELINE — researched via a Claude API web-search call (curated
    rumored/intent names + last-private valuations are not on Bloomberg/EDGAR).
    Soft-fails to the cached tracker workbook when no ANTHROPIC key is present.

Every entry point returns tidy DataFrames; failures raise (the run's _safe
wrapper logs and falls back to last-good), so nothing here kills a run.
"""
from __future__ import annotations

import os
import re
from datetime import date

import pandas as pd

from .. import store

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(BASE, "data")

# live-market columns (§2.4.3) — null for dead names, actively wrong for recycled
# tickers; never joined onto historical rows.
_EQS_WANT = ["Ticker", "Name", "IPO Dt", "IPO Sh Px", "IPO Sh Offered", "GICS Sector"]

# name-pattern tagging (§2.4.4). Ritter join is the authoritative operating-co
# filter; these are best-effort vehicle classes for the all-in series.
_SPAC_RE = re.compile(r"ACQUISITION|MERGER CORP|BLANK CHECK|CAPITAL CORP|SPAC", re.I)
_BANK_RE = re.compile(r"BANCORP|BANCSHARES|FINANCIAL CORP|SAVINGS|BANCERT|BANK", re.I)
_FUND_RE = re.compile(r"\bFUND\b|TRUST|PORTFOLIO|ETF|INCOME FD|VAR RT|PREF", re.I)

_SUFFIX_RE = re.compile(
    r"\b(INC|CORP|CORPORATION|CO|COMPANY|LLC|LP|LTD|PLC|HOLDINGS?|GROUP|"
    r"THE|SA|NV|AG|CLASS [A-Z]|CL [A-Z]|COM|ORD|ADR|ADS)\b", re.I)


def _norm_ticker(t: str) -> str:
    """'RGP US Equity' / 'RGP US' → 'RGP'. Delisted placeholders (0618171D US)
    normalize the same way; they are valid rows (spec §2.2)."""
    if not isinstance(t, str):
        return ""
    return t.replace(" Equity", "").replace(" US", "").strip().upper()


def _norm_name(n: str) -> str:
    """Fuzzy-join key: uppercase, drop entity suffixes and punctuation."""
    if not isinstance(n, str):
        return ""
    s = _SUFFIX_RE.sub(" ", n.upper())
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _vehicle_class(name: str, shares: float) -> str:
    if _SPAC_RE.search(name or ""):
        return "SPAC"
    if _FUND_RE.search(name or ""):
        return "Fund/Vehicle"
    # small-share bank/thrift heuristic (§2.4.4)
    if _BANK_RE.search(name or "") and (shares or 0) < 10_000_000:
        return "Bank/Thrift"
    return "Operating Co"


def _find_header_row(raw: pd.DataFrame) -> int:
    """Locate the EQS header row (contains 'IPO Dt' and 'IPO Sh Px'). The export
    carries two metadata rows above it and a '(NNN securities)' grouping row below."""
    for i in range(min(12, len(raw))):
        row = {str(c).strip() for c in raw.iloc[i].tolist()}
        if "IPO Dt" in row and "IPO Sh Px" in row:
            return i
    raise ValueError("EQS header row (with 'IPO Dt'/'IPO Sh Px') not found")


def read_historical(year: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Clean an IPO_<year>.xlsx EQS export. Returns (kept, exclusions).

    `kept` columns: ticker, ticker_root, name, name_key, ipo_dt (Timestamp),
    offer_px, shares, proceeds_mm, vehicle_class, gics_sector, penny (bool).
    Cleaning rules 2.4.1–2.4.6 are all logged, never silent (spec §2.8.6)."""
    path = os.path.join(DATA_DIR, f"IPO_{year}.xlsx")
    if not os.path.exists(path):
        raise FileNotFoundError(f"missing historical export {path}")
    raw = pd.read_excel(path, header=None, engine="openpyxl")
    hdr = _find_header_row(raw)
    cols = [str(c).strip() for c in raw.iloc[hdr].tolist()]
    df = raw.iloc[hdr + 1:].copy()
    df.columns = cols
    # first Ticker/Name pair are the offering-time identifiers we keep; a second
    # Ticker/Name pair (live-market join) may repeat the header names — dedupe to
    # the first occurrence so _EQS_WANT selects the offering-time columns.
    df = df.loc[:, ~pd.Index(df.columns).duplicated()]
    df = df[[c for c in _EQS_WANT if c in df.columns]].copy()
    df = df.rename(columns={
        "Ticker": "ticker", "Name": "name", "IPO Dt": "ipo_dt",
        "IPO Sh Px": "offer_px", "IPO Sh Offered": "shares", "GICS Sector": "gics_sector"})

    # drop the '(NNN securities)' grouping row and any fully-blank rows
    df = df[df["ticker"].notna() & ~df["ticker"].astype(str).str.contains(
        r"securities\)", case=False, na=False)]

    excl = []

    # 2.4.1 keep only US listings
    df["ticker_root"] = df["ticker"].map(_norm_ticker)
    is_us = df["ticker"].astype(str).str.contains(r"\bUS\b", na=False)
    for _, r in df[~is_us].iterrows():
        excl.append({"ticker": r["ticker"], "name": r["name"], "reason": "non_us_listing"})
    df = df[is_us].copy()

    # numeric coercion; 2.4.2 drop rows without offer terms
    df["offer_px"] = pd.to_numeric(df["offer_px"], errors="coerce")
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce")
    no_terms = df["offer_px"].isna() | df["shares"].isna()
    for _, r in df[no_terms].iterrows():
        excl.append({"ticker": r["ticker"], "name": r["name"],
                     "reason": "no_offer_terms (corporate action, not a bookbuilt IPO)"})
    df = df[~no_terms].copy()

    df["ipo_dt"] = pd.to_datetime(df["ipo_dt"], errors="coerce")
    df = df[df["ipo_dt"].notna()].copy()
    df["proceeds_mm"] = df["offer_px"] * df["shares"] / 1e6
    df["name_key"] = df["name"].map(_norm_name)
    df["vehicle_class"] = [
        _vehicle_class(n, s) for n, s in zip(df["name"], df["shares"])]
    df["penny"] = df["offer_px"] < 5.0  # 2.4.6 flag (kept in all-in series)

    # 2.4.5 dedupe: same company twice → keep the larger-proceeds row
    df = df.sort_values("proceeds_mm", ascending=False)
    dup = df.duplicated(subset=["ticker_root"], keep="first")
    for _, r in df[dup].iterrows():
        excl.append({"ticker": r["ticker"], "name": r["name"],
                     "reason": "duplicate_ticker (kept larger-proceeds row)"})
    df = df[~dup].copy()

    df = df.sort_values("ipo_dt").reset_index(drop=True)
    exclusions = pd.DataFrame(excl, columns=["ticker", "name", "reason"])
    return df, exclusions


def read_reconciled(year: int):
    """Prefer a hand-built Ritter × Bloomberg reconciliation file if one exists for
    `year` (e.g. IPO_2000_Ritter_x_Bloomberg_v3.xlsx). Its Master_<year> sheet is
    already Ritter-anchored (full operating-co universe + offer dates) with proceeds
    gap-filled from Bloomberg + cited web sources, so it supersedes the raw EQS
    export. Returns (deals_df, meta) or None if no such file is present.

    deals_df columns: ipo_dt, proceeds_mm, ritter_comparable (bool). meta reports
    coverage and how much of the total is estimated 'plug' rather than sourced."""
    import glob
    pat = os.path.join(DATA_DIR, f"IPO_{year}_Ritter_x_Bloomberg*.xlsx")
    hits = sorted(f for f in glob.glob(pat) if not os.path.basename(f).startswith("~$"))
    if not hits:
        return None
    path = hits[-1]  # highest version suffix sorts last (…_v2, _v3)
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheet = next((s for s in xl.sheet_names if s.lower().startswith("master")), None)
    if sheet is None:
        raise ValueError(f"{os.path.basename(path)} has no Master sheet")
    m = xl.parse(sheet)
    m.columns = [str(c).strip() for c in m.columns]

    adr = pd.to_numeric(m["ADR Flag"], errors="coerce")
    dt = pd.to_datetime(m["Ritter Offer Date"], errors="coerce")
    # Final Proceeds folds Bloomberg/web values + estimated plugs into a complete,
    # Ritter-anchored series; fall back to Best Proceeds if the column is absent.
    pcol = "Final Proceeds ($mm)" if "Final Proceeds ($mm)" in m.columns else "Best Proceeds ($mm)"
    proceeds = pd.to_numeric(m[pcol], errors="coerce")
    plug = pd.to_numeric(m.get("Est Plug ($mm)", pd.Series(index=m.index)), errors="coerce")

    deals = pd.DataFrame({
        "ipo_dt": dt,
        "proceeds_mm": proceeds,
        "ritter_comparable": adr == 1,
    })
    deals = deals[deals["ipo_dt"].notna() & deals["proceeds_mm"].notna()].reset_index(drop=True)

    op = deals["ritter_comparable"]
    meta = {
        "source": os.path.basename(path),
        "proceeds_column": pcol,
        "operating_co_deals": int(op.sum()),
        "operating_co_proceeds_bn": round(float(deals.loc[op, "proceeds_mm"].sum()) / 1000, 1),
        "estimated_plug_deals": int(plug.notna().sum()),
        "estimated_plug_bn": round(float(plug.sum()) / 1000, 1) if plug.notna().any() else 0.0,
    }
    return deals, meta


def read_ritter_roster() -> pd.DataFrame:
    """Jay Ritter roster (data/IPO-age.xlsx, sheet 1975-2025). Returns columns
    year, ticker_root, name_key, adr (int), where '.' → null (spec §2.2)."""
    path = os.path.join(DATA_DIR, "IPO-age.xlsx")
    if not os.path.exists(path):
        raise FileNotFoundError(f"missing Ritter roster {path}")
    r = pd.read_excel(path, sheet_name="1975-2025", engine="openpyxl")
    r = r.rename(columns={
        "offer date": "offer_date", "IPO name": "name", "Ticker": "ticker",
        "ADR (2=ADR)": "adr"})
    r = r.replace(".", pd.NA)
    r["offer_date"] = pd.to_numeric(r["offer_date"], errors="coerce")
    r = r[r["offer_date"].notna()].copy()
    r["year"] = (r["offer_date"] // 10000).astype(int)
    r["adr"] = pd.to_numeric(r["adr"], errors="coerce")
    r["ticker_root"] = r["ticker"].map(_norm_ticker)
    r["name_key"] = r["name"].map(_norm_name)
    return r[["year", "ticker_root", "name_key", "adr"]]


def ritter_comparable(kept: pd.DataFrame, year: int,
                      roster: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Flag the Ritter-comparable subset (§2.5): EQS rows matching a roster row
    for `year` with ADR flag == 1 (domestic operating company). Match on ticker
    first, then normalized name. Returns (kept+`ritter_comparable` bool col, report)."""
    ry = roster[roster["year"] == year]
    op = ry[ry["adr"] == 1]  # 1 = domestic operating co
    op_tickers = set(op["ticker_root"]) - {""}
    op_names = set(op["name_key"]) - {""}

    by_ticker = kept["ticker_root"].isin(op_tickers)
    by_name = kept["name_key"].isin(op_names)
    kept = kept.copy()
    kept["ritter_comparable"] = by_ticker | by_name

    # fuzzy pass (§2.5.1) for the remainder: match unresolved EQS rows to an
    # operating-co roster name by high-threshold sequence similarity. Guards
    # against false positives with a 0.88 ratio floor + shared leading token.
    from difflib import SequenceMatcher
    op_name_list = sorted(op_names)
    unresolved = kept.index[~kept["ritter_comparable"]]
    for idx in unresolved:
        nk = kept.at[idx, "name_key"]
        if not nk:
            continue
        lead = nk.split(" ")[0]
        best = 0.0
        for cand in op_name_list:
            if cand.split(" ")[0] != lead:      # cheap prefilter
                continue
            ratio = SequenceMatcher(None, nk, cand).ratio()
            if ratio > best:
                best = ratio
        if best >= 0.88:
            kept.at[idx, "ritter_comparable"] = True

    all_tickers = set(ry["ticker_root"]) - {""}
    all_names = set(ry["name_key"]) - {""}
    matched_any = kept["ticker_root"].isin(all_tickers) | kept["name_key"].isin(all_names)
    report = {
        "year": year,
        "eqs_rows": int(len(kept)),
        "ritter_roster_rows": int(len(ry)),
        "ritter_operating_co_rows": int(len(op)),
        "matched_to_roster": int(matched_any.sum()),
        "ritter_comparable": int(kept["ritter_comparable"].sum()),
        "match_rate_pct": round(100 * matched_any.sum() / max(len(kept), 1), 1),
    }
    return kept, report


# ---- 2026 live sources (soft-fail; see module docstring) -------------------

def _tracker_path() -> str | None:
    """Newest IPO_Tracker_*.xlsx in data/ (fallback + tabular-section source)."""
    files = sorted(f for f in os.listdir(DATA_DIR)
                   if f.startswith("IPO_Tracker_") and f.endswith(".xlsx")
                   and not f.startswith("~$"))
    return os.path.join(DATA_DIR, files[-1]) if files else None


def read_tracker_master() -> pd.DataFrame:
    """The tracker workbook's Master tab — one row per company, matching the Part 1
    flat-file contract. Used for the tabular section, and as the 2026 fallback
    when the live Bloomberg/Claude pulls are unavailable."""
    path = _tracker_path()
    if not path:
        raise FileNotFoundError("no IPO_Tracker_*.xlsx in data/")
    m = pd.read_excel(path, sheet_name="Master", engine="openpyxl")
    m.columns = [str(c).strip() for c in m.columns]
    return m


def pull_priced_bbg(year: int, screen: str = None):
    """2026 priced deals, live from a saved Terminal EQS screen (default IPO_<year>).
    Raises if the Terminal or screen is unavailable — caller soft-fails to cache."""
    from xbbg import blp  # raises ImportError off-Terminal → logged, falls back
    screen = screen or f"IPO_{year}"
    df = blp.beqs(screen)
    if df is None or df.empty:
        raise RuntimeError(f"BEQS screen '{screen}' returned no rows")
    return df
