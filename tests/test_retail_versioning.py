"""§7.6: the methodology version gate.

This is the guard that makes a classifier change actually reprocess. The rule it
replaced treated any midpoint-signed day as complete, so bumping the methodology
left the EC2 lanes with nothing to do while they logged success — a box that ran
for an hour, synced, and recomputed nothing. Both `backfill_tape` and the lane
runner now share this one definition.
Run: python tests/test_retail_versioning.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import config  # noqa: E402
from src.pull import massive  # noqa: E402

CUR = config.RETAIL_METHOD_VERSION


def _day(tdir, name, **cols):
    df = pd.DataFrame([{"date": name, "ticker": "AAA", **cols}])
    df.to_parquet(os.path.join(tdir, f"{name}.parquet"), index=False)


def _holiday(tdir, name):
    pd.DataFrame([{"date": name, "ticker": "_HOLIDAY_"}]).to_parquet(
        os.path.join(tdir, f"{name}.parquet"), index=False)


def test_have_days_version_gate():
    d = tempfile.mkdtemp()
    _day(d, "2026-01-05", signing="midpoint")                          # pre-stamp
    _day(d, "2026-01-06", signing="midpoint", method_version=CUR - 1)  # older method
    _day(d, "2026-01-07", signing="midpoint", method_version=CUR)      # current
    _day(d, "2026-01-08", signing="none", method_version=CUR)          # trades-only
    _holiday(d, "2026-01-09")                                          # market closed

    have = massive._have_days(d, quotes=True)
    assert "2026-01-05" not in have, "unstamped day must reprocess"
    assert "2026-01-06" not in have, "older methodology must reprocess"
    assert "2026-01-07" in have, "current methodology is done"
    assert "2026-01-08" not in have, "trades-only must re-run for midpoint signing"
    assert "2026-01-09" in have, "holiday markers must never be re-asked"

    # trades-only lane accepts an unsigned day, but STILL enforces the version
    have_t = massive._have_days(d, quotes=False)
    assert "2026-01-08" in have_t
    assert "2026-01-05" not in have_t
    print("PASS have-days version gate (stamp, holiday, signing)")


def test_version_helpers():
    d = tempfile.mkdtemp()
    _day(d, "2026-02-02", signing="midpoint")
    _day(d, "2026-02-03", signing="midpoint", method_version=CUR)
    _holiday(d, "2026-02-04")
    assert massive._day_method_version(os.path.join(d, "2026-02-02.parquet")) == 0
    assert massive._day_method_version(os.path.join(d, "2026-02-03.parquet")) == CUR
    assert massive._is_holiday_marker(os.path.join(d, "2026-02-04.parquet")) is True
    assert massive._is_holiday_marker(os.path.join(d, "2026-02-03.parquet")) is False
    # a holiday marker has no method_version and must not read as current
    assert massive._day_method_version(os.path.join(d, "2026-02-04.parquet")) == 0
    print("PASS version/holiday helpers")


if __name__ == "__main__":
    test_have_days_version_gate()
    test_version_helpers()
    print("\nAll versioning tests passed.")
