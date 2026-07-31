"""ipo.py — IPO tracker inputs (issuance pace chart + tabular section).

Sourcing model (CIO 2026-07-21):
  * HISTORICAL comparison years (2000, 2021, …) — deal-level Bloomberg EQS
    exports saved once in data/IPO_<YYYY>.xlsx. Cleaned per spec §2.4 and joined
    to the Jay Ritter roster (data/IPO-age.xlsx) to define the operating-company
    "Ritter-comparable" universe (§2.5). Computed once; cached.
  * CURRENT-YEAR PRICED deals — pulled LIVE from the Terminal each run
    (pull_priced_bbg → saved BEQS screen IPO_THIS_YEAR), normalized to the
    tracker Master schema and appended to the lake. Soft-fails to the last lake
    pull, then to the cached tracker workbook.
  * FORWARD PIPELINE — researched LIVE each run via the Claude API's web-search
    tool (pull_pipeline: search pass → schema-constrained structuring pass);
    curated rumored/intent names and last-private valuations are on neither
    Bloomberg nor EDGAR. Soft-fails the same way; skips the call outright when
    the cached pull is younger than PIPELINE_MAX_AGE_DAYS.

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


# tracker Master column order (mirrored in compute.ipo._TRACKER_COLS)
_MASTER_COLS = [
    "Company", "Status", "Stage", "Tier", "Ticker", "Sector", "Vehicle Type",
    "Key Date", "Offer/Target Window", "Raise ($mm)", "Valuation ($mm)",
    "Valuation Basis", "Since Offer (%)", "Since Open (%)", "Source", "As Of",
]

# Stage → Tier, the mapping the hand-built tracker used (A priced … D intent/rumor)
_TIER_BY_STAGE = {
    "Priced": "A", "Terms set": "B", "Public S-1": "B",
    "Confidential filing": "C", "Stated intent": "D", "Rumored": "D",
}

PRICED_SCREEN = "IPO_THIS_YEAR"   # saved Terminal EQS screen (verified 2026-07-31)
PRICED_TABLE = "ipo_priced"
PIPELINE_TABLE = "ipo_pipeline"


def _patch_beqs_transform():
    """xbbg 0.x on this machine pivots the BEQS response through narwhals, which
    has no `pivot` for its pyarrow backend — every blp.beqs() call dies with
    NotImplementedError (discovered 2026-07-31; the bloomberg-mcp venv escapes it
    only by running an older xbbg with no narwhals). Swap in the identical pivot
    done in pandas. Idempotent; safe once the upstream backend gap is closed."""
    import pyarrow as pa

    from xbbg.core.strategies import screening as _sc

    if getattr(_sc.BeqsTransformer, "_avos_pandas_pivot", False):
        return

    def _transform(self, raw_data, request, exchange_info, session_window):
        if raw_data.num_rows == 0:
            return pa.table({})
        df = raw_data.to_pandas()
        if "ticker" not in df.columns or "field" not in df.columns:
            return pa.table({})
        wide = df.pivot(index="ticker", columns="field", values="value").reset_index()
        wide.columns = [str(c).lower().replace(" ", "_").replace("-", "_")
                        for c in wide.columns]
        # the screen carries its own 'ticker' field alongside the pivot index
        wide = wide.loc[:, ~pd.Index(wide.columns).duplicated()]
        return pa.Table.from_pandas(wide, preserve_index=False)

    _sc.BeqsTransformer.transform = _transform
    _sc.BeqsTransformer._avos_pandas_pivot = True


def pull_priced_bbg(year: int = None, screen: str = None) -> pd.DataFrame:
    """Priced deals for `year`, live from the saved Terminal EQS screen, normalized
    to the tracker Master schema and appended to the lake (`ipo_priced`).

    Raises if the Terminal or the screen is unavailable — run.py's _safe wrapper
    logs it and compute falls back to the cached tracker workbook."""
    year = year or date.today().year
    screen = screen or PRICED_SCREEN
    _patch_beqs_transform()
    from xbbg import blp  # raises ImportError off-Terminal → logged, falls back
    raw = blp.beqs(screen)
    if raw is None or raw.empty:
        raise RuntimeError(f"BEQS screen '{screen}' returned no rows")

    def col(*names):
        """First matching column, tolerant of xbbg's snake_case vs the raw labels."""
        norm = {str(c).lower().replace(" ", "_"): c for c in raw.columns}
        for n in names:
            key = n.lower().replace(" ", "_")
            if key in norm:
                return raw[norm[key]]
        return pd.Series(index=raw.index, dtype="object")

    px = pd.to_numeric(col("ipo_sh_px", "IPO Sh Px"), errors="coerce")
    sh = pd.to_numeric(col("ipo_sh_offered", "IPO Sh Offered"), errors="coerce")
    cap = pd.to_numeric(col("market_cap", "Market Cap"), errors="coerce")
    since_offer = pd.to_numeric(
        col("ipo_offer_px_lst_cls_%_chg", "IPO Offer Px Lst Cls % Chg"), errors="coerce") / 100.0
    open_vs_offer = pd.to_numeric(
        col("ipo_offer_px_1st_opn_px_%_chg", "IPO Offer Px 1st Opn Px % Chg"), errors="coerce") / 100.0
    name = col("name", "Name").astype("object")
    dt = pd.to_datetime(col("ipo_dt", "IPO Dt"), errors="coerce")

    out = pd.DataFrame({
        "Company": name,
        "Status": "Priced",
        "Stage": "Priced",
        "Tier": "A",
        "Ticker": col("ticker", "Ticker").astype("object"),
        "Sector": col("gics_sector", "GICS Sector").astype("object"),
        "Vehicle Type": [_vehicle_class(n, s) for n, s in zip(name, sh)],
        "Key Date": dt.dt.strftime("%Y-%m-%d"),
        "Offer/Target Window": None,
        "Raise ($mm)": (px * sh / 1e6).round(2),
        "Valuation ($mm)": (cap / 1e6).round(2),
        "Valuation Basis": "Current market cap (Bloomberg)",
        "Since Offer (%)": since_offer,
        # return from the first-day OPEN (what a buyer at the open earned), same
        # identity compute/ipo.py used against the workbook's Priced tab
        "Since Open (%)": (1 + since_offer) / (1 + open_vs_offer) - 1,
        "Source": f"Bloomberg BEQS {screen}",
        "As Of": date.today().isoformat(),
    })
    # the screen is "this year"; keep the guard so a stale screen can't leak deals
    out = out[dt.dt.year.eq(year).fillna(False)].copy()
    out = out[out["Key Date"].notna() & out["Raise ($mm)"].notna()]
    if out.empty:
        raise RuntimeError(f"BEQS screen '{screen}' returned no {year} priced deals")
    out = out.sort_values("Key Date").reset_index(drop=True)
    store.append_parquet(PRICED_TABLE, out[_MASTER_COLS])
    return out


