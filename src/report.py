"""report.py — post-run digest so a run's health is legible without grepping.

Two things, both read-only:
  * Source status — last ok/fail/skip per source from run_log.jsonl, scoped to
    the current run (when called from run.py) or the recent log (standalone).
  * Metric freshness — each built metric's as-of vs its cadence tolerance
    (util.classify_staleness), so a soft-failed source shows up as STALE tiles.

  end of `python -m src.run`   → render(since=<run start>)   (this run only)
  `python -m src.report`       → last 2 hours of the log
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from . import config, store, util


def _load_log() -> list[dict]:
    if not os.path.exists(store.RUN_LOG):
        return []
    rows = []
    for line in open(store.RUN_LOG, encoding="utf-8").read().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue  # tolerate a partially-written trailing line
    return rows


def _source_statuses(since: datetime | None) -> dict[str, tuple[str, str]]:
    """Last (status, detail) per source among entries at/after `since` (all if
    None). 'detail' lines (the per-metric ok/skip breakdowns) are ignored."""
    last: dict[str, tuple[str, str]] = {}
    for r in _load_log():
        try:
            ts = datetime.fromisoformat(r["ts"])
        except (KeyError, ValueError):
            continue
        if since is not None and ts < since:
            continue
        if r.get("status") == "detail":
            continue
        last[r["source"]] = (r.get("status", "?"), (r.get("detail") or "").strip())
    return last


# Fast-cadence metrics SHOULD refresh most days; staleness there is actionable
# (a failed/stale pull). Monthly/quarterly official series lag by publication
# schedule, so their age is expected, not a pipeline fault.
_FAST_CADENCES = {"daily", "weekly", "biweekly"}
# Daily-cadence metrics that lag by CONSTRUCTION, not by failure — never alarm:
#   LV11 (variance risk premium) ends ~21 sessions ago (implied vs *subsequent*
#         realized); LV9 (synthetic financing) only prints when it passes its
#         ±500bp sanity gate.
_BY_DESIGN_LAG = {"LV9", "LV11"}


def _freshness(today: date | None = None) -> tuple[list, list]:
    """(fresh, stale) — (id, asof, cadence, age_days) for every built metric,
    classified against its cadence tolerance (§2 staleness). Cadence is read
    from the display JSON (what was actually built), not the registry, so a
    registry/compute cadence mismatch can't create a phantom stale flag."""
    today = today or date.today()
    display = store.load_all_display()
    fresh, stale = [], []
    for m in config._REGISTRY_ROWS:
        d = display.get(m.id)
        if not d or not d.get("asof"):
            continue
        cadence = d.get("cadence") or m.cadence
        try:
            s = util.classify_staleness(date.fromisoformat(str(d["asof"])[:10]),
                                        cadence, today)
        except Exception:  # noqa: BLE001 — a metric without a parseable as-of
            continue
        (stale if s.level == "stale" else fresh).append(
            (m.id, d["asof"], cadence, s.age_days))
    return fresh, stale


def render(since: datetime | None = None) -> str:
    stat = _source_statuses(since)
    ok = sorted(k for k, (s, _) in stat.items() if s == "ok")
    fail = sorted((k, d) for k, (s, d) in stat.items() if s == "fail")
    skip = sorted(k for k, (s, _) in stat.items() if s in ("skip", "todo"))
    fresh, stale = _freshness()

    # ASCII only — this prints to a cp1252 Windows console, not the UTF-8 page
    bar = "=" * 60
    L = [bar,
         "RUN REPORT - " + (f"since {since:%Y-%m-%d %H:%M}" if since else "recent log"),
         bar,
         f"Sources - OK {len(ok)} | FAIL {len(fail)} | SKIP {len(skip)}"]
    if fail:
        for k, d in fail:
            first = d.splitlines()[0] if d else ""
            L.append(f"  FAIL   {k:20} {first[:74]}")
    else:
        L.append("  no source failures")
    if skip:
        L.append("  skip:  " + ", ".join(skip))

    # partition stale into actionable (fast cadence) vs expected (slow official
    # series); drop the by-construction laggers entirely
    alarm, slow = [], []
    for row in stale:
        mid, _asof, cad, _age = row
        if mid in _BY_DESIGN_LAG:
            continue
        (alarm if cad in _FAST_CADENCES else slow).append(row)

    L.append("")
    L.append(f"Metrics - {len(fresh) + len(stale)} built | fresh {len(fresh)} | "
             f"stale {len(alarm) + len(slow)}")
    if alarm:
        L.append("  NEEDS ATTENTION (fast-cadence metric behind — check its source):")
        for mid, asof, cad, age in sorted(alarm, key=lambda x: -x[3]):
            L.append(f"    {mid:6} as-of {str(asof)[:10]}  ({cad}, {age}d old)")
    else:
        L.append("  all fast-cadence metrics current")
    if slow:
        L.append("  slow official series (expected publication lag): "
                 + " ".join(m[0] for m in sorted(slow)))
    L.append(bar)
    return "\n".join(L)


def main():
    print(render(since=datetime.now() - timedelta(hours=2)))


if __name__ == "__main__":
    main()
