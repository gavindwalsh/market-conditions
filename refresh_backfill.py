#!/usr/bin/env python3
"""refresh_backfill.py — fold newly backfilled tape/OPRA days into the charts.

Recomputes ONLY the backfill-fed metrics (no network pulls, no BBG/FRED):
  stock tape  → RF1 (breadth), RF2, RF3, RF4, RF10, MH9
  OPRA trades → LV2, LV3, RF7, RF8 (+ snapshot metrics re-read, cheap)
then re-renders pulse_latest.html. Run as often as you like while the
backfill terminals are working; the daily `python -m src.run` supersedes
this once backfill is done.

Usage:  python refresh_backfill.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import quiet  # noqa: F401,E402 — side effect: silence known-benign warnings
from src.compute import opra, retail_series  # noqa: E402
from src.pull import massive  # noqa: E402
from src.render import render  # noqa: E402


def main():
    rd = massive.read_retail_daily()
    od = massive.read_opra_daily()
    n_tape = rd["date"].nunique() if rd is not None else 0
    n_signed = (rd[rd["signing"] == "midpoint"]["date"].nunique()
                if rd is not None and "signing" in rd.columns else 0)
    n_opra = od["date"].nunique() if od is not None else 0
    print(f"lake: {n_tape} tape days ({n_signed} signed) | {n_opra} OPRA days")

    r1 = retail_series.build()
    r2 = opra.build()
    ok = {**r1, **r2}
    print("computes:", " ".join(f"{k}={'ok' if v else 'skip'}" for k, v in ok.items()))
    if n_signed < retail_series.RF4_WINDOW:
        print(f"  (RF4 unlocks at {retail_series.RF4_WINDOW} signed days — "
              f"{retail_series.RF4_WINDOW - n_signed} to go)")

    out = render.build(build_version="backfill-refresh")
    print("rendered:", out)
    print("deploy with:  python deploy.py market-conditions")


if __name__ == "__main__":
    main()
