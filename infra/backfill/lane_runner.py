#!/usr/bin/env python3
"""lane_runner.py — orchestrates the backfill lanes ON the EC2 box.

Started by bootstrap.sh (user-data) with MCD_BUCKET/MCD_REGION set. Spawns:
  tape1/tape2  full-tape quotes lanes over TAPE_START→yesterday, split by
               missing-day count (quotes=True upgrades trades-only days to
               midpoint signing — feeds RF4/RF9)
  opra         OPRA trades back to OPRA_START (~80MB/day, fast)
  grouped      grouped-daily REST bars back to GROUPED_START (1 call/day)

Every SYNC_EVERY seconds the aggregate lake syncs to s3://$MCD_BUCKET/lake/
and a progress snapshot lands at status/status.json. A Spot interruption
notice (2-minute warning) triggers a final sync, so no completed day is lost.
Lanes are the SAME resumable src/pull code the workstation runs — a reclaimed
box relaunched from the template picks up exactly where this one stopped.
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import date, timedelta
from multiprocessing import Process

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

BUCKET = os.environ["MCD_BUCKET"]
REGION = os.environ.get("MCD_REGION", "us-east-2")

TAPE_START = "2026-01-02"     # quotes lanes re-do trades-only days (signing upgrade)
TAPE_LANES = 2                # >2-3 heavy lanes → Massive S3 503s (session notes)
OPRA_START = "2024-01-01"     # extends the existing 2024-08-30+ OPRA history
GROUPED_START = "2016-01-04"  # matches pull_grouped_phase2 target
SYNC_EVERY = 600
TAPE_BATCH = 6                # days per backfill_tape() call between status writes

LAKE = os.path.join(ROOT, "data", "dashboard")
LANE_DIR = os.path.join(ROOT, "_lane_status")
EXCLUDES = ["_tape_scratch*", "*.duckdb*", "run_log.jsonl", "*.tmp"]


def _sh(cmd):
    return subprocess.run(cmd, check=False)


def sync_up():
    cmd = ["aws", "s3", "sync", LAKE, f"s3://{BUCKET}/lake/",
           "--only-show-errors", "--region", REGION]
    for e in EXCLUDES:
        cmd += ["--exclude", e]
    _sh(cmd)


def _yesterday() -> str:
    return (date.today() - timedelta(days=1)).isoformat()


def _write_lane(name: str, **kw):
    os.makedirs(LANE_DIR, exist_ok=True)
    kw.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S"))
    p = os.path.join(LANE_DIR, f"{name}.json")
    with open(p + ".tmp", "w") as f:
        json.dump(kw, f)
    os.replace(p + ".tmp", p)


# ---- lane workers (each its own process; private scratch via TAPE_SCRATCH) ----
def tape_lane(name: str, lo: str, hi: str):
    os.environ["TAPE_SCRATCH"] = os.path.join(LAKE, f"_tape_scratch_{name}")
    from src.pull.massive import backfill_tape
    total, stall = 0, 0
    while stall < 2:  # two consecutive empty batches = range done (or all-skips)
        done = backfill_tape(lo, end=hi, max_days=TAPE_BATCH, quotes=True)
        total += len(done)
        stall = stall + 1 if not done else 0
        _write_lane(name, state="running", range=[lo, hi], done_total=total,
                    last_batch=done)
        if not done and stall < 2:
            time.sleep(30)
    _write_lane(name, state="done", range=[lo, hi], done_total=total)
    print(f"{name}: done ({total} days)", flush=True)


def opra_lane(name: str):
    os.environ["TAPE_SCRATCH"] = os.path.join(LAKE, f"_tape_scratch_{name}")
    from src.pull.massive import backfill_opra
    total, stall = 0, 0
    while stall < 2:
        done = backfill_opra(OPRA_START, max_days=30)
        total += len(done)
        stall = stall + 1 if not done else 0
        _write_lane(name, state="running", start=OPRA_START, done_total=total,
                    last_batch=done[-3:])
        if not done and stall < 2:
            time.sleep(30)
    _write_lane(name, state="done", start=OPRA_START, done_total=total)
    print(f"{name}: done ({total} days)", flush=True)


def grouped_lane(name: str):
    from src.pull.massive import pull_grouped_range
    total, fails = 0, 0
    while True:
        try:
            n = pull_grouped_range(GROUPED_START, max_calls=250)
        except Exception as e:  # noqa: BLE001 — REST blips; retry then give up
            fails += 1
            _write_lane(name, state="running", error=str(e)[:150], done_total=total)
            if fails >= 5:
                break
            time.sleep(30)
            continue
        fails = 0
        total += n
        _write_lane(name, state="running", done_total=total)
        if n == 0:
            break
    _write_lane(name, state="done", done_total=total)
    print(f"{name}: done ({total} days)", flush=True)


# ---- progress accounting -------------------------------------------------------
def tape_missing() -> list[str]:
    """Days in [TAPE_START, yesterday] absent from the lake or stored without
    quote signing — mirrors backfill_tape's quotes-lane 'have' rule."""
    import pandas as pd
    from src.pull.massive import RETAIL_TABLE, _day_signing
    tdir = os.path.join(LAKE, RETAIL_TABLE)
    os.makedirs(tdir, exist_ok=True)
    have = {f.split(".")[0] for f in os.listdir(tdir)
            if f.endswith(".parquet")
            and _day_signing(os.path.join(tdir, f)) != "none"}
    return [d for d in pd.bdate_range(TAPE_START, _yesterday()).strftime("%Y-%m-%d")
            if d not in have]


