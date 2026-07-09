"""edgar.py — SEC EDGAR quarterly form indexes (§3). No auth; UA header + <=10 req/s.

IS2 filing rate: S-1/F-1 new filings + S-1/A amendment rate, weekly, 2020→.
Source: quarterly full-index form.idx (one file per quarter, all filings, fixed
width). The current quarter's file updates daily; past quarters are immutable,
so the backfill is pulled once and cached in the lake (lake table per quarter).

NOTE (2026-07-08): this host sits behind an iboss TLS-intercepting proxy that
decrypts sec.gov; _net.py's truststore injection is what makes this work.

Interface:
  edgar.pull(since_year=2020) -> tidy frame [date, form, cik, company]
                                 (S-1, S-1/A, F-1, F-1/A, 485APOS, N-1A only)
"""
from __future__ import annotations

import io
import time
from datetime import date, datetime

import pandas as pd

from . import _net

INDEX_URL = "https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{q}/form.idx"
FORMS = ("S-1", "S-1/A", "F-1", "F-1/A", "485APOS", "N-1A")  # IS2 + IS7 pipeline


import re

# first date-shaped token on the line = the Date Filed column (the file-name
# accession numbers that also contain digit-dash runs come after it)
_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _parse_form_idx(text: str) -> pd.DataFrame:
    """form.idx rows: Form Type / Company Name / CIK / Date Filed / File Name.

    The header labels do NOT align with the data columns (verified 2026-07-08 —
    a header-offset slice truncated dates to month precision), so parse by
    anchoring on the date token: everything left of it is FORM / COMPANY / CIK,
    with CIK the last whitespace-separated token."""
    rows = []
    for l in text.splitlines():
        form = l.split(" ", 1)[0]
        if form not in FORMS or not l.startswith(form + " "):
            continue
        m = _DATE_RE.search(l)
        if not m:
            continue
        left = l[: m.start()].rstrip()
        parts = left.split()
        if len(parts) < 2 or not parts[-1].isdigit():
            continue
        rows.append({"date": m.group(1), "form": form,
                     "cik": parts[-1], "company": " ".join(parts[1:-1])})
    df = pd.DataFrame(rows, columns=["date", "form", "cik", "company"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], format="%Y-%m-%d").dt.strftime("%Y-%m-%d")
    return df


def _quarters(since_year: int):
    today = date.today()
    cur_q = (today.month - 1) // 3 + 1
    for y in range(since_year, today.year + 1):
        for q in (1, 2, 3, 4):
            if y == today.year and q > cur_q:
                break
            yield y, q


def pull(since_year: int = 2020) -> pd.DataFrame:
    """Pull all quarters since `since_year`. Immutable past quarters come from
    the lake if already present; only the current quarter is re-fetched."""
    from .. import store
    s = _net.session()
    today = date.today()
    cur = (today.year, (today.month - 1) // 3 + 1)
    frames = []
    pulled_at = datetime.now().isoformat(timespec="seconds")
    for y, q in _quarters(since_year):
        table = f"edgar_formidx_{y}q{q}"
        cached = store.read_latest(table)
        if cached is not None and (y, q) != cur:
            frames.append(cached[["date", "form", "cik", "company"]])
            continue
        r = s.get(INDEX_URL.format(year=y, q=q), timeout=60)
        r.raise_for_status()
        df = _parse_form_idx(r.text)
        store.append_parquet(table, df, pulled_at=pulled_at)
        frames.append(df)
        time.sleep(0.15)  # SEC fair-access: stay well under 10 req/s
    out = pd.concat(frames, ignore_index=True).drop_duplicates()
    return out.sort_values("date").reset_index(drop=True)
