"""finra.py — FINRA pulls (§3). Token at repo-root .finra_api_key works as a
direct Bearer on api.finra.org (no OAuth dance needed — verified 2026-07-09;
metadata endpoints 403 but data endpoints authorize).

LV15 margin debt: NOT on the Query API. Source = margin-statistics page:
  * archive XLSX (1997→2021) pulled once and cached in the lake
  * live HTML table (trailing ~13 months) parsed on each run (raw snapshotted)
RF6 wholesaler volumes: Query API otcMarket/weeklySummary (aggregate v1;
  firm-level wholesaler detail itemized).
"""
from __future__ import annotations

import io
import os
import re
from datetime import datetime

import pandas as pd

from . import _net

BASE = os.environ.get("WORKSPACE") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(BASE, ".finra_api_key")
API = "https://api.finra.org"
MARGIN_PAGE = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
ARCHIVE_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"


_TOKEN_CACHE: dict = {}


def _token() -> str:
    """OAuth2 client-credentials: .finra_api_key holds two lines (API client id,
    client secret). Tokens cached ~25 min (FINRA issues 30-min tokens).
    Single-line files fall back to direct-Bearer (legacy limited access)."""
    import base64
    import time as _t
    if not os.path.exists(KEY_FILE):
        raise FileNotFoundError(f"FINRA key not found at {KEY_FILE}")
    lines = [l.strip() for l in open(KEY_FILE).read().strip().splitlines() if l.strip()]
    # accept labeled lines ('API KEY: xxx') or bare values
    vals = [l.split(":", 1)[1].strip() if ":" in l else l for l in lines]
    if len(vals) == 1:
        return vals[0]  # legacy direct token
    if _TOKEN_CACHE.get("exp", 0) > _t.time():
        return _TOKEN_CACHE["tok"]
    cid, secret = vals[0], vals[1]
    auth = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    r = _net.session().post(
        "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials",
        headers={"Authorization": f"Basic {auth}"}, timeout=30)
    r.raise_for_status()
    tok = r.json()["access_token"]
    _TOKEN_CACHE.update(tok=tok, exp=_t.time() + 25 * 60)
    return tok


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}", "Accept": "application/json"}


def margin_debt() -> pd.DataFrame:
    """LV15: monthly margin debt (debit balances in margin accounts, $M) —
    archive workbook + live page table stitched into one series 1997→."""
    from .. import store
    from .free import _snapshot_raw
    s = _net.session()

    archive = store.read_latest("finra_margin_archive")
    if archive is None:
        r = s.get(ARCHIVE_XLSX, timeout=90)
        r.raise_for_status()
        xl = pd.read_excel(io.BytesIO(r.content))
        xl.columns = [str(c).strip().lower() for c in xl.columns]
        date_col = next(c for c in xl.columns if "month" in c or "date" in c)
        debit_col = next(c for c in xl.columns if "debit" in c)
        archive = xl[[date_col, debit_col]].rename(columns={date_col: "date", debit_col: "value"})
        archive["date"] = pd.to_datetime(archive["date"], errors="coerce")
        archive["value"] = pd.to_numeric(archive["value"], errors="coerce")
        archive = archive.dropna().sort_values("date")
        store.append_parquet("finra_margin_archive", archive,
                             pulled_at=datetime.now().isoformat(timespec="seconds"))

    r = s.get(MARGIN_PAGE, timeout=60)
    r.raise_for_status()
    _snapshot_raw("finra_margin", r.content)
    tables = pd.read_html(io.StringIO(r.text))
    recent = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any("debit" in c for c in cols) and any("month" in c for c in cols):
            date_col = next(c for c in t.columns if "month" in str(c).lower())
            debit_col = next(c for c in t.columns if "debit" in str(c).lower())
            recent = t[[date_col, debit_col]].rename(columns={date_col: "date", debit_col: "value"})
            break
    if recent is None:
        raise RuntimeError("FINRA margin page table not found")
    recent["date"] = pd.to_datetime(recent["date"], format="%b-%y", errors="coerce").fillna(
        pd.to_datetime(recent["date"], errors="coerce"))
    recent["value"] = pd.to_numeric(
        recent["value"].astype(str).str.replace(",", "").str.replace("$", ""), errors="coerce")
    recent = recent.dropna().sort_values("date")

    full = pd.concat([archive[["date", "value"]], recent], ignore_index=True)
    full = full.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    store.append_parquet("finra_margin_debt", full,
                         pulled_at=datetime.now().isoformat(timespec="seconds"))
    return full