def table_stats() -> dict:
    out = {}
    for t in ("massive_retail_daily", "massive_opra_daily", "massive_grouped_daily"):
        tdir = os.path.join(LAKE, t)
        if os.path.isdir(tdir):
            days = sorted(f[:-8] for f in os.listdir(tdir) if f.endswith(".parquet"))
            if days:
                out[t] = {"days": len(days), "first": days[0], "last": days[-1]}
    return out


def signed_days() -> int:
    from src.pull.massive import RETAIL_TABLE, _day_signing
    tdir = os.path.join(LAKE, RETAIL_TABLE)
    if not os.path.isdir(tdir):
        return 0
    return sum(1 for f in os.listdir(tdir) if f.endswith(".parquet")
               and _day_signing(os.path.join(tdir, f)) == "midpoint")


def lane_states() -> dict:
    out = {}
    if os.path.isdir(LANE_DIR):
        for f in os.listdir(LANE_DIR):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(LANE_DIR, f)) as fh:
                        out[f[:-5]] = json.load(fh)
                except Exception:  # noqa: BLE001 — mid-write race; next pass gets it
                    pass
    return out


def push_status(state: str) -> dict:
    st = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "state": state,
          "signed_days": signed_days(),
          "tape_days_remaining": len(tape_missing()),
          "tables": table_stats(),
          "lanes": lane_states(),
          "disk_free_gb": round(shutil.disk_usage(LAKE).free / 1e9, 1)}
    p = "/tmp/mcd_status.json"
    with open(p, "w") as f:
        json.dump(st, f, indent=1)
    _sh(["aws", "s3", "cp", p, f"s3://{BUCKET}/status/status.json",
         "--only-show-errors", "--region", REGION])
    log = "/var/log/mcd-lane-runner.log"
    if os.path.exists(log):
        _sh(["aws", "s3", "cp", log, f"s3://{BUCKET}/status/lane-runner.log",
             "--only-show-errors", "--region", REGION])
    return st


def spot_notice() -> str | None:
    """IMDSv2 poll — non-None inside the 2-minute reclaim window."""
    try:
        rq = urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "120"})
        tok = urllib.request.urlopen(rq, timeout=2).read().decode()
        rq = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/spot/instance-action",
            headers={"X-aws-ec2-metadata-token": tok})
        return urllib.request.urlopen(rq, timeout=2).read().decode()
    except Exception:  # noqa: BLE001 — 404 = no notice (the normal case)
        return None


def main():
    print(f"lane_runner: bucket={BUCKET} region={REGION}", flush=True)
    procs: dict[str, Process] = {}

    missing = tape_missing()
    print(f"tape: {len(missing)} days missing/unsigned in "
          f"[{TAPE_START}, {_yesterday()}]", flush=True)
    if missing:
        if len(missing) >= 10 and TAPE_LANES > 1:
            mid = len(missing) // 2
            chunks = [(missing[0], missing[mid - 1]), (missing[mid], missing[-1])]
        else:
            chunks = [(missing[0], missing[-1])]
        for i, (lo, hi) in enumerate(chunks, 1):
            procs[f"tape{i}"] = Process(target=tape_lane, args=(f"tape{i}", lo, hi))
    procs["opra"] = Process(target=opra_lane, args=("opra",))
    procs["grouped"] = Process(target=grouped_lane, args=("grouped",))
    for p in procs.values():
        p.start()
    push_status("starting")

    last = time.time()
    while any(p.is_alive() for p in procs.values()):
        if spot_notice():
            print("SPOT INTERRUPTION NOTICE — final sync", flush=True)
            sync_up()
            push_status("interrupted")
            return
        if time.time() - last >= SYNC_EVERY:
            sync_up()
            st = push_status("running")
            print(f"sync: signed={st['signed_days']} "
                  f"tape_remaining={st['tape_days_remaining']} "
                  + " ".join(f"{k.replace('massive_', '')}={v['days']}"
                             for k, v in st["tables"].items()), flush=True)
            last = time.time()
        time.sleep(20)

    sync_up()
    push_status("done")
    print("ALL LANES DONE — lake synced; box idle, terminate when verified", flush=True)


if __name__ == "__main__":
    main()
