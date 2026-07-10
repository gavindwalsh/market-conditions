"""free.py — free sources, API-first, scrape last resort (§3). Every scrape
snapshots raw bytes into the lake first, so layout drift is debuggable (§7).

Live status (probed 2026-07-09):
  NY Fed HHDC quarterly XLSX  — works (stable URL pattern per quarter)  → MH5/MH6
  NAAIM exposure index page   — works (HTML table)                      → MH8 (partial)
  ICI MMF weekly              — page up; XLS link mined per release     → OP4
  AAII sentiment.xls          — 403 members-only now → ITEMIZED, MH8 ships NAAIM-only
  Cboe daily stats CSV        — no clean endpoint; LV1 superseded by Phase-3 LV2
                                (OPRA-computed SPX 0DTE share); wire later as cross-check
  Broker margin rates (LV14)  — no API anywhere; manual-quarterly config below,
                                each value carrying source + as-of (house pattern)
"""
from __future__ import annotations

import io
import os
import re
from datetime import date, datetime

import pandas as pd

from . import _net

# LV14 — manual-quarterly inputs (house manual_monthly pattern: value + source +
# as-of in-line; §A3 honesty — posted rates read by hand, refreshed quarterly;
# the LV14 tile carries the as-of).
# !!! SEED VALUES UNVERIFIED (2026-07-09): shaped from typical rate structures,
# NOT yet read from the broker pages. Gavin to verify against each source and
# update before the LV14 tile is trusted — flagged in the blockers list.
BROKER_MARGIN_RATES = {  # UNVERIFIED seeds — verify at ibkr.com/interest-rates etc.
    "IBKR Pro (tiered, <100k)": 4.83,
    "Schwab (base)": 10.75,
    "Fidelity (base)": 10.50,
    "Robinhood Gold": 5.00,
}
BROKER_RATES_ASOF = "2026-07-09"
BROKER_RATES_VERIFIED = False  # flip true once Gavin confirms against the pages


def _snapshot_raw(name: str, content: bytes) -> str:
    from .. import store
    d = os.path.join(store.LAKE_DIR, "scrape_raw", name)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{datetime.now().strftime('%Y%m%d%H%M%S')}.bin")
    with open(path, "wb") as f:
        f.write(content)
    return path


def _latest_hhdc_url() -> str:
    """Most recent quarterly HHDC workbook (Q releases lag ~1 quarter)."""
    today = date.today()
    candidates = []
    y, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(5):
        candidates.append(f"HHD_C_Report_{y}Q{q}.xlsx")
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    base = "https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/"
    s = _net.session(total_retries=1)
    for c in candidates:
        r = s.head(base + c, timeout=20)
        if r.status_code == 200:
            return base + c
    raise RuntimeError("No HHDC workbook found in the last 5 quarters")


def _hhdc_quarter_index(col) -> pd.Series:
    """'03:Q1' → Timestamp quarter-end."""
    def conv(x):
        m = re.match(r"^(\d{2}):Q([1-4])$", str(x).strip())
        if not m:
            return pd.NaT
        yy, q = int(m.group(1)), int(m.group(2))
        year = 2000 + yy if yy < 80 else 1900 + yy
        return pd.Timestamp(year=year, month=q * 3, day=1) + pd.offsets.MonthEnd(0)
    return col.map(conv)


def nyfed_hhdc() -> dict[str, pd.DataFrame]:
    """MH5 balances by product + MH6 30+ delinquency transitions by product.
    Sheets located by title text (layout drifts across releases)."""
    from .. import store
    url = _latest_hhdc_url()
    s = _net.session()
    r = s.get(url, timeout=90)
    r.raise_for_status()
    _snapshot_raw("nyfed_hhdc", r.content)
    xl = pd.ExcelFile(io.BytesIO(r.content))

    def parse_sheet(title_match: str) -> pd.DataFrame | None:
        for sheet in xl.sheet_names:
            if not sheet.endswith("Data"):
                continue
            head = xl.parse(sheet, header=None, nrows=8)
            title = str(head.iloc[0, 0])
            if title_match.lower() in title.lower():
                # header row DRIFTS between sheets (balances row 3, transitions
                # row 4 — the latter shipped columns as 'Unnamed: N'): use the
                # first row whose second cell is a non-numeric label
                hrow = 3
                for r in range(2, 7):
                    c1 = head.iloc[r, 1]
                    if isinstance(c1, str) and c1.strip():
                        hrow = r
                        break
                df = xl.parse(sheet, header=hrow)
                df = df.rename(columns={df.columns[0]: "quarter"})
                df["date"] = _hhdc_quarter_index(df["quarter"])
                df = df.dropna(subset=["date"])
                return df.drop(columns=["quarter"])
        return None

    balances = parse_sheet("Total Debt Balance")
    # 30+ transition flow by product — titled "New Delinquent* Balances by Loan
    # Type" in current releases (the * = newly 30+ days late)
    transitions = parse_sheet("New Delinquent")
    pulled_at = datetime.now().isoformat(timespec="seconds")
    out = {}
    if balances is not None:
        store.append_parquet("hhdc_balances", balances, pulled_at=pulled_at)
        out["balances"] = balances
    if transitions is not None:
        store.append_parquet("hhdc_transitions", transitions, pulled_at=pulled_at)
        out["transitions"] = transitions
    if not out:
        raise RuntimeError(f"HHDC parse found no target sheets in {url}")
    return out