def weekly_summary(tiers: str = "T1") -> pd.DataFrame:
    """RF6 (v1): OTC weekly aggregate share volume from the Query API.
    Firm-level wholesaler split is a follow-up (needs the detail dataset)."""
    from .. import store
    s = _net.session()
    r = s.get(f"{API}/data/group/otcMarket/name/weeklySummary",
              params={"limit": 5000}, headers=_headers(), timeout=60)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError("weeklySummary returned no rows")
    store.append_parquet("finra_weekly_summary", df,
                         pulled_at=datetime.now().isoformat(timespec="seconds"))
    return df


def weekly_otc_stats(weeks_back: int = 30) -> pd.DataFrame:
    """RF2-splice anchor + RF6 wholesaler detail. KEY FINDING 2026-07-09: the
    dataset is partitioned by week — EXACT-week domainFilters return instantly;
    range scans 504. Post-2023 rows are per-firm/per-symbol (OTC_W_FIRM =
    non-ATS volume by wholesaler; ATS_W_FIRM) with tiers T1/T2 (was NMS).
    Pulls missing recent weeks one at a time, idempotent via the lake."""
    from .. import store
    s = _net.session(total_retries=2, backoff=3.0)
    have = store.read_latest("finra_weekly_otc")
    have_weeks = set(have["weekStartDate"].unique()) if have is not None else set()
    mondays = pd.date_range(end=pd.Timestamp.today(), periods=weeks_back, freq="W-MON")
    frames = [have] if have is not None else []
    pulled = 0
    for wk in mondays.strftime("%Y-%m-%d"):
        if wk in have_weeks:
            continue
        rows, offset = [], 0
        while True:
            body = {"limit": 5000, "offset": offset,
                    "fields": ["weekStartDate", "summaryTypeCode", "tierIdentifier",
                               "firmCRDNumber", "marketParticipantName",
                               "totalWeeklyShareQuantity", "totalWeeklyTradeCount"],
                    "domainFilters": [
                        {"fieldName": "weekStartDate", "values": [wk]},
                        {"fieldName": "summaryTypeCode",
                         "values": ["OTC_W_FIRM", "ATS_W_FIRM"]}]}
            r = s.post(f"{API}/data/group/otcMarket/name/weeklySummary", json=body,
                       headers={**_headers(), "Content-Type": "application/json"},
                       timeout=90)
            r.raise_for_status()
            try:
                batch = r.json() if r.text.strip() else []
            except ValueError:
                batch = []   # unpublished week → empty body, not JSON
            rows.extend(batch)
            if len(batch) < 5000:
                break
            offset += 5000
        import time as _t
        _t.sleep(0.3)
        if rows:
            frames.append(pd.DataFrame(rows))
            pulled += 1
    if not frames:
        raise RuntimeError("no weekly OTC rows")
    df = pd.concat(frames, ignore_index=True).drop_duplicates()
    store.append_parquet("finra_weekly_otc", df,
                         pulled_at=datetime.now().isoformat(timespec="seconds"))
    store.log_run("finra:weekly_otc", "ok", f"{pulled} new weeks, {df['weekStartDate'].nunique()} total")
    return df


def pull() -> dict:
    from .. import store
    out = {}
    for name, fn in (("margin_debt", margin_debt), ("weekly_summary", weekly_summary),
                     ("weekly_otc_stats", weekly_otc_stats)):
        try:
            fn()
            out[name] = "ok"
        except Exception as e:  # noqa: BLE001
            store.log_run(f"finra:{name}", "fail", str(e)[:120])
            out[name] = f"fail: {str(e)[:60]}"
    return out
