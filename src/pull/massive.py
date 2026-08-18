"""massive.py — Massive (ex-Polygon) pulls (§3). Phase 2/3.

Access shapes (probed live 2026-07-08):
  * REST at api.polygon.io — API key works, both plans entitled (stocks SIP
    trades/quotes + options). `api.massive.com` not yet serving.
  * Flat files at files.polygon.io (S3): needs DEDICATED S3 credentials from
    the Massive dashboard — the API key 403s. Drop them at repo root as
    `.massive_s3_keys`, one line `ACCESS_KEY_ID:SECRET`.

Data-handling doctrine (§2): raw tape is NEVER persisted. Flat files stream
through DuckDB (ASOF join trades↔quotes), reduce to per-symbol-per-day
aggregates, and the raw download is deleted. Only aggregates enter the lake.

Grouped daily bars (1 REST call = whole market, ~12.4k symbols) are the cheap
workhorse for SC5 and any close/volume need; backfill is resumable, one call
per trading day, stored whole-market per day (~0.5 MB/day parquet).
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta

import pandas as pd

from . import _net

BASE = os.environ.get("WORKSPACE") or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KEY_FILE = os.path.join(BASE, ".massive_api_key")
S3_KEY_FILE = os.path.join(BASE, ".massive_s3_keys")
REST = "https://api.polygon.io"

GROUPED_TABLE = "massive_grouped_daily"   # one parquet per trading day

# Exchange test/dummy securities (Nasdaq UTP + NYSE/Arca/CTA). They carry
# absurd prices and periodic fake volume — e.g. ZAZZT printed $87k × 90M sh =
# $7.8T notional on 2025-05-23, ~2.5× the entire real tape, tripling implied
# $/share that week. Never real activity; stripped from every grouped read so
# no tape aggregate (retail $/share, participation denominators, dispersion…)
# inherits the artifact.
TEST_SYMBOLS = frozenset({
    "ZAZZT", "ZBZZT", "ZCZZT", "ZEXIT", "ZIEXT", "ZJZZT", "ZTEST",
    "ZVZZT", "ZVZZC", "ZWZZT", "ZXZZT", "ZZZOT", "ZBZX",   # Nasdaq
    "NTEST", "ZTST", "ZXIET",                               # NYSE/Arca/CTA
})


def _key() -> str:
    if not os.path.exists(KEY_FILE):
        raise FileNotFoundError(f"Massive API key not found at {KEY_FILE}.")
    return open(KEY_FILE).read().strip()


def _s3_creds() -> tuple[str, str]:
    """Accepts .massive_s3_keys or .massive_s3_key, in either format:
      one line   ACCESS_KEY_ID:SECRET
      labeled    'Access Key ID: …' / 'Secret Access Key: …' (endpoint line ignored)
    """
    path = next((p for p in (S3_KEY_FILE, S3_KEY_FILE.rstrip("s"))
                 if os.path.exists(p)), None)
    if path is None:
        raise FileNotFoundError(
            f"Massive flat-file S3 credentials not found at {S3_KEY_FILE}. "
            "Generate in the Massive dashboard (Flat Files → Access Keys).")
    lines = [l.strip() for l in open(path).read().strip().splitlines() if l.strip()]
    if len(lines) == 1 and ":" in lines[0]:
        a, s = lines[0].split(":", 1)
        return a.strip(), s.strip()
    access = secret = None
    for l in lines:
        low = l.lower()
        if "access key id" in low:
            access = l.split(":", 1)[1].strip()
        elif "secret" in low:
            secret = l.split(":", 1)[1].strip()
    if not (access and secret):
        raise ValueError(f"Could not parse S3 credentials from {path}.")
    return access, secret


# ---- REST: grouped daily bars ------------------------------------------------
def grouped_daily(day: str) -> pd.DataFrame:
    """All-market daily OHLCV for one date → [ticker, open, high, low, close,
    volume, trades]. Empty frame on non-trading days."""
    s = _net.session()
    r = s.get(f"{REST}/v2/aggs/grouped/locale/us/market/stocks/{day}",
              params={"adjusted": "true", "apiKey": _key()}, timeout=60)
    r.raise_for_status()
    js = r.json()
    rows = js.get("results") or []
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={
        "T": "ticker", "o": "open", "h": "high", "l": "low",
        "c": "close", "v": "volume", "n": "trades"})
    df["date"] = day
    keep = [c for c in ("date", "ticker", "open", "high", "low", "close", "volume", "trades") if c in df.columns]
    return df[keep]


def _grouped_have() -> set[str]:
    tdir = os.path.join(BASE, "data", "dashboard", GROUPED_TABLE)
    if not os.path.isdir(tdir):
        return set()
    return {f.split(".")[0] for f in os.listdir(tdir) if f.endswith(".parquet")}


def pull_grouped_range(start: str, end: str | None = None, max_calls: int = 300) -> int:
    """Resumable grouped-daily backfill: pulls missing weekdays in [start, end],
    newest first (recent data is the most valuable), up to max_calls per run.
    One parquet per day named by date → idempotent, resumes across runs."""
    from .. import store
    end_d = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
    start_d = date.fromisoformat(start)
    have = _grouped_have()
    tdir = os.path.join(store.LAKE_DIR, GROUPED_TABLE)
    os.makedirs(tdir, exist_ok=True)

    days = [d for d in pd.bdate_range(start_d, end_d).strftime("%Y-%m-%d") if d not in have]
    days = sorted(days, reverse=True)[:max_calls]
    pulled = 0
    for day in days:
        df = grouped_daily(day)
        # holidays return empty — write a marker frame so we don't re-ask
        out = df if not df.empty else pd.DataFrame(
            [{"date": day, "ticker": "_HOLIDAY_", "close": None}])
        out.to_parquet(os.path.join(tdir, f"{day}.parquet"), index=False)
        pulled += 1
        time.sleep(0.05)
    return pulled


def read_grouped(days_back: int | None = None) -> pd.DataFrame | None:
    """Read the grouped-daily lake (optionally only the trailing N files)."""
    tdir = os.path.join(BASE, "data", "dashboard", GROUPED_TABLE)
    if not os.path.isdir(tdir):
        return None
    files = sorted(f for f in os.listdir(tdir) if f.endswith(".parquet"))
    if days_back:
        files = files[-days_back:]
    if not files:
        return None
    df = pd.concat([pd.read_parquet(os.path.join(tdir, f)) for f in files],
                   ignore_index=True)
    return df[(df["ticker"] != "_HOLIDAY_") & (~df["ticker"].isin(TEST_SYMBOLS))]


def latest_grouped_day() -> str | None:
    """Most recent ACTUAL trading day in the grouped-daily lake — weekends and
    holiday-marker days excluded. The daily grouped pull runs first and refreshes
    this to the true last business day, so the tape/OPRA pull can target it
    instead of a literal 'yesterday' that lands on a weekend or holiday."""
    df = read_grouped(days_back=15)
    if df is None or df.empty:
        return None
    return pd.to_datetime(df["date"]).max().strftime("%Y-%m-%d")


# ---- Flat files (S3) — trades/quotes tape ------------------------------------
def flatfile_download(dataset: str, day: str, dest_dir: str) -> str:
    """Download one day's flat file (e.g. us_stocks_sip/trades_v1) to dest_dir.
    Caller is responsible for deleting after aggregation (§2: prune raw tape)."""
    import boto3
    from botocore.config import Config
    a, s = _s3_creds()
    s3 = boto3.client("s3", endpoint_url="https://files.polygon.io",
                      aws_access_key_id=a, aws_secret_access_key=s,
                      config=Config(signature_version="s3v4",
                                    retries={"max_attempts": 8, "mode": "adaptive"}))
    y, m, _ = day.split("-")
    key = f"{dataset}/{y}/{m}/{day}.csv.gz"
    os.makedirs(dest_dir, exist_ok=True)
    local = os.path.join(dest_dir, key.replace("/", "_"))
    s3.download_file("flatfiles", key, local)
    return local


def pull_grouped_phase2() -> dict:
    """Daily-run entry: keep the grouped-daily lake current (small incremental
    top-up; the deep backfill runs via pull_grouped_range explicitly)."""
    n = pull_grouped_range("2016-01-04", max_calls=30)
    return {"grouped_days_pulled": n}


class _SkipQuotes(Exception):
    pass


def _day_signing(path: str) -> str:
    """Read just the signing mode of a stored day (cheap single-column read)."""
    try:
        return str(pd.read_parquet(path, columns=["signing"])["signing"].iloc[0])
    except Exception:  # noqa: BLE001 — holiday markers have no signing column
        return "holiday"


# ---- RF1 daily tape processor -------------------------------------------------
RETAIL_TABLE = "massive_retail_daily"   # per-symbol-per-day classifier aggregates
# Long-format per-symbol cut of the identified-ELIGIBLE set by notional / sale
# condition / quoted spread, BEFORE the size and condition filters. This is what
# makes a future threshold change arithmetic on the lake instead of another
# ~2TB tape pull: any cap or condition list is recoverable by summing buckets.
RETAIL_BUCKETS_TABLE = "massive_retail_buckets"


def _day_method_version(path: str) -> int:
    """Classifier methodology version stamped into a stored day.
    0 = written before the stamp existed, i.e. pre-2026-08 methodology."""
    try:
        return int(pd.read_parquet(path, columns=["method_version"])
                   ["method_version"].iloc[0])
    except Exception:  # noqa: BLE001 — column absent on older days / holidays
        return 0


def _is_holiday_marker(path: str) -> bool:
    """Holiday days are a one-row {date, ticker:_HOLIDAY_} frame with no other
    columns — they must stay 'done' forever or every reprocess re-asks S3 for a
    file that does not exist."""
    try:
        t = pd.read_parquet(path, columns=["ticker"])["ticker"]
        return bool(len(t)) and bool((t == "_HOLIDAY_").all())
    except Exception:  # noqa: BLE001
        return False


def _have_days(tdir: str, quotes: bool = True) -> set[str]:
    """Days needing no work. ONE definition, shared by backfill_tape and the EC2
    lane runner — they previously carried independent copies of this rule and
    could disagree about what was complete.

    Done = holiday marker, OR (current method_version AND, in quotes mode,
    midpoint-signed). Days predating the stamp report version 0 and are always
    reprocessed. That is the whole point: the pre-2026-08 rule treated any
    signed day as complete, so a methodology change made the lanes no-op while
    logging success — a box that ran, synced, and recomputed nothing.
    """
    from .. import config as _cfg
    have: set[str] = set()
    for f in os.listdir(tdir):
        if not f.endswith(".parquet"):
            continue
        p, d = os.path.join(tdir, f), f.split(".")[0]
        if _is_holiday_marker(p):
            have.add(d)
            continue
        if _day_method_version(p) != _cfg.RETAIL_METHOD_VERSION:
            continue
        if quotes and _day_signing(p) == "none":
            continue
        have.add(d)
    return have


def process_tape_day(day: str, keep_files: bool = False,
                     use_quotes: bool = True) -> pd.DataFrame:
    """One day's full-tape pass: download trades+quotes flat files, run the
    §5.1 classifier (compute/retail.py), store per-symbol aggregates in the
    lake, DELETE the raw files (§2 doctrine). Needs .massive_s3_keys.

    Sizing: trades ~2-8 GB gz, quotes ~15-30 GB gz per day. DuckDB streams
    both out-of-core; workstation disk needs ~40 GB transient headroom."""
    from botocore.exceptions import ClientError

    from .. import store
    from ..compute.retail import classify_day
    # TAPE_SCRATCH env lets concurrent lanes use private scratch dirs — the
    # stale-partial sweep in backfill_tape would otherwise delete another
    # lane's in-flight download (EC2 lane runner sets one per lane)
    scratch = os.environ.get("TAPE_SCRATCH") or os.path.join(
        BASE, "data", "dashboard", "_tape_scratch")
    trades_f = quotes_f = None
    try:
        # reuse a complete prior download if present (a failed run leaves one)
        cand = os.path.join(
            scratch, f"us_stocks_sip_trades_v1_{day[:4]}_{day[5:7]}_{day}.csv.gz")
        trades_f = cand if os.path.exists(cand) else \
            flatfile_download("us_stocks_sip/trades_v1", day, scratch)
        try:
            qcand = os.path.join(
                scratch, f"us_stocks_sip_quotes_v1_{day[:4]}_{day[5:7]}_{day}.csv.gz")
            quotes_f = qcand if os.path.exists(qcand) else \
                flatfile_download("us_stocks_sip/quotes_v1", day, scratch)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("403", "AccessDenied"):
                # quotes not entitled on this plan (found 2026-07-08):
                # trades-only mode — identification full, midpoint signing off
                store.log_run("massive:tape", "warn",
                              f"{day}: quotes not entitled — trades-only mode")
                quotes_f = None
            else:
                raise
        from .. import config as _cfg
        # regular-session bounds for the open/close 30-minute buckets. SIP
        # timestamps are epoch ns; the ET->UTC offset moves with DST, so derive
        # it from the trade date rather than hardcoding an offset.
        _open = pd.Timestamp(f"{day} 09:30:00", tz="America/New_York")
        _close = pd.Timestamp(f"{day} 16:00:00", tz="America/New_York")
        agg, buckets = classify_day(
            trades_f, quotes_f,
            max_print_usd=_cfg.RETAIL_MAX_PRINT_USD,
            exclude_conditions=_cfg.RETAIL_EXCLUDE_CONDITIONS,
            session_open_ns=int(_open.value), session_close_ns=int(_close.value),
            with_buckets=True)
        agg.insert(0, "date", day)
        # self-describing: the have-rule reads method_version to decide whether a
        # stored day predates the current methodology (see _day_method_version).
        agg["method_version"] = _cfg.RETAIL_METHOD_VERSION
        agg["max_print_usd"] = float(_cfg.RETAIL_MAX_PRINT_USD)
        tdir = os.path.join(store.LAKE_DIR, RETAIL_TABLE)
        os.makedirs(tdir, exist_ok=True)
        agg.to_parquet(os.path.join(tdir, f"{day}.parquet"), index=False)
        if buckets is not None and not buckets.empty:
            buckets.insert(0, "date", day)
            bdir = os.path.join(store.LAKE_DIR, RETAIL_BUCKETS_TABLE)
            os.makedirs(bdir, exist_ok=True)
            buckets.to_parquet(os.path.join(bdir, f"{day}.parquet"), index=False)
        return agg
    finally:
        if not keep_files:
            for f in (trades_f, quotes_f):
                if f:
                    try:
                        os.remove(f)
                    except OSError:
                        pass


def backfill_tape(start: str, max_days: int = 5, end: str | None = None,
                  quotes: bool = True) -> list[str]:
    """Resumable full-tape backfill: process missing days in [start, end],
    NEWEST first, up to max_days per invocation (each day ≈ 12GB transient
    download + ~minutes of DuckDB). Days already in RETAIL_TABLE are skipped,
    so re-invoking extends history backward until the range is covered."""
    from .. import store
    tdir = os.path.join(store.LAKE_DIR, RETAIL_TABLE)
    os.makedirs(tdir, exist_ok=True)
    # sweep stale boto3 partials (random suffix after .csv.gz) from killed runs
    scratch = os.environ.get("TAPE_SCRATCH") or os.path.join(
        BASE, "data", "dashboard", "_tape_scratch")
    if os.path.isdir(scratch):
        for f in os.listdir(scratch):
            if ".csv.gz." in f:
                try:
                    os.remove(os.path.join(scratch, f))
                except OSError:
                    pass
    # quotes lane treats trades-only days as MISSING (re-does them, upgrading
    # to midpoint signing); trades-only lane treats any stored day as done.
    # Version-aware since 2026-08 — see _have_days.
    have = _have_days(tdir, quotes=quotes)
    end_d = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
    days = [d for d in pd.bdate_range(date.fromisoformat(start), end_d)
            .strftime("%Y-%m-%d") if d not in have]
    done = []
    for day in sorted(days, reverse=True)[:max_days]:
        for attempt in range(3):
            try:
                agg = process_tape_day(day, use_quotes=quotes)
                done.append(day)
                store.log_run("massive:tape", "ok", f"{day}: {len(agg)} symbols")
                break
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "404" in msg or "Not Found" in msg or "NoSuchKey" in msg:
                    # holiday — mark so we never re-ask
                    pd.DataFrame([{"date": day, "ticker": "_HOLIDAY_"}]).to_parquet(
                        os.path.join(tdir, f"{day}.parquet"), index=False)
                    break
                # transient stream/proxy errors (SSL bad record mac, resets):
                # retry the day, then SKIP it and keep the run alive — a lost
                # day is re-attempted on the next invocation anyway
                store.log_run("massive:tape", "retry" if attempt < 2 else "skip",
                              f"{day} attempt {attempt + 1}: {msg[:100]}")
                if attempt == 2:
                    print(f"  {day}: skipped after 3 attempts ({msg[:80]})", flush=True)
                else:
                    import time as _t
                    # 503/5xx = server throttling: wait much longer before retry
                    _t.sleep(120 * (attempt + 1) if "503" in msg or "SlowDown" in msg
                             else 15 * (attempt + 1))
    return done


# ---- Phase 3: OPRA trades + option snapshots ----------------------------------
OPRA_TABLE = "massive_opra_daily"       # per-underlying-per-day aggregates
SNAPSHOT_TABLE = "massive_opt_snapshot"  # per-contract EOD snapshot subset


def process_opra_day(day: str, keep_files: bool = False) -> pd.DataFrame:
    """One day's OPRA trades pass (files are small, ~70-90MB gz): parse the OCC
    ticker for expiry/type/strike, aggregate per underlying:
      volume, premium, 0DTE share, DTE buckets, small-lot (<10) premium &
      call/put split (§5.2 proxy). Raw deleted after (§2)."""
    import duckdb

    from .. import store
    scratch = os.environ.get("TAPE_SCRATCH") or os.path.join(
        BASE, "data", "dashboard", "_tape_scratch")
    f = flatfile_download("us_options_opra/trades_v1", day, scratch)
    try:
        con = duckdb.connect()
        q = f"""
        WITH t AS (
            SELECT ticker, price, size, sip_timestamp,
                   regexp_extract(ticker, '^O:([A-Z0-9]+?)(\\d{{6}})([CP])(\\d{{8}})$', 1) AS und,
                   regexp_extract(ticker, '^O:([A-Z0-9]+?)(\\d{{6}})([CP])(\\d{{8}})$', 2) AS expiry_raw,
                   regexp_extract(ticker, '^O:([A-Z0-9]+?)(\\d{{6}})([CP])(\\d{{8}})$', 3) AS cp,
                   price * size * 100.0 AS premium
            FROM '{f}'
        ),
        e AS (
            SELECT *, strptime('20' || expiry_raw, '%Y%m%d')::DATE AS expiry,
                   DATE '{day}' AS td
            FROM t WHERE und <> ''
        ),
        d AS (
            SELECT *, datediff('day', td, expiry) AS dte FROM e
        )
        SELECT und AS underlying,
               SUM(size)                                        AS contracts,
               SUM(premium)                                     AS premium,
               SUM(CASE WHEN dte = 0 THEN size END)             AS c_0dte,
               SUM(CASE WHEN dte BETWEEN 1 AND 5 THEN size END) AS c_1_5,
               SUM(CASE WHEN dte BETWEEN 6 AND 30 THEN size END) AS c_6_30,
               SUM(CASE WHEN dte > 30 THEN size END)            AS c_over30,
               SUM(CASE WHEN size < 10 THEN premium END)        AS smalllot_prem,
               SUM(CASE WHEN size < 10 AND cp='C' THEN premium END) AS smalllot_call_prem,
               SUM(CASE WHEN size < 10 THEN size END)           AS smalllot_contracts
        FROM d GROUP BY und ORDER BY premium DESC
        """
        agg = con.execute(q).df()
        con.close()
        agg.insert(0, "date", day)
        tdir = os.path.join(store.LAKE_DIR, OPRA_TABLE)
        os.makedirs(tdir, exist_ok=True)
        agg.to_parquet(os.path.join(tdir, f"{day}.parquet"), index=False)
        return agg
    finally:
        if not keep_files:
            try:
                os.remove(f)
            except OSError:
                pass


def backfill_opra(start: str, max_days: int = 60, end: str | None = None) -> list[str]:
    """Resumable OPRA-trades backfill (files ~80MB/day — fast). Feeds RF7/RF8,
    LV2/LV3 history. Newest-first; holidays marked; transient errors retried."""
    from .. import store
    tdir = os.path.join(store.LAKE_DIR, OPRA_TABLE)
    os.makedirs(tdir, exist_ok=True)
    have = {f.split(".")[0] for f in os.listdir(tdir) if f.endswith(".parquet")}
    end_d = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
    days = [d for d in pd.bdate_range(date.fromisoformat(start), end_d)
            .strftime("%Y-%m-%d") if d not in have]
    done = []
    for day in sorted(days, reverse=True)[:max_days]:
        for attempt in range(3):
            try:
                agg = process_opra_day(day)
                done.append(day)
                store.log_run("massive:opra", "ok", f"{day}: {len(agg)} underlyings")
                break
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "404" in msg or "NoSuchKey" in msg or "Not Found" in msg:
                    pd.DataFrame([{"date": day, "underlying": "_HOLIDAY_"}]).to_parquet(
                        os.path.join(tdir, f"{day}.parquet"), index=False)
                    break
                store.log_run("massive:opra", "retry" if attempt < 2 else "skip",
                              f"{day} attempt {attempt + 1}: {msg[:80]}")
                if attempt == 2:
                    print(f"  {day}: skipped ({msg[:60]})", flush=True)
                else:
                    import time as _t
                    _t.sleep(10)
    return done


def read_opra_daily() -> pd.DataFrame | None:
    tdir = os.path.join(BASE, "data", "dashboard", OPRA_TABLE)
    if not os.path.isdir(tdir):
        return None
    files = sorted(x for x in os.listdir(tdir) if x.endswith(".parquet"))
    if not files:
        return None
    return pd.concat([pd.read_parquet(os.path.join(tdir, x)) for x in files],
                     ignore_index=True)


def option_snapshot(underlying: str) -> pd.DataFrame:
    """Full EOD option-chain snapshot for one underlying via REST (paginated):
    per contract [ticker, cp, strike, expiry, iv, delta, gamma, oi, day_volume,
    day_close, underlying_price]. Massive computes IV/greeks server-side."""
    s = _net.session()
    rows, url = [], f"{REST}/v3/snapshot/options/{underlying}?limit=250&apiKey={_key()}"
    while url:
        r = s.get(url, timeout=60)
        r.raise_for_status()
        js = r.json()
        for x in js.get("results", []):
            det, day, g = x.get("details", {}), x.get("day", {}), x.get("greeks", {})
            rows.append({
                "ticker": det.get("ticker"), "cp": det.get("contract_type"),
                "strike": det.get("strike_price"), "expiry": det.get("expiration_date"),
                "iv": x.get("implied_volatility"),
                "delta": g.get("delta"), "gamma": g.get("gamma"),
                "oi": x.get("open_interest"), "day_volume": day.get("volume"),
                "day_close": day.get("close"),
                "und_price": (x.get("underlying_asset") or {}).get("price"),
            })
        url = js.get("next_url")
        if url:
            url += f"&apiKey={_key()}"
    df = pd.DataFrame(rows)
    df["underlying"] = underlying
    return df


def pull_snapshots(underlyings: list[str]) -> pd.DataFrame:
    """Snapshot a list of underlyings, land in the lake (one file per day)."""
    from .. import store
    frames = []
    for u in underlyings:
        try:
            frames.append(option_snapshot(u))
        except Exception as e:  # noqa: BLE001
            store.log_run("massive:snapshot", "fail", f"{u}: {str(e)[:60]}")
    if not frames:
        raise RuntimeError("no snapshots retrieved")
    df = pd.concat(frames, ignore_index=True)
    df.insert(0, "date", date.today().isoformat())
    tdir = os.path.join(store.LAKE_DIR, SNAPSHOT_TABLE)
    os.makedirs(tdir, exist_ok=True)
    df.to_parquet(os.path.join(tdir, f"{date.today().isoformat()}.parquet"), index=False)
    return df


def read_snapshots() -> pd.DataFrame | None:
    tdir = os.path.join(BASE, "data", "dashboard", SNAPSHOT_TABLE)
    if not os.path.isdir(tdir):
        return None
    files = sorted(x for x in os.listdir(tdir) if x.endswith(".parquet"))
    if not files:
        return None
    return pd.concat([pd.read_parquet(os.path.join(tdir, x)) for x in files],
                     ignore_index=True)


def read_retail_daily() -> pd.DataFrame | None:
    tdir = os.path.join(BASE, "data", "dashboard", RETAIL_TABLE)
    if not os.path.isdir(tdir):
        return None
    files = sorted(f for f in os.listdir(tdir) if f.endswith(".parquet"))
    if not files:
        return None
    df = pd.concat([pd.read_parquet(os.path.join(tdir, f)) for f in files],
                   ignore_index=True)
    return df[(df["ticker"] != "_HOLIDAY_") & (~df["ticker"].isin(TEST_SYMBOLS))]