# ---- forward pipeline (Claude API web-search research) ----------------------

PIPELINE_MODEL = "claude-opus-5"
PIPELINE_MAX_AGE_DAYS = 1       # re-research at most once a day (each pass costs)
PIPELINE_ROSTER_DAYS = 21       # keep an unseen name this long before retiring it
# Single-line key file (house convention, §2). `.claude_api_key` is the name in
# use; `.anthropic_api_key` is accepted as an alias. Worktrees don't all carry a
# copy of every key, so fall back to the main checkout's root.
_KEY_NAMES = (".claude_api_key", ".anthropic_api_key")


def _key_roots() -> list[str]:
    roots = [BASE]
    head, tail = os.path.split(BASE)
    while head and head != BASE:            # .../<repo>/.claude/worktrees/<name>
        head, tail = os.path.split(head)
        if tail == ".claude":
            roots.append(head)              # the main checkout above .claude/
            break
    return roots

_GICS_SECTORS = [
    "Information Technology", "Communication Services", "Consumer Discretionary",
    "Consumer Staples", "Financials", "Health Care", "Industrials", "Energy",
    "Materials", "Real Estate", "Utilities", "Unknown",
]
_STAGES = ["Terms set", "Public S-1", "Confidential filing", "Stated intent",
           "Rumored", "Withdrawn"]

