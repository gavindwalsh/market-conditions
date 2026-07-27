"""fred.py — FRED REST pulls (§3). Free API key at repo-root .fred_api_key.

Adapts the proven avos-country-dashboard/fred_refresh.py: retry-with-backoff on
5xx/429, '.'-missing handling, full-history observations. Returns a tidy frame
[date, value] per series and lands it in the lake.

Phase-1 FRED series feed OP1/OP3/OP4 (DFA, Z.1), MH2 (OAS fallback),
MH4/MH5 (PMMS, G.19, DGS10), LV15 context. Exact series IDs are verified
against FRED in the first build pass (§3 discipline) — the SERIES map below is
seeded and flagged where unverified.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

from . import _net  # noqa: F401 — injects truststore into ssl (iboss proxy, see _net.py)

BASE = os.environ.get("WORKSPACE") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(BASE, ".fred_api_key")
FRED_API = "https://api.stlouisfed.org/fred"

# Mnemonic → FRED id. All IDs below [verified 2026-07-08] live against the API
# (the spec's original OP1 seed WFRBLB50107 was wrong; corrected here).
SERIES = {
    # MH2 credit OAS fallback (BBG primary)
    "ig_oas": "BAMLC0A0CM",
    "hy_oas": "BAMLH0A0HYM2",
    # MH4 borrowing rates
    "mortgage30": "MORTGAGE30US",   # PMMS 30y
    "dgs10": "DGS10",
    "dgs5": "DGS5",                 # MH3 current-coupon spread blend leg
    "sofr": "SOFR",                 # LV7/LV8/LV9 financing benchmark
    "gdp": "GDP",                   # LV15/OP2/OP10 denominator — nominal GDP, $B SAAR (quarterly)
    # OP1 — DFA corporate equities + MF shares by wealth cohort, levels ($M, Q)
    "dfa_eq_top1": "WFRBLT01014",       # top 1% (99th-100th)
    "dfa_eq_next9": "WFRBLN09041",      # 90th-99th
    "dfa_eq_next40": "WFRBLN40068",     # 50th-90th
    "dfa_eq_bottom50": "WFRBLB50095",   # bottom 50%
    # OP1 — cohort shares (% of aggregate) for the highlight view
    "dfa_eqsh_top1": "WFRBST01122",
    "dfa_eqsh_next9": "WFRBSN09149",
    "dfa_eqsh_next40": "WFRBSN40176",
    "dfa_eqsh_bottom50": "WFRBSB50203",
    # OP3 — Z.1 B.101 household cash components / total financial assets (Q)
    "hh_fin_assets": "BOGZ1FL194090005Q",   # households; total financial assets
    "hh_checkable": "BOGZ1FL193020005Q",    # checkable deposits + currency
    "hh_time_savings": "BOGZ1FL193030205Q", # other deposits incl time+savings (IMA)
    "hh_mmf": "BOGZ1FL193034005Q",          # money market fund shares
    # OP9-12 — household saving + debt burden (Households panel)
    "psavert": "PSAVERT",   # personal saving rate, % of DPI (monthly)
    "pmsave": "PMSAVE",     # personal saving, $B SAAR (monthly)
    "tdsp": "TDSP",         # household debt service payments, % of DPI (quarterly)
    "fodsp": "FODSP",       # financial obligations ratio, % of DPI (quarterly)
}


def _load_key() -> str:
    if not os.path.exists(KEY_FILE):
        raise FileNotFoundError(
            f"FRED API key not found at {KEY_FILE}. Drop a single-line file with your key.")
    key = open(KEY_FILE).read().strip()
    if not key:
        raise ValueError(f"{KEY_FILE} is empty.")
    return key


def _get_json(url, max_retries=4, base_delay=1.0):
    last = None
    for attempt in range(max_retries):
        try:
            with urlopen(url, timeout=30) as r:
                import json
                return json.load(r)
        except HTTPError as e:
            last = e
            if (e.code >= 500 or e.code == 429) and attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt)); continue
            raise
        except URLError as e:
            last = e
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt)); continue
            raise
    if last:
        raise last


def _call(endpoint, params):
    params["file_type"] = "json"
    return _get_json(f"{FRED_API}/{endpoint}?{urlencode(params)}")


def fetch_series(series_id: str, start="1947-01-01") -> pd.DataFrame:
    """Return tidy [date, value] frame for a FRED series (full history)."""
    key = _load_key()
    obs = _call("series/observations",
                {"series_id": series_id, "api_key": key, "observation_start": start})
    rows = []
    for o in obs.get("observations", []):
        v = o.get("value")
        if v in (None, "", "."):
            continue
        try:
            rows.append({"date": o["date"], "value": float(v)})
        except (ValueError, TypeError):
            continue
    if not rows:
        raise RuntimeError(f"No observations for {series_id}")
    return pd.DataFrame(rows)


def pull(mnemonics: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Pull each requested mnemonic; land in lake; return {mnemonic: frame}."""
    from .. import store  # lazy to avoid import cycle at module load
    targets = mnemonics or list(SERIES)
    out = {}
    pulled_at = datetime.now().isoformat(timespec="seconds")
    for m in targets:
        sid = SERIES.get(m, m)
        df = fetch_series(sid)
        store.append_parquet(f"fred_{m}", df, pulled_at=pulled_at)
        out[m] = df
    return out
