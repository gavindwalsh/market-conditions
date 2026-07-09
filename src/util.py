"""util.py — deterministic helpers shared across compute + render.

Pure functions (numpy/pandas only), so they are unit-testable without any data
source. Three jobs, all set by the spec:

  * trailing_percentile  — §1 "percentile vs its own history", with the §6
    young-series gate (need >= 1yr of history or return None -> show level only).
  * downsample_display   — §6 display resolution: daily for the last year,
    monthly (last obs per calendar month) before that.
  * staleness            — §2 failure handling: classify an as-of date as
    fresh / stale for the yellow-stamp rule, relative to each metric's cadence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

TRADING_DAYS_1Y = 252
CALENDAR_DAYS_1Y = 365
MIN_HISTORY_FOR_PERCENTILE = TRADING_DAYS_1Y  # §6: 1yr gate


def _to_series(values, index=None) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.dropna()
    s = pd.Series(values, index=index)
    return s.dropna()


def trailing_percentile(values, value=None, min_history: int = MIN_HISTORY_FOR_PERCENTILE):
    """Percentile rank (0-100) of `value` within its own history.

    `values`  : historical observations (list / np.array / pd.Series), including
                or excluding the current point — the rank is computed against the
                full vector passed in.
    `value`   : the point to rank; defaults to the last element of `values`.
    Returns   : float in [0, 100], or None when history is shorter than
                `min_history` (§6 young-series gate — caller shows level only).

    Rank convention: fraction of history strictly below `value` plus half the
    ties, ×100 (a mid-rank that is stable and symmetric). Deterministic.
    """
    s = _to_series(values)
    n = len(s)
    if n < min_history:
        return None
    if value is None:
        value = s.iloc[-1]
    if pd.isna(value):
        return None
    arr = s.to_numpy(dtype=float)
    below = np.sum(arr < value)
    ties = np.sum(arr == value)
    pct = (below + 0.5 * ties) / n * 100.0
    return float(round(pct, 2))


def downsample_display(df: pd.DataFrame, date_col: str = "date", asof: date | None = None,
                       daily_window_days: int = CALENDAR_DAYS_1Y) -> pd.DataFrame:
    """§6 display resolution: daily for the last `daily_window_days`, monthly
    (last observation per calendar month) before the cutoff.

    Deterministic and idempotent. Keeps rows sorted ascending by date. `asof`
    defaults to the max date in the frame (so a rerun on stored data is stable
    regardless of wall clock — supports the byte-identical-rerun guarantee).
    """
    if df.empty:
        return df.copy()
    out = df.copy()
    out[date_col] = pd.to_datetime(out[date_col])
    out = out.sort_values(date_col).reset_index(drop=True)

    max_date = out[date_col].max() if asof is None else pd.Timestamp(asof)
    cutoff = max_date - pd.Timedelta(days=daily_window_days)

    recent = out[out[date_col] > cutoff]
    old = out[out[date_col] <= cutoff]
    if not old.empty:
        # last obs per calendar month
        old = old.groupby(old[date_col].dt.to_period("M"), as_index=False).last()
    kept = pd.concat([old, recent], ignore_index=True)
    kept = kept.drop_duplicates(subset=[date_col]).sort_values(date_col).reset_index(drop=True)
    return kept


@dataclass(frozen=True)
class Staleness:
    level: str      # "fresh" | "stale"
    age_days: int
    tolerance_days: int


# how many days past the expected cadence before a value is flagged stale (yellow)
_CADENCE_TOLERANCE = {
    "daily": 4,        # weekend + a holiday
    "weekly": 10,
    "biweekly": 20,
    "monthly": 45,
    "quarterly": 100,  # DFA/Z.1 land with an ~11-week lag by design
}


def classify_staleness(asof: date, cadence: str, today: date | None = None) -> Staleness:
    """Flag whether an as-of date is stale for its native cadence (§2). A slow
    source at its normal lag is NOT stale — only lateness beyond tolerance is."""
    today = today or date.today()
    if isinstance(asof, (datetime, pd.Timestamp)):
        asof = asof.date() if hasattr(asof, "date") else asof
    age = (today - asof).days
    tol = _CADENCE_TOLERANCE.get(cadence, 4)
    return Staleness("stale" if age > tol else "fresh", age, tol)
