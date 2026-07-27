"""run.py — orchestrator: pull → compute → render (§2).

  python -m src.run              # full run at the configured PHASE
  python -m src.run --render     # re-render from existing build_data (no pulls)
  python -m src.run --commit     # + commit/push the refreshed artifacts (src/sync.py)

Design guarantees:
  * A source failure never kills the run (§2): each source is pulled in a
    try/except, logged to run_log.jsonl, and its metrics fall back to last-good
    display JSON already on disk.
  * The render step reads only build_data/, so --render is deterministic.

Phase 1 wires FRED today; BBG + free sources fill in as their pull+compute land
(their stubs raise NotImplementedError, which is caught and logged, not fatal).
"""
from __future__ import annotations

from . import quiet  # noqa: F401 — import side effect: silence known-benign warnings

import argparse
import traceback
from datetime import date, datetime

from . import config, store
from .render import render


def _safe(source_name: str, fn, *a, **kw):
    """Run a pull/compute step; log status; swallow failure (fall back to last-good)."""
    try:
        result = fn(*a, **kw)
        store.log_run(source_name, "ok")
        return result
    except NotImplementedError as e:
        store.log_run(source_name, "todo", str(e))
    except Exception as e:  # noqa: BLE001 — soft-fail by design
        store.log_run(source_name, "fail", f"{e}\n{traceback.format_exc(limit=2)}")
    return None


def _pull_snapshot_universe():
    """Phase-3 snapshot universe: retail top-25 (latest tape day) + majors + semis."""
    from .pull import massive
    rd = massive.read_retail_daily()
    top25 = []
    if rd is not None and not rd.empty:
        latest = rd[rd["date"] == rd["date"].max()]
        top25 = latest.nlargest(25, "retail_ident_usd")["ticker"].tolist()
    univ = sorted(set(top25) | {"SPY", "QQQ", "IWM"} | set(config.SEMI_TOP10))
    return massive.pull_snapshots(univ)


def _member_tickers() -> list[str]:
    members = store.read_all("bbg_spx_members")
    if members is None or members.empty:
        return []
    latest = members[members["date"] == members["date"].max()]
    return latest.drop_duplicates("ticker")["ticker"].tolist()


def pull_all():
    """Attempt every enabled source for the current PHASE. Order: heavy first."""
    from .pull import bbg, edgar, fred  # finra, free, massive wired as they land
    _safe("bbg", bbg.pull)       # heaviest (member snapshot ~503 tickers)
    _safe("bbg:etf", bbg.pull_etf_universe)
    _safe("bbg:iv", bbg.pull_iv_histories)
    _safe("bbg:box", bbg.pull_box_yield)
    _safe("bbg:si", lambda: bbg.pull_short_interest(_member_tickers()))
    from .pull import free
    _safe("free", free.pull)
    _safe("edgar", edgar.pull)   # backfill is ~27 files, cached after first run
    _safe("fred", fred.pull)
    from .pull import finra
    _safe("finra", finra.pull)
    # _safe("free", free.cboe_0dte); ...
    if config.PHASE >= 2:
        import os as _os

        from .pull import massive
        _safe("massive:grouped", massive.pull_grouped_phase2)
        # full-tape RF1 pass — target the most recent ACTUAL trading day (from
        # the grouped lake the step above just refreshed), so Mondays/holidays
        # pull Friday's tape rather than an empty 'yesterday'. SKIP if the day is
        # already in the lake (backfill lanes or a prior run may have done it;
        # re-doing costs ~13GB + 30min).
        from datetime import timedelta
        bday = massive.latest_grouped_day()
        if bday is None:  # grouped lake empty — fall back to the last weekday
            d = date.today() - timedelta(days=1)
            while d.weekday() >= 5:  # Sat/Sun → step back to Friday
                d -= timedelta(days=1)
            bday = d.isoformat()
        if not _os.path.exists(_os.path.join(store.LAKE_DIR, massive.RETAIL_TABLE, f"{bday}.parquet")):
            _safe("massive:tape", massive.process_tape_day, bday)
        else:
            store.log_run("massive:tape", "skip", f"{bday} already in lake")
    if config.PHASE >= 3:
        if not _os.path.exists(_os.path.join(store.LAKE_DIR, massive.OPRA_TABLE, f"{bday}.parquet")):
            _safe("massive:opra", massive.process_opra_day, bday)
        else:
            store.log_run("massive:opra", "skip", f"{bday} already in lake")
        _safe("massive:snapshots", _pull_snapshot_universe)


def compute_all():
    """Turn lake data into display JSON (build_data/*.json). Each metric's compute
    lands here as it is built; missing computes simply leave last-good in place."""
    from .compute import (health, ipo, issuance, leverage, opra, ownership,
                          retail_series, structure, volatility)
    for name, mod in (("structure", structure), ("ownership", ownership),
                      ("volatility", volatility), ("leverage", leverage),
                      ("health", health), ("issuance", issuance), ("ipo", ipo),
                      ("retail", retail_series), ("opra", opra)):
        result = _safe(f"compute:{name}", mod.build)
        if result:
            store.log_run(f"compute:{name}", "detail",
                          " ".join(f"{k}={'ok' if v else 'skip'}" for k, v in result.items()))


def main():
    ap = argparse.ArgumentParser(description="Market Pulse Dashboard build")
    ap.add_argument("--render", action="store_true", help="re-render only, no pulls")
    ap.add_argument("--commit", action="store_true",
                    help="commit + push the refreshed artifacts (build_data/, APPENDIX.md); "
                         "also enabled standing via AUTO_COMMIT_ARTIFACTS=1 in infra/config.env")
    ap.add_argument("--no-push", action="store_true",
                    help="with --commit: commit locally but never push")
    ap.add_argument("--build-version", default="dev")
    args = ap.parse_args()

    run_date = date.today().isoformat()
    run_start = datetime.now()
    if not args.render:
        pull_all()
        compute_all()
    out = render.build(run_date=run_date, build_version=args.build_version)
    print(f"Rendered {out}")
    # drop the agent-readable bundle (manifest + per-metric JSON + methodology)
    # into AGENT_BUNDLE_DIR for Cowork synthesis — soft-fail, never kills the run.
    from . import export_bundle
    res = _safe("export:bundle", export_bundle.export)
    if res and res.get("exported"):
        print(f"Agent bundle: {res['metric_count']} metrics + {res['docs']} docs -> {res['dest']}")
    elif res and res.get("reason"):
        print(f"Agent bundle: skipped ({res['reason']})")
    # commit (and push) the tracked artifacts this run rewrote, so they don't pile
    # up as uncommitted edits — soft-fail, never blocks the deploy (see sync.py).
    from . import sync
    if args.commit or sync.enabled():
        res = _safe("git:sync", sync.commit_and_push, push=not args.no_push)
        print(sync.describe(res))
    # post-run digest: per-source PASS/FAIL/SKIP + stale-metric check (§2) so the
    # console tells you whether the run was clean without grepping run_log.jsonl
    from . import report
    print(report.render(since=run_start))
    print("Deploy with:  python deploy.py market-conditions")


if __name__ == "__main__":
    main()