def naaim() -> pd.DataFrame:
    """MH8 (NAAIM half): manager equity-exposure index, weekly. The program
    page links a since-inception XLSX (USE_Data-*.xlsx) — mine it, parse the
    [date, mean exposure] columns."""
    from .. import store
    s = _net.session()
    r = s.get("https://naaim.org/programs/naaim-exposure-index/", timeout=60)
    r.raise_for_status()
    links = re.findall(r'href="([^"]*USE_Data[^"]*\.xlsx)"', r.text)
    if not links:
        raise RuntimeError("NAAIM: no USE_Data xlsx link on page")
    x = s.get(links[0], timeout=90)
    x.raise_for_status()
    _snapshot_raw("naaim", x.content)
    raw = pd.read_excel(io.BytesIO(x.content), header=None)
    hdr = next(i for i in range(min(10, len(raw)))
               if any("date" in str(c).lower() for c in raw.iloc[i].tolist()))
    body = pd.read_excel(io.BytesIO(x.content), header=hdr)
    body.columns = [str(c).strip().lower() for c in body.columns]
    date_col = next(c for c in body.columns if "date" in c)
    val_col = next((c for c in body.columns
                    if any(k in c for k in ("mean", "average", "naaim", "number"))),
                   body.columns[-1])
    df = body[[date_col, val_col]].rename(columns={date_col: "date", val_col: "value"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)
    store.append_parquet("naaim_exposure", df,
                         pulled_at=datetime.now().isoformat(timespec="seconds"))
    return df


def ici_mmf() -> pd.DataFrame:
    """OP4 numerator: ICI weekly money-market fund assets. Mine the stats page
    for the latest weekly XLS release, parse total + retail."""
    from .. import store
    s = _net.session()
    page = s.get("https://www.ici.org/research/stats/mmf", timeout=60)
    page.raise_for_status()
    links = re.findall(r'href="([^"]+\.xls[x]?)"', page.text)
    if not links:
        raise RuntimeError("ICI: no XLS link found on stats page")
    url = links[0]
    if url.startswith("/"):
        url = "https://www.ici.org" + url
    r = s.get(url, timeout=90)
    r.raise_for_status()
    _snapshot_raw("ici_mmf", r.content)
    xl = pd.ExcelFile(io.BytesIO(r.content))
    df = xl.parse(xl.sheet_names[0], header=None)
    # locate the header row containing 'Date' then read [date, total, retail?]
    hdr = None
    for i in range(min(20, len(df))):
        if any("date" in str(x).lower() for x in df.iloc[i].tolist()):
            hdr = i
            break
    if hdr is None:
        raise RuntimeError("ICI: no Date header row found")
    body = xl.parse(xl.sheet_names[0], header=hdr)
    body.columns = [str(c).strip().lower() for c in body.columns]
    date_col = next(c for c in body.columns if "date" in c)
    total_col = next((c for c in body.columns if "total" in c), body.columns[1])
    out = body[[date_col, total_col]].rename(columns={date_col: "date", total_col: "value"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    out = out.dropna().sort_values("date").reset_index(drop=True)
    store.append_parquet("ici_mmf", out,
                         pulled_at=datetime.now().isoformat(timespec="seconds"))
    return out


def pull() -> dict:
    """Daily-run entry: attempt each free source, fail soft per source."""
    from .. import store
    out = {}
    for name, fn in (("nyfed_hhdc", nyfed_hhdc), ("naaim", naaim), ("ici_mmf", ici_mmf)):
        try:
            res = fn()
            out[name] = "ok"
        except Exception as e:  # noqa: BLE001
            store.log_run(f"free:{name}", "fail", str(e)[:120])
            out[name] = f"fail: {str(e)[:60]}"
    return out