_PIPELINE_SCHEMA = {
    "type": "object",
    "properties": {
        "deals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string", "description": "Legal or common company name"},
                    "ticker": {"type": ["string", "null"],
                               "description": "Reserved ticker if disclosed, else null"},
                    "stage": {"type": "string", "enum": _STAGES},
                    "sector": {"type": "string", "enum": _GICS_SECTORS},
                    "vehicle_type": {"type": "string",
                                     "enum": ["Operating Co", "SPAC", "Fund/Vehicle"]},
                    "key_date": {"type": "string",
                                 "description": "YYYY-MM-DD of the most recent confirming "
                                                "event (filing date, announcement, report)"},
                    "window": {"type": "string",
                               "description": "Expected offer window as reported, e.g. "
                                              "'Q4 2026', 'October 2026', 'No date announced'"},
                    "raise_mm": {"type": ["number", "null"],
                                 "description": "Targeted IPO proceeds in USD millions — the "
                                                "size of the OFFERING only. Never a private "
                                                "round size, never a valuation. Null unless a "
                                                "source states the offering size."},
                    "valuation_mm": {"type": ["number", "null"],
                                     "description": "Last known valuation in USD millions"},
                    "valuation_basis": {"type": "string",
                                        "description": "What the valuation_mm figure is — which "
                                                       "round and what date, e.g. 'Series H "
                                                       "post-money 05/28/2026'. Must describe "
                                                       "valuation_mm itself; never restate a "
                                                       "different dollar figure."},
                    "source": {"type": "string",
                               "description": "Publication + date for each claim, "
                                              "semicolon-separated, e.g. 'WSJ 6/8/26; SEC EDGAR S-1 7/6/26'"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "verification": {
                        "type": "string",
                        "enum": ["new", "re-verified", "unchanged", "not-found"],
                        "description": "Against the prior roster supplied in the research: "
                                       "'new' = not previously tracked; 'unchanged' = "
                                       "re-sourced this pass, same facts; 're-verified' = "
                                       "re-sourced and something changed; 'not-found' = on "
                                       "the prior roster but NO current source could be "
                                       "confirmed this pass. Never mark a row re-verified or "
                                       "unchanged on the strength of the prior roster alone.",
                    },
                },
                "required": ["company", "ticker", "stage", "sector", "vehicle_type",
                             "key_date", "window", "raise_mm", "valuation_mm",
                             "valuation_basis", "source", "confidence", "verification"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["deals"],
    "additionalProperties": False,
}


# Opus 5 list price, $/MTok (input / cache-write / cache-read / output) — used
# only to put a dollar figure in the run log so the daily cost is visible.
_PRICE = {"in": 5.0, "cache_w": 6.25, "cache_r": 0.5, "out": 25.0}
_SEARCH_PRICE = 10.0 / 1000    # $/search, server-side web_search


def _log_usage(label: str, usage, searches: int = 0) -> float:
    """Record token usage + estimated cost per pass. Server-tool loops bill every
    iteration's re-read of the accumulated transcript, so input dwarfs output —
    worth watching, hence the log line."""
    u = {k: int(getattr(usage, k, 0) or 0) for k in
         ("input_tokens", "output_tokens", "cache_creation_input_tokens",
          "cache_read_input_tokens")}
    cost = (u["input_tokens"] * _PRICE["in"]
            + u["cache_creation_input_tokens"] * _PRICE["cache_w"]
            + u["cache_read_input_tokens"] * _PRICE["cache_r"]
            + u["output_tokens"] * _PRICE["out"]) / 1e6 + searches * _SEARCH_PRICE
    store.log_run("ipo:pipeline", "usage",
                  f"{label}: in={u['input_tokens']:,} "
                  f"cache_r={u['cache_read_input_tokens']:,} "
                  f"out={u['output_tokens']:,} searches={searches} ~${cost:.2f}")
    return cost


def _anthropic_client():
    """Client keyed from ANTHROPIC_API_KEY, else the gitignored .claude_api_key
    in this checkout's root (or the main checkout's, when run from a worktree)."""
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        for root in _key_roots():
            for name in _KEY_NAMES:
                path = os.path.join(root, name)
                if os.path.exists(path):
                    key = open(path, encoding="utf-8").read().strip()
                    break
            if key:
                break
    if not key:
        raise FileNotFoundError(
            "no Anthropic key: set ANTHROPIC_API_KEY or drop a single-line "
            f".claude_api_key in {' or '.join(_key_roots())}")
    return anthropic.Anthropic(api_key=key)


PIPELINE_VERIFY_BATCH = 12      # roster rows re-checked per pass (see _roster_digest)


def _roster_digest(prev: pd.DataFrame | None, limit: int = PIPELINE_VERIFY_BATCH) -> str:
    """The stalest slice of the roster, as compact text for the research prompt.

    Sizing this matters more than it looks. The budget is `max_uses` searches per
    pass; handing over all 41 names left under one search each, and the pass burnt
    the lot on verification, returned a single usable row and still cost $2.58
    (measured 2026-07-31). So each pass re-checks only the {limit} rows with the
    oldest evidence and spends the rest of the budget hunting new names — a rota
    that comes back round to every name within a few days, at flat cost.

    Deliberately terse, and ordered oldest-first: it is a checklist of claims to
    re-source, not context to be trusted (see the anti-anchoring rules below)."""
    if prev is None or prev.empty:
        return ""
    p = prev.assign(_age=pd.to_datetime(prev["As Of"], errors="coerce"))
    p = p.sort_values("_age", ascending=True, na_position="first").head(limit)
    lines = []
    for _, r in p.iterrows():
        val = ("" if pd.isna(r["Valuation ($mm)"])
               else f" | last-known valuation ${float(r['Valuation ($mm)'])/1000:.1f}B")
        lines.append(f"- {r['Company']} | {r['Stage']} | window as we have it: "
                     f"{r['Offer/Target Window'] or 'none'}{val} | our source: "
                     f"{r['Source'] or 'unknown'} | last confirmed {r['As Of']}")
    return "\n".join(lines)


def _research_prompt(year: int, prev: pd.DataFrame | None = None) -> str:
    today = date.today().isoformat()
    digest = _roster_digest(prev)
    roster_block = ("" if not digest else (
        "\n\nWe already track a roster of names from earlier runs. Below are the ones "
        "whose evidence is OLDEST and due a re-check — not the whole roster. Treat "
        "every line as an UNVERIFIED CLAIM to check, not as established fact: some is "
        "weeks old and some may have been wrong when written. Names not listed here "
        "are being tracked and re-checked on other passes — do not re-report them "
        "unless you happen to find genuine news about them.\n\n"
        f"{digest}\n\n"
        "Do two jobs, in this order:\n\n"
        "(A) Re-source each name above against a CURRENT source. Report what you "
        "actually find now — if the stage advanced, the window slipped, the valuation "
        "moved, or it has since priced or been pulled, say so. If you cannot confirm a "
        "name from any current source this pass, return it with verification "
        "'not-found' and carry the prior figures unchanged rather than inventing an "
        "update. Never restate one of these figures as re-verified on the strength of "
        "this list alone.\n\n"
        "(B) Then spend the REST of your search budget — most of it — hunting names "
        "not on this list. That is where the coverage gap is. Budget roughly one "
        "search per name above for job (A) and leave the remainder for (B).\n"))
    return (
        f"Today is {today}. Research the forward-looking US IPO pipeline: companies "
        f"that have not yet priced but are credibly expected to list on a US exchange "
        f"within roughly the next twelve months.\n\n"
        "Search the web for each of these lanes and report what you find:\n"
        "  1. Public S-1/F-1 filings on SEC EDGAR that have not yet priced (include "
        "     any with terms set — price range and share count filed).\n"
        "  2. Confidential submissions the company or press has disclosed.\n"
        "  3. Publicly stated intent to list from the company or its bankers.\n"
        "  4. Credibly reported (named-publication) rumors of a listing.\n"
        "  5. Withdrawn or postponed deals that were previously in the pipeline "
        "     this year — mark these stage 'Withdrawn'.\n"
        "  6. Large late-stage US-listable private companies that IPO trackers and "
        "     secondary marketplaces (Forge, Caplight, Notice, AccessIPOs, IPOScoop) "
        "     carry as near-term listing candidates — these are usually 'stated "
        "     intent' or 'rumored' and are where the biggest names sit.\n\n"
        "Aim for breadth: the 20-30 most prominent candidates, not just the ones "
        "with filings. A board that lists only filed deals misses the names that "
        "matter most.\n\n"
        "For every company report: legal name; reserved ticker if disclosed; stage "
        "(terms set / public S-1 / confidential filing / stated intent / rumored / "
        "withdrawn); GICS sector; whether it is an operating company, a SPAC, or a "
        "fund vehicle; the date of the most recent confirming event; the expected "
        "offer window exactly as reported; the targeted raise if reported; the last "
        "known private valuation with what that figure is (which round, what date) — "
        "report this for the rumored and stated-intent names too, not just the filed "
        "ones; and the publication and date for each claim.\n\n"
        "Rules: exclude anything that has already priced. Prefer primary sources "
        "(EDGAR filings, company statements) over aggregators, and say which you "
        "used. Do not infer a valuation, a raise, or a date that no source states — "
        "report it as unknown instead. Note explicitly when a valuation is stale.\n\n"
        "Return your findings as a markdown table plus a short notes section for "
        "anything that didn't fit the table."
        + roster_block
    )


def _research(client, year: int, model: str, prev: pd.DataFrame | None = None) -> str:
    """Web-search research pass. Server-side tool use, so the loop only has to
    resume `pause_turn` (the search-iteration cap), never execute a tool."""
    messages = [{"role": "user", "content": _research_prompt(year, prev)}]
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 30}]
    text, cost = [], 0.0
    for i in range(6):  # pause_turn resume cap
        with client.messages.stream(
            model=model,
            max_tokens=32000,
            output_config={"effort": "high"},
            tools=tools,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()
        searches = sum(1 for b in response.content
                       if b.type == "server_tool_use" and b.name == "web_search")
        cost += _log_usage(f"research pass {i + 1}", response.usage, searches)
        text += [b.text for b in response.content if b.type == "text"]
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    if response.stop_reason == "refusal":
        raise RuntimeError("pipeline research refused by the model")
    store.log_run("ipo:pipeline", "usage", f"research total ~${cost:.2f}")
    out = "\n\n".join(t for t in text if t.strip())
    if not out:
        raise RuntimeError("pipeline research returned no text")
    return out


def _structure(client, research: str, model: str) -> dict:
    """Second pass: no tools, schema-constrained — the research text in, rows out.
    Split from the search pass because structured outputs and the search tool's
    citations don't co-exist on one call."""
    import json
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": _PIPELINE_SCHEMA},
        },
        messages=[{"role": "user", "content":
                   "Convert this IPO pipeline research into structured rows. One row per "
                   "company. Carry the reported figures through unchanged — do not round, "
                   "infer, or fill a value the research leaves unknown; use null. Set "
                   "confidence 'high' for an SEC filing or company statement, 'medium' for "
                   "a named-publication report, 'low' for anything thinner.\n\n"
                   f"<research>\n{research}\n</research>"}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("pipeline structuring refused by the model")
    _log_usage("structuring", response.usage)
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _clean_text(v):
    """Model text → display text. Strips the U+FFFD replacement char (scraped
    pages carry mangled dashes through the research pass) and collapses the
    whitespace that leaves behind."""
    if v is None:
        return None
    s = re.sub(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(v))
    s = re.sub(r"\s+", " ", s).strip(" ;-–—")
    return s or None


_MONEY_RE = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s*(bn|b\b|billion|mm|m\b|million)", re.I)
# a figure only contradicts valuation_mm if it is itself claimed as a VALUATION.
# Basis lines are full of other money: round sizes ('Series H post-money ($65B
# raise)'), comparatives ('below the $13.4bn 2021 mark'), talks at other numbers.
# The first cut flagged all seven of those and zero real errors (2026-07-31), so
# this reads the words around the figure and stays quiet unless they say so.
_VALUATION_WORD = re.compile(r"post-?money|pre-?money|valuation|valued|worth", re.I)
# words that mean "this figure is money moving, not the company's worth", or that
# make it someone else's number — tested against the figure's immediate neighbours
_ROUND_SIZE = re.compile(r"rais\w*|round of|tender|buyback|\braise\b", re.I)
_COMPARATIVE = re.compile(r"below|above|under|over|vs\.?|versus|than|peak|prior|"
                          r"talks?|reported at|target\w*|down from|up from", re.I)


def _basis_conflict(valuation_mm, basis) -> str | None:
    """Flag a basis line that claims a *different valuation* than valuation_mm —
    a real research error (2026-07-31: '$65bn Series H post-money' against a
    $965bn figure). Logged, never auto-corrected: the row ships with both visible
    so a human picks the side."""
    if valuation_mm is None or basis is None or pd.isna(valuation_mm):
        return None
    text = str(basis)
    for m in _MONEY_RE.finditer(text):
        near_before, near_after = text[max(0, m.start() - 20):m.start()], text[m.end():m.end() + 20]
        context = text[max(0, m.start() - 40):m.end() + 40]
        # a round size or a comparative sitting right next to the figure means it
        # isn't this row's valuation being restated
        if _ROUND_SIZE.search(near_before) or _ROUND_SIZE.search(near_after):
            continue
        if _COMPARATIVE.search(near_before):
            continue
        # ...and it only IS a competing valuation if something nearby says so
        if not _VALUATION_WORD.search(context):
            continue
        stated = float(m.group(1).replace(",", ""))
        stated_mm = stated * 1000 if m.group(2).lower().startswith("b") else stated
        if abs(stated_mm - float(valuation_mm)) > 0.2 * max(stated_mm, float(valuation_mm)):
            return f"${float(valuation_mm)/1000:.1f}B vs basis '{m.group(0)}'"
    return None


def _normalize_stage(v) -> str:
    """Canonical stage label. The schema pins the enum but the model still returns
    case variants ('Stated Intent'), and the exact string is load-bearing twice
    over — the Tier map here and the renderer's stage_order grouping."""
    s = _clean_text(v) or "Rumored"
    return {x.lower(): x for x in _STAGES}.get(s.lower(), s)


def _pipeline_frame(payload: dict, prev: pd.DataFrame | None = None) -> pd.DataFrame:
    """Structured deals → tracker Master rows.

    Low-confidence rows are KEPT, not dropped: the board's credibility signal is
    Stage/Tier (a 'Rumored' Tier-D row already reads as thin), and the hand-built
    tracker deliberately carried that tier — the biggest names sit there. They are
    labelled in Source instead so the thinness is visible on the row itself.

    `As Of` means "date we last had evidence", not "date we last mentioned it": a
    row the pass couldn't re-source keeps its prior As Of, so it goes on ageing
    toward retirement instead of looking freshly confirmed."""
    deals = payload.get("deals") or []
    prior_asof = {}
    if prev is not None and not prev.empty:
        prior_asof = dict(zip(prev["Company"].map(roster_key), prev["As Of"]))
    rows, thin, unconfirmed = [], 0, 0
    for d in deals:
        stage = _normalize_stage(d.get("stage"))
        source = _clean_text(d.get("source"))
        if d.get("confidence") == "low":
            thin += 1
            source = f"Low-confidence: {source}" if source else "Low-confidence source"
        company = _clean_text(d.get("company"))
        asof = date.today().isoformat()
        if d.get("verification") == "not-found":
            # on the roster but not re-sourced this pass — keep the older evidence
            # date so the row ages out on schedule rather than looking confirmed
            unconfirmed += 1
            asof = prior_asof.get(roster_key(company), asof)
            source = f"Not re-sourced {date.today().isoformat()}; {source}" if source else source
        key_dt = pd.to_datetime(d.get("key_date"), errors="coerce")
        rows.append({
            "Company": company,
            "Status": "Withdrawn" if stage == "Withdrawn" else "Pipeline",
            "Stage": stage,
            "Tier": _TIER_BY_STAGE.get(stage),
            "Ticker": _clean_text(d.get("ticker")),
            "Sector": None if d.get("sector") == "Unknown" else d.get("sector"),
            "Vehicle Type": d.get("vehicle_type"),
            "Key Date": None if pd.isna(key_dt) else key_dt.strftime("%Y-%m-%d"),
            "Offer/Target Window": _clean_text(d.get("window")),
            "Raise ($mm)": d.get("raise_mm"),
            "Valuation ($mm)": d.get("valuation_mm"),
            "Valuation Basis": _clean_text(d.get("valuation_basis")),
            "Since Offer (%)": None,
            "Since Open (%)": None,
            "Source": source,
            "As Of": asof,
        })
    df = pd.DataFrame(rows, columns=_MASTER_COLS)
    df = df[df["Company"].notna()]
    df = df.loc[~df["Company"].map(roster_key).duplicated(keep="first")]
    if df.empty:
        raise RuntimeError("pipeline research yielded no usable rows")
    store.log_run("ipo:pipeline", "detail",
                  f"{len(df)} rows ({thin} low-confidence, {unconfirmed} not re-sourced); "
                  + " ".join(f"{k}={v}" for k, v in df["Stage"].value_counts().items()))
    conflicts = [f"{r['Company']}: {c}" for _, r in df.iterrows()
                 if (c := _basis_conflict(r["Valuation ($mm)"], r["Valuation Basis"]))]
    if conflicts:
        store.log_run("ipo:pipeline", "check",
                      "valuation disagrees with its basis line — " + "; ".join(conflicts))
    return df.reset_index(drop=True)


def roster_key(name) -> str:
    """Identity key for a pipeline company. `_norm_name` alone keeps parenthetical
    aliases, so 'Payward, Inc. (Kraken)' and 'Payward, Inc. (dba Kraken)' keyed
    apart and the roster carried the same company twice (2026-07-31). Drop the
    parenthetical and the alias markers before normalizing."""
    s = re.sub(r"\([^)]*\)", " ", str(name or ""))
    s = re.sub(r"\b(dba|fka|f/k/a|formerly|aka|née)\b", " ", s, flags=re.I)
    return _norm_name(s)


# window text is free prose; only compare it after flattening the ways the model
# writes "we don't know" and the dash/case variants it swaps between runs
_NO_WINDOW = re.compile(
    r"^(none|unknown|no (date|window)( announced| stated| set)?|not (stated|announced)|"
    r"no window|tbd|n/?a)\W*$", re.I)


def _norm_window(v) -> str:
    s = re.sub(r"[–—]", "-", str(v or "")).lower()
    s = re.sub(r"\b20(\d\d)\b", r"\1", s)            # 7/28/2026 == 7/28/26
    s = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\- ]", " ", s)).strip(" -")
    return "" if not s or _NO_WINDOW.match(s) else s


