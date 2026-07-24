"""export_bundle.py — write an agent-readable snapshot of the dashboard into a
shared directory (e.g. OneDrive) so Claude Cowork instances can synthesize the
dashboard WITHOUT the auth-gated web page.

Runs as the final step of `python -m src.run` (see run.py). The target dir is
config.AGENT_BUNDLE_DIR (from $AGENT_BUNDLE_DIR or infra/config.env); if unset or
unwritable the export is skipped/logged and the run continues — never fatal (§2).

Bundle layout (all files overwritten each run):
  manifest.json        one file, every metric: tile + status + notes + recent
                       series, grouped by panel — the agent's starting point.
  metrics/<id>.json    full per-metric display JSON (complete history) for drill-down.
  APPENDIX.md          per-metric methodology + the meaning of each status flag.
  DASHBOARD-SPEC.md    metric definitions and data lineage.
  READ_ME_FIRST.md     how to read the bundle + interpretation cautions.
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone

from . import config, store

RECENT_POINTS = 60  # points per series kept inline in manifest.json (full set in metrics/)
DOC_FILES = ("APPENDIX.md", "DASHBOARD-SPEC.md")


def _trim_series(series: list | None) -> list:
    """Keep the tail of each series inline in the manifest; note the full length."""
    out = []
    for s in series or []:
        pts = s.get("points") or []
        out.append({
            "name": s.get("name"),
            "role": s.get("role"),
            "unit": s.get("unit"),
            "points_total": len(pts),
            "points_shown": min(len(pts), RECENT_POINTS),
            "points": pts[-RECENT_POINTS:],
        })
    return out


def _manifest() -> dict:
    panels = []
    for key, label in config.PANELS:
        metrics = []
        for m in config.metrics_for_panel(key):
            d = store.read_display(m.id)
            if not d:
                continue  # metric not built yet (phase-gated or awaiting data) — skip
            metrics.append({
                "id": d.get("id", m.id),
                "name": d.get("name", m.name),
                "asof": d.get("asof"),
                "unit": d.get("unit"),
                "cadence": d.get("cadence", m.cadence),
                "source": d.get("source", m.source),
                "status": d.get("status"),        # {level, label} — e.g. provisional / uncalibrated
                "tile": d.get("tile"),            # {value, delta, percentile} — the headline read
                "tooltip": d.get("tooltip"),
                "notes": d.get("notes"),          # methodology + scaling caveats — READ THESE
                "series": _trim_series(d.get("series")),
            })
        if metrics:
            panels.append({"key": key, "label": label, "metrics": metrics})
    asofs = [mm["asof"] for p in panels for mm in p["metrics"] if mm.get("asof")]
    return {
        "app": config.APP_SLUG,
        "title": "US Market Conditions",
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_asof": max(asofs) if asofs else None,
        "phase": config.PHASE,
        "panel_count": len(panels),
        "metric_count": sum(len(p["metrics"]) for p in panels),
        "notes": ("Derived/aggregated dashboard data, cleared for publication. NOT raw trade "
                  "data. Always defer to each metric's 'status' and 'notes' fields — some "
                  "metrics are provisional/uncalibrated or ×3-scaled estimates."),
        "panels": panels,
    }


def _read_me(manifest: dict) -> str:
    return f"""# US Market Conditions — agent bundle

Snapshot of the internal market-conditions dashboard, refreshed each time
`python -m src.run` completes. Read this so you interpret the metrics correctly.

Generated: {manifest['generated_utc']} · latest data as-of: {manifest['latest_asof']}
Metrics: {manifest['metric_count']} across {manifest['panel_count']} panels · phase {manifest['phase']}

## Where to look
- `manifest.json` — start here. Every metric grouped by panel, each with its
  headline `tile` (value, trailing percentile, delta), a `status` flag, `notes`,
  and the most recent {RECENT_POINTS} series points.
- `metrics/<id>.json` — the full history for one metric (drill-down).
- `APPENDIX.md` — how each metric is computed and what its status flag means.
- `DASHBOARD-SPEC.md` — metric definitions and data lineage.

## How to read a metric
- `tile.value` is the latest reading; `tile.percentile` is its rank within its
  OWN trailing history (0–100), or `null` when history is too short to rank —
  in that case report the level, not a percentile.
- `status.level` matters. `provisional`, `uncalibrated`, and `classifier floor`
  mean the metric is not production-grade — weight it accordingly and say so.
- Dollar-denominated retail metrics may be ×3-scaled to an ESTIMATED TOTAL
  (see each metric's `notes`); ratios/slopes are shown unscaled. Don't compare
  a scaled level against an unscaled one.
- `asof` varies by `cadence` (daily / weekly / quarterly) — check per metric
  before saying "today".

## Cautions
- This is a derivative already cleared for the dashboard, not the raw licensed
  tape. Do not treat series points as individual trades.
- When a metric looks extreme, read its `notes` before concluding — several
  carry known caveats.
"""


def export() -> dict:
    """Write the bundle. Returns a small summary dict. Raises on hard I/O error
    (run.py wraps this in _safe, so a failure is logged and the run continues)."""
    dest = config.AGENT_BUNDLE_DIR
    if not dest:
        store.log_run("export:bundle", "skip", "AGENT_BUNDLE_DIR not set")
        return {"exported": False, "reason": "AGENT_BUNDLE_DIR not set"}

    os.makedirs(dest, exist_ok=True)
    metrics_dir = os.path.join(dest, "metrics")
    os.makedirs(metrics_dir, exist_ok=True)

    manifest = _manifest()
    tmp = os.path.join(dest, "manifest.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, os.path.join(dest, "manifest.json"))

    # full per-metric display JSON for drill-down
    n_metrics = 0
    if os.path.isdir(store.DISPLAY_DIR):
        for fn in os.listdir(store.DISPLAY_DIR):
            if fn.endswith(".json"):
                shutil.copy2(os.path.join(store.DISPLAY_DIR, fn),
                             os.path.join(metrics_dir, fn))
                n_metrics += 1

    # methodology docs
    n_docs = 0
    for doc in DOC_FILES:
        src = os.path.join(store.BASE, doc)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dest, doc))
            n_docs += 1

    with open(os.path.join(dest, "READ_ME_FIRST.md"), "w", encoding="utf-8") as f:
        f.write(_read_me(manifest))

    store.log_run("export:bundle", "ok",
                  f"{manifest['metric_count']} metrics, {n_metrics} files, {n_docs} docs -> {dest}")
    return {"exported": True, "dest": dest, "metric_count": manifest["metric_count"],
            "files": n_metrics, "docs": n_docs}
