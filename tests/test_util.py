"""Unit tests for util.py (§7.6). Run: python -m pytest tests/ -q
Also runnable without pytest: python tests/test_util.py
"""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import util  # noqa: E402


def test_percentile_gate_below_one_year():
    # 251 points < 252 -> gated (None), show level only
    assert util.trailing_percentile(list(range(251))) is None


def test_percentile_basic_rank():
    # value equal to the max of 0..999 -> ~99.95 (below=999, ties=1, n=1000)
    vals = list(range(1000))
    pct = util.trailing_percentile(vals, value=999)
    assert pct is not None and 99.0 < pct <= 100.0
    # median-ish
    mid = util.trailing_percentile(vals, value=500)
    assert 49.0 < mid < 51.0


def test_percentile_ties_midrank():
    vals = [5] * 300  # all identical -> value 5 sits at mid-rank 50
    assert util.trailing_percentile(vals, value=5) == 50.0


def test_downsample_daily_then_monthly():
    # 3 years of daily data
    idx = pd.date_range("2023-01-01", "2025-12-31", freq="D")
    df = pd.DataFrame({"date": idx, "value": np.arange(len(idx))})
    asof = date(2025, 12, 31)
    out = util.downsample_display(df, asof=asof, daily_window_days=365)
    out["date"] = pd.to_datetime(out["date"])
    cutoff = pd.Timestamp(asof) - pd.Timedelta(days=365)

    recent = out[out["date"] > cutoff]
    old = out[out["date"] <= cutoff]
    # recent stays daily: consecutive days differ by 1
    gaps = recent["date"].diff().dropna().dt.days
    assert (gaps == 1).all()
    # old is monthly: at most one row per calendar month
    per_month = old.groupby(old["date"].dt.to_period("M")).size()
    assert (per_month <= 1).all()
    # last point preserved
    assert out["date"].max() == pd.Timestamp("2025-12-31")


def test_downsample_idempotent():
    idx = pd.date_range("2022-01-01", "2025-06-30", freq="D")
    df = pd.DataFrame({"date": idx, "value": np.arange(len(idx))})
    asof = date(2025, 6, 30)
    once = util.downsample_display(df, asof=asof)
    twice = util.downsample_display(once, asof=asof)
    pd.testing.assert_frame_equal(
        once.reset_index(drop=True).assign(date=lambda d: pd.to_datetime(d["date"])),
        twice.reset_index(drop=True).assign(date=lambda d: pd.to_datetime(d["date"])),
    )


def test_staleness_slow_source_not_flagged():
    # a quarterly print 70 days old is still fresh (DFA ~11wk lag)
    s = util.classify_staleness(date(2026, 4, 30), "quarterly", today=date(2026, 7, 8))
    assert s.level == "fresh"
    # a daily series 10 days old is stale
    s2 = util.classify_staleness(date(2026, 6, 28), "daily", today=date(2026, 7, 8))
    assert s2.level == "stale"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")