def _diff_roster(prev: pd.DataFrame | None, new: pd.DataFrame) -> list[str]:
    """What moved since the last pull. The lake keeps every pull, so this is the
    payoff: stage advances, valuation moves and window slips are the actual news
    on a forward calendar, and they're invisible if you only diff the name list."""
    if prev is None or prev.empty:
        return [f"first pull: {len(new)} names"]
    p = prev.set_index(prev["Company"].map(roster_key))
    changes = []
    for _, r in new.iterrows():
        k = roster_key(r["Company"])
        if k not in p.index:
            changes.append(f"NEW {r['Company']} ({r['Stage']})")
            continue
        was = p.loc[k]
        was = was.iloc[0] if isinstance(was, pd.DataFrame) else was
        if was["Stage"] != r["Stage"]:
            changes.append(f"STAGE {r['Company']}: {was['Stage']} → {r['Stage']}")
        a, b = was["Valuation ($mm)"], r["Valuation ($mm)"]
        if pd.notna(a) and pd.notna(b) and abs(float(b) - float(a)) > 0.05 * float(a):
            changes.append(f"VALUATION {r['Company']}: "
                           f"${float(a)/1000:.1f}B → ${float(b)/1000:.1f}B")
        if _norm_window(was["Offer/Target Window"]) != _norm_window(r["Offer/Target Window"]):
            changes.append(f"WINDOW {r['Company']}: "
                           f"{was['Offer/Target Window'] or '—'} → "
                           f"{r['Offer/Target Window'] or '—'}")
    return changes


