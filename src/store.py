"""store.py — the two-layer store (§2).

Layer 1  DuckDB + Parquet lake under data/dashboard/  (raw + aggregates).
Layer 2  build_data/*.json display layer the renderer embeds.

The renderer touches ONLY layer 2, so the build is deterministic and offline.
DuckDB is imported lazily so the compute/render path runs on machines without
it (and so tests don't need it).

Display-layer JSON schema (one file per metric id, lower-cased):
  {
    "id": "SC1", "name": "...", "panel": "structure", "source": "BBG",
    "cadence": "daily", "asof": "YYYY-MM-DD", "unit": "%",
    "series": [{"name": "Top-10 weight", "points": [{"date":..,"value":..}, ...],
                "role": "avos"|"benchmark", "estimated_from": "YYYY-MM-DD"|null}],
    "tile": {"value": .., "delta": .., "percentile": ..|null},
    "provenance": "bloomberg_cache"|"fred_cache"|..., "notes": "..."
  }
"""
from __future__ import annotations

import json
import os
from datetime import datetime

BASE = os.environ.get("WORKSPACE") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAKE_DIR = os.path.join(BASE, "data", "dashboard")
DISPLAY_DIR = os.path.join(BASE, "build_data")
RUN_LOG = os.path.join(LAKE_DIR, "run_log.jsonl")


def _ensure_dirs():
    os.makedirs(LAKE_DIR, exist_ok=True)
    os.makedirs(DISPLAY_DIR, exist_ok=True)


def _atomic_write(path: str, text: str):
    """tmp-then-rename so a partial write can't trash a cache (house pattern)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ---- Layer 2: display layer ------------------------------------------------
def write_display(metric_id: str, payload: dict):
    """Write one metric's display JSON. Sorted keys → byte-identical rerun (§7.1)."""
    _ensure_dirs()
    path = os.path.join(DISPLAY_DIR, f"{metric_id.lower()}.json")
    _atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))
    return path


def read_display(metric_id: str) -> dict | None:
    path = os.path.join(DISPLAY_DIR, f"{metric_id.lower()}.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_all_display() -> dict[str, dict]:
    """Every metric's display JSON, keyed by upper-case id — the renderer's input."""
    _ensure_dirs()
    out = {}
    for fn in sorted(os.listdir(DISPLAY_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(DISPLAY_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            out[d.get("id", fn[:-5].upper())] = d
    return out


# ---- run log (§2 failure handling) -----------------------------------------
def log_run(source: str, status: str, detail: str = "", **extra):
    """Append one per-source status line to run_log.jsonl."""
    _ensure_dirs()
    rec = {"ts": datetime.now().isoformat(timespec="seconds"),
           "source": source, "status": status, "detail": detail, **extra}
    with open(RUN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---- Layer 1: lake (lazy DuckDB) -------------------------------------------
def lake_conn():
    """Open the DuckDB lake. Lazy import so compute/render don't require duckdb."""
    import duckdb  # noqa: local import by design
    _ensure_dirs()
    return duckdb.connect(os.path.join(LAKE_DIR, "lake.duckdb"))


def read_latest(table: str):
    """Read the most recent Parquet pull for a table (by filename = pulled_at
    stamp). Returns a pandas DataFrame or None if the table has no pulls yet —
    computes treat None as 'leave last-good display JSON in place' (§2)."""
    import pandas as pd
    tdir = os.path.join(LAKE_DIR, table)
    if not os.path.isdir(tdir):
        return None
    files = sorted(f for f in os.listdir(tdir) if f.endswith(".parquet"))
    if not files:
        return None
    return pd.read_parquet(os.path.join(tdir, files[-1]))


def read_all(table: str):
    """Concatenate every Parquet pull for a table (append-only history — used
    where each run contributes a snapshot, e.g. daily member weights)."""
    import pandas as pd
    tdir = os.path.join(LAKE_DIR, table)
    if not os.path.isdir(tdir):
        return None
    files = sorted(f for f in os.listdir(tdir) if f.endswith(".parquet"))
    if not files:
        return None
    return pd.concat(
        [pd.read_parquet(os.path.join(tdir, f)) for f in files], ignore_index=True)


def append_parquet(table: str, df, pulled_at: str = None):
    """Append a pull to the Parquet lake, stamped with pulled_at (§2 append-only).
    df is a pandas DataFrame; requires pyarrow."""
    _ensure_dirs()
    pulled_at = pulled_at or datetime.now().isoformat(timespec="seconds")
    d = df.copy()
    d["pulled_at"] = pulled_at
    tdir = os.path.join(LAKE_DIR, table)
    os.makedirs(tdir, exist_ok=True)
    fn = os.path.join(tdir, f"{pulled_at.replace(':', '').replace('-', '')}.parquet")
    d.to_parquet(fn, index=False)
    return fn