def _merge_roster(new: pd.DataFrame, prev: pd.DataFrame | None) -> pd.DataFrame:
    """Union this pull with the last one, keyed on the normalized company name.

    A single research pass has variable recall — consecutive runs surfaced 24 and
    25 names with only partial overlap (measured 2026-07-31). Re-snapshotting
    would churn half the board daily, so the lane accumulates: today's research
    wins for any name it found, names it missed are carried at their own older
    `As Of` (so the row visibly ages on the board), and anything unseen for
    PIPELINE_ROSTER_DAYS retires. A name that lists or dies leaves either by
    ageing out or by coming back stage 'Withdrawn'."""
    if prev is None or prev.empty:
        return new
    key_new = set(new["Company"].map(roster_key))
    prev = prev.copy()
    prev["_key"] = prev["Company"].map(roster_key)
    unseen = prev[~prev["_key"].isin(key_new)].drop(columns="_key")
    age = (pd.Timestamp(date.today()) - pd.to_datetime(unseen["As Of"], errors="coerce")).dt.days
    carried = unseen[age.fillna(999) < PIPELINE_ROSTER_DAYS]
    store.log_run("ipo:pipeline", "detail",
                  f"roster: {len(new)} researched + {len(carried)} carried, "
                  f"{len(unseen) - len(carried)} retired (unseen > {PIPELINE_ROSTER_DAYS}d)")
    out = pd.concat([new, carried[_MASTER_COLS]], ignore_index=True)
    return (out.loc[~out["Company"].map(roster_key).duplicated(keep="first")]
               .reset_index(drop=True))


def latest_pipeline() -> pd.DataFrame | None:
    """Most recent cached pipeline pull (tracker Master schema), or None."""
    return store.read_latest(PIPELINE_TABLE)


def latest_priced() -> pd.DataFrame | None:
    """Most recent cached BEQS priced pull (tracker Master schema), or None."""
    return store.read_latest(PRICED_TABLE)


def pull_pipeline(year: int = None, model: str = PIPELINE_MODEL,
                  force: bool = False) -> pd.DataFrame:
    """Forward IPO pipeline, researched live via the Claude API's web-search tool.

    Two passes: a web-search research call, then a schema-constrained structuring
    call. The result lands in the lake (`ipo_pipeline`) in tracker Master schema,
    so compute reads it exactly like the workbook rows. Skips the API entirely
    when the cached pull is younger than PIPELINE_MAX_AGE_DAYS.

    Raises with no key / no Terminal-independent network — the caller soft-fails
    to the last cached pull, and failing that to the tracker workbook."""
    year = year or date.today().year
    force = force or os.environ.get("IPO_PIPELINE_FORCE") == "1"
    cached = latest_pipeline()
    if not force and cached is not None and not cached.empty:
        asof = pd.to_datetime(cached["As Of"], errors="coerce").max()
        age = (pd.Timestamp(date.today()) - asof).days if pd.notna(asof) else 999
        if age < PIPELINE_MAX_AGE_DAYS:
            store.log_run("ipo:pipeline", "skip", f"cached pull is {age}d old")
            return cached
    client = _anthropic_client()
    research = _research(client, year, model, prev=cached)
    fresh = _pipeline_frame(_structure(client, research, model), prev=cached)
    changes = _diff_roster(cached, fresh)
    if changes:
        store.log_run("ipo:pipeline", "changes", "; ".join(changes[:25]))
    df = _merge_roster(fresh, cached)
    store.append_parquet(PIPELINE_TABLE, df)
    return df
