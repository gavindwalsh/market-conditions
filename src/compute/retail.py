"""retail.py — the §5.1 retail-flow classifier engine (RF1–RF5 core).

Method (Barber-Huang-Jorion-Odean-Schwarz, JF 2024 — per spec §5.1):
  identify : off-exchange (TRF) prints with subpenny price improvement,
             price ≥ $1. Subpenny bands via INTEGER arithmetic on price×10⁴:
             sub = price_e4 % 100 (hundredths of a cent); retail iff
             0 < sub < 40 or 60 < sub < 100. Exact half/round penny excluded.
  sign     : quote-midpoint. Prevailing NBBO via ASOF join (latest quote at or
             before the trade's SIP timestamp, same symbol). Above mid → buy,
             below mid → sell, at mid → EXCLUDED (not signed). NOT the original
             BJZZ subpenny-position signing (28% error rate — spec forbids it).
  half-penny regime (§5.1): for symbols on the SEC half-penny tick, bands are
             recomputed on the 0.5¢ grid: sub5 = price_e4 % 50, retail iff
             0 < sub5 < 20 or 30 < sub5 < 50. The regime is DETECTED from the
             quotes file (a symbol quoting on the half-cent grid is on the
             half-penny tick), so the table maintains itself as the SEC tick
             reform phases more symbols in. Before 2026-08 this was a dead
             parameter — nothing ever passed it, so every symbol was scored on
             the penny grid regardless of its actual tick.

Two filters BJZZ does not have, both added 2026-08 after institutional flow was
found dominating the identified set (see config.RETAIL_MAX_PRINT_USD):
  size cap : per-print notional ceiling. BJZZ's only institutional guard is the
             0.4-0.6c exclusion band, which sheds ATS midpoint crosses but not
             VWAP/benchmark/negotiated prints struck AWAY from the mid.
  conditions: sale conditions marking a price that was not set by supply and
             demand at that moment (average-price, derivatively-priced, price
             variation, contingent). Institutional by construction, and their
             computed prices land on subpennies constantly.
Both are measured as well as applied — excl_size_* and excl_cond_* record what
each removed, and the bucket table lets either be re-cut later WITHOUT another
tape pull.

Engine: DuckDB out-of-core — reads trades+quotes files (csv.gz or parquet)
directly, streams the ASOF join, returns per-symbol daily aggregates. Raw tape
is never persisted (§2); the caller deletes the downloaded files.

Known properties (§5.1, rendered in panel footnotes): captures a fraction of
retail trades; levels are floors, not totals; trends are the product.
"""
from __future__ import annotations

import pandas as pd

# Expected input columns (Massive flat-file names):
#  trades: sip_timestamp, ticker, price, size, exchange, conditions
#  quotes: sip_timestamp, ticker, bid_price, ask_price
TRF_EXCHANGE = 4  # FINRA TRF/ADF exchange id on the Massive/Polygon feed

# Half-penny detection: a penny-tick symbol never quotes on the half-cent mark,
# so any material share of bids at p_e4 % 100 == 50 identifies the half-penny
# grid. 1% is well clear of stray bad prints without needing the share to be
# large (a half-penny symbol still spends much of the day on whole cents).
HALF_PENNY_QUOTE_FRAC = 0.01

# Per-print notional buckets. Chosen so any plausible size cap falls on a
# boundary — $50k, $100k, $200k, $500k and $1M are all recoverable by summing.
SIZE_BUCKETS = [
    (0.0, 1_000.0, "01_lt_1k"), (1_000.0, 2_500.0, "02_1k_2p5k"),
    (2_500.0, 5_000.0, "03_2p5k_5k"), (5_000.0, 10_000.0, "04_5k_10k"),
    (10_000.0, 25_000.0, "05_10k_25k"), (25_000.0, 50_000.0, "06_25k_50k"),
    (50_000.0, 100_000.0, "07_50k_100k"), (100_000.0, 200_000.0, "08_100k_200k"),
    (200_000.0, 500_000.0, "09_200k_500k"), (500_000.0, 1_000_000.0, "10_500k_1m"),
    (1_000_000.0, None, "11_gt_1m"),
]

# Quoted-spread buckets (dollars). BHJOS: midpoint signing is ~93% accurate on
# one-cent spreads and degrades to ~52% at ten cents, so storing this lets a
# later metric weight or drop the unreliable tail.
SPREAD_BUCKETS = [
    (0.0, 0.01, "1_le_1c"), (0.01, 0.02, "2_1c_2c"), (0.02, 0.05, "3_2c_5c"),
    (0.05, 0.10, "4_5c_10c"), (0.10, None, "5_gt_10c"),
]

# Sale conditions that mark a non-market-determined price, grouped for the
# bucket table. Codes are the Massive/Polygon unified stock condition ids.
COND_GROUPS = [
    (2, "avg_price"),          # Average Price Trade
    (10, "deriv_priced"),      # Derivatively Priced
    (21, "price_variation"),   # Price Variation Trade
    (52, "contingent"),        # Contingent Trade
    (53, "contingent_qct"),    # Qualified Contingent Trade
]


def _bucket_case(col: str, buckets) -> str:
    """CASE expression mapping a numeric column onto bucket labels."""
    parts = []
    for lo, hi, label in buckets:
        if hi is None:
            parts.append(f"WHEN {col} >= {lo} THEN '{label}'")
        else:
            parts.append(f"WHEN {col} >= {lo} AND {col} < {hi} THEN '{label}'")
    return "CASE " + " ".join(parts) + " END"


def _cond_match(code: int) -> str:
    """Match one sale-condition code inside the quoted CSV `conditions` string
    (e.g. "14,12,37,41"). int() guards the f-string against caller text."""
    return (f"(',' || CAST(conditions AS VARCHAR) || ',') LIKE '%,{int(code)},%'")


def classify_day(trades_src: str, quotes_src: str | None = None,
                 half_penny_symbols: set[str] | None = None,
                 max_print_usd: float | None = None,
                 exclude_conditions: set[int] | None = None,
                 session_open_ns: int | None = None,
                 session_close_ns: int | None = None,
                 with_buckets: bool = False,
                 spill_dir: str | None = None):
    """Run the classifier over one day's tape.

    trades_src / quotes_src: paths DuckDB can read (parquet or csv[.gz]).
      quotes_src=None → trades-only mode: identification runs in full (it needs
      no quotes); midpoint-signed columns come back 0 and `signing` = 'none'.
      BJZZ position-signed columns (retail_*_bjzz) are ALWAYS computed from the
      subpenny position alone — the §5.1-forbidden fallback, emitted under its
      own explicit label so a rendering decision (not this engine) controls
      whether it is ever shown. Discovered necessary 2026-07-08: the Massive
      Stocks tier carries trades but no quotes entitlement.
    half_penny_symbols: override the per-symbol half-penny tick regime (§5.1).
      None → DETECT it from quotes_src, which is what production does. Pass an
      explicit set only in tests, or to force the penny grid with set().
    max_print_usd: per-print notional ceiling for retail IDENTIFICATION.
      None disables it. Affects every retail_* column; NEVER a tape_* column.
    exclude_conditions: sale-condition ids removed from identification.
    session_open_ns / session_close_ns: regular-session bounds in epoch ns, used
      only for the open/close 30-minute buckets. None → those come back 0.
    with_buckets: also return the long-format bucket frame (see below).
    spill_dir: where DuckDB spills. Defaults to the trades file's directory —
      see the tmpfs note below; do NOT let this land on /tmp for a real day.

    Returns the per-symbol aggregate frame, or `(aggregate, buckets)` when
    with_buckets=True. Bucket rows are (ticker, dim, bucket, buy_usd, sell_usd,
    trades) for dim in {size, cond, spread} over the IDENTIFIED-eligible set
    BEFORE the size and condition filters, so either can be re-cut from the lake
    without re-pulling the tape.
    """
    import os
    import tempfile
    import uuid

    import duckdb
    con = duckdb.connect()  # in-memory; spills to disk for the big ASOF sort
    # PER-PROCESS spill dir: two backfill lanes sharing one spill directory
    # collide on spill files and hard-crash DuckDB (found 2026-07-09 — solo
    # runs fine, concurrent dies minutes in with no Python traceback)
    #
    # Spill NEXT TO THE TRADES FILE, not in gettempdir(). On Amazon Linux 2023
    # (the backfill box) /tmp is a TMPFS — RAM — so "spilling to disk" spilled
    # into memory, and DuckDB died with
    #   Out of Memory Error: failed to offload data block ... (6.5 GiB/6.5)
    # while ~294 GB of real disk sat unused. Found 2026-08-18, an hour into a
    # 156-day reprocess that had completed ZERO days. Latent since the engine
    # was written; only surfaced once the signed set was materialised, which
    # needs far more spill than the old fully-streaming query. The trades file
    # is already multi-GB, so its directory is guaranteed to be real storage.
    spill_root = (spill_dir or os.path.dirname(os.path.abspath(trades_src))
                  or tempfile.gettempdir())
    spill = os.path.join(spill_root,
                         f"duckdb_spill_{os.getpid()}_{uuid.uuid4().hex[:8]}")
    os.makedirs(spill, exist_ok=True)
    con.execute(f"SET temp_directory='{spill}'")
    # ...and let DuckDB actually use it. The default cap is derived from the
    # temp volume, which is what bound us at 6.5 GiB when it was tmpfs.
    con.execute("SET max_temp_directory_size='200GB'")
    con.execute("SET preserve_insertion_order=false")  # allows streaming aggregation
    # hard cap: two backfill lanes ran concurrent classifies at DuckDB's default
    # (~80% RAM each) and OOM-killed each other (2026-07-09). DuckDB spills the
    # excess to disk (to the real disk, see the tmpfs note above). The lane
    # runner lowers this as lane count rises — 4 lanes at 10GB would promise
    # 40GB on a 30GB box.
    _mem = float(os.environ.get("MCD_DUCKDB_MEM_GB", "10"))
    con.execute(f"SET memory_limit='{_mem}GB'")
    con.execute("SET threads=4")

    signing = "midpoint" if quotes_src else "none"
    cols = {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM '{trades_src}' LIMIT 0").fetchall()}
    has_conds = "conditions" in cols
    conds_expr = "conditions" if has_conds else "NULL AS conditions"

    # ---- half-penny regime -------------------------------------------------
    # Detected from the quotes file unless the caller supplied a set. Symbols
    # under $1 are excluded from detection — subpenny quoting is legal there
    # under Rule 612 and would produce false positives.
    con.execute("CREATE TEMP TABLE hp_syms(ticker VARCHAR)")
    if half_penny_symbols is not None:
        for t in sorted(half_penny_symbols):
            con.execute("INSERT INTO hp_syms VALUES (?)", [t])
    elif quotes_src:
        con.execute(f"""
            INSERT INTO hp_syms
            SELECT ticker FROM (
                SELECT ticker,
                       SUM(CASE WHEN (CAST(ROUND(bid_price*10000) AS BIGINT) % 100) = 50
                                THEN 1 ELSE 0 END)::DOUBLE / COUNT(*) AS frac
                FROM '{quotes_src}'
                WHERE bid_price >= 1.0
                GROUP BY ticker
            ) WHERE frac > {HALF_PENNY_QUOTE_FRAC}
        """)

    # ---- filter expressions ------------------------------------------------
    excl = sorted(int(c) for c in (exclude_conditions or set()))
    cond_excl_expr = (" OR ".join(_cond_match(c) for c in excl)
                      if (has_conds and excl) else "FALSE")
    size_excl_expr = ("FALSE" if max_print_usd is None
                      else f"notional > {float(max_print_usd)}")
    # Collapse conditions to a short group label INSIDE the candidate scan, so
    # `sgn` never carries the raw CSV string for ~18M rows.
    cond_group_expr = ("CASE " + " ".join(
        f"WHEN {_cond_match(code)} THEN '{label}'" for code, label in COND_GROUPS
    ) + " ELSE 'standard' END") if has_conds else "'standard'"
    moc_expr = (f"SUM(CASE WHEN {_cond_match(8)} OR {_cond_match(19)} THEN size END)"
                if has_conds else "SUM(NULL)")
    o_lo = "NULL" if session_open_ns is None else str(int(session_open_ns))
    o_hi = ("NULL" if session_open_ns is None
            else str(int(session_open_ns) + 30 * 60 * 1_000_000_000))
    c_lo = ("NULL" if session_close_ns is None
            else str(int(session_close_ns) - 30 * 60 * 1_000_000_000))
    c_hi = "NULL" if session_close_ns is None else str(int(session_close_ns))

    # ---- signed set, materialised once -------------------------------------
    # Every subpenny TRF candidate, with its prevailing quote, written ONCE to a
    # parquet file on the spill volume. Both the aggregate and the bucket cut
    # then read it, so the expensive ASOF join runs once. Deliberately a FILE
    # and not a TEMP TABLE: a temp table lives in DuckDB's buffer pool and has
    # to spill under memory pressure, which is what blew up on the box; a
    # parquet file streams in and out with a flat memory profile.
    # LEFT so prints with no prevailing quote survive and can be counted.
    sgn_path = os.path.join(spill, "sgn.parquet").replace("\\", "/")
    if quotes_src:
        sgn_sql = f"""
        COPY (
        WITH trades AS (
            SELECT ticker, sip_timestamp, price, size, {conds_expr},
                   price*size AS notional,
                   CAST(ROUND(price*10000) AS BIGINT) AS p_e4
            FROM '{trades_src}'
            WHERE exchange = {TRF_EXCHANGE} AND price >= 1.0
        ),
        cand AS (
            SELECT t.*, (h.ticker IS NOT NULL) AS half_penny,
                   {size_excl_expr} AS excl_size,
                   ({cond_excl_expr}) AS excl_cond,
                   {cond_group_expr} AS cond_group
            FROM trades t LEFT JOIN hp_syms h USING (ticker)
            WHERE CASE WHEN h.ticker IS NOT NULL
                       THEN (p_e4 % 50)  BETWEEN 1 AND 19 OR (p_e4 % 50)  BETWEEN 31 AND 49
                       ELSE (p_e4 % 100) BETWEEN 1 AND 39 OR (p_e4 % 100) BETWEEN 61 AND 99
                  END
        ),
        quotes AS (
            SELECT ticker, sip_timestamp, bid_price, ask_price
            FROM '{quotes_src}'
            WHERE bid_price > 0 AND ask_price > bid_price
        )
        SELECT c.ticker, c.sip_timestamp, c.price, c.size, c.notional, c.p_e4,
               c.half_penny, c.excl_size, c.excl_cond, c.cond_group,
               q.bid_price, q.ask_price,
               (q.bid_price + q.ask_price)/2 AS mid,
               (q.ask_price - q.bid_price)   AS spread,
               CASE WHEN q.bid_price IS NULL THEN NULL
                    WHEN c.price > (q.bid_price + q.ask_price)/2 THEN 1
                    WHEN c.price < (q.bid_price + q.ask_price)/2 THEN -1
                    ELSE 0 END AS side
        FROM cand c
        ASOF LEFT JOIN quotes q
          ON c.ticker = q.ticker AND c.sip_timestamp >= q.sip_timestamp
        ) TO '{sgn_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    else:
        sgn_sql = f"""
        COPY (
        WITH trades AS (
            SELECT ticker, sip_timestamp, price, size, {conds_expr},
                   price*size AS notional,
                   CAST(ROUND(price*10000) AS BIGINT) AS p_e4
            FROM '{trades_src}'
            WHERE exchange = {TRF_EXCHANGE} AND price >= 1.0
        )
        SELECT t.ticker, t.sip_timestamp, t.price, t.size, t.notional, t.p_e4,
               (h.ticker IS NOT NULL) AS half_penny,
               {size_excl_expr} AS excl_size,
               ({cond_excl_expr}) AS excl_cond,
               {cond_group_expr} AS cond_group,
               CAST(NULL AS DOUBLE) AS bid_price, CAST(NULL AS DOUBLE) AS ask_price,
               CAST(NULL AS DOUBLE) AS mid, CAST(NULL AS DOUBLE) AS spread,
               0 AS side
        FROM trades t LEFT JOIN hp_syms h USING (ticker)
        WHERE CASE WHEN h.ticker IS NOT NULL
                   THEN (p_e4 % 50)  BETWEEN 1 AND 19 OR (p_e4 % 50)  BETWEEN 31 AND 49
                   ELSE (p_e4 % 100) BETWEEN 1 AND 39 OR (p_e4 % 100) BETWEEN 61 AND 99
              END
        ) TO '{sgn_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    con.execute(sgn_sql)

    # `keep` = survives BOTH new filters; this is the identified retail set.
    KEEP = "NOT excl_size AND NOT excl_cond"
    OPEN30 = ("FALSE" if session_open_ns is None
              else f"sip_timestamp >= {o_lo} AND sip_timestamp < {o_hi}")
    CLOSE30 = ("FALSE" if session_close_ns is None
               else f"sip_timestamp >= {c_lo} AND sip_timestamp <= {c_hi}")

    agg = con.execute(f"""
    WITH retail AS (
        SELECT ticker,
               ANY_VALUE(half_penny)                                  AS half_penny,
               SUM(CASE WHEN {KEEP} AND side=1  THEN notional END)    AS retail_buy_usd,
               SUM(CASE WHEN {KEEP} AND side=-1 THEN notional END)    AS retail_sell_usd,
               SUM(CASE WHEN {KEEP} AND side=1  THEN size END)        AS retail_buy_sh,
               SUM(CASE WHEN {KEEP} AND side=-1 THEN size END)        AS retail_sell_sh,
               COUNT(CASE WHEN {KEEP} AND side IN (1,-1) THEN 1 END)  AS retail_trades,
               -- identification-only totals (valid without any signing)
               SUM(CASE WHEN {KEEP} THEN notional END)                AS retail_ident_usd,
               COUNT(CASE WHEN {KEEP} THEN 1 END)                     AS retail_ident_trades,
               -- BJZZ position signing (§5.1-FORBIDDEN as primary; labeled
               -- fallback only): sub-position < 0.4c => sell, > 0.6c => buy.
               SUM(CASE WHEN {KEEP} AND (p_e4 % 100) BETWEEN 61 AND 99 THEN  notional
                        WHEN {KEEP} AND (p_e4 % 100) BETWEEN 1  AND 39 THEN -notional
                   END)                                               AS retail_net_usd_bjzz,
               -- what each new filter removed (measured, not just applied)
               SUM(CASE WHEN excl_size THEN notional END)             AS excl_size_usd,
               COUNT(CASE WHEN excl_size THEN 1 END)                  AS excl_size_trades,
               SUM(CASE WHEN excl_cond THEN notional END)             AS excl_cond_usd,
               COUNT(CASE WHEN excl_cond THEN 1 END)                  AS excl_cond_trades,
               -- what the signing step discards
               SUM(CASE WHEN {KEEP} AND side=0 THEN notional END)     AS atmid_usd,
               COUNT(CASE WHEN {KEEP} AND side=0 THEN 1 END)          AS atmid_trades,
               SUM(CASE WHEN {KEEP} AND side IS NULL THEN notional END) AS noquote_usd,
               COUNT(CASE WHEN {KEEP} AND side IS NULL THEN 1 END)    AS noquote_trades,
               -- execution quality (notional-weighted; divide by retail_usd)
               SUM(CASE WHEN {KEEP} AND spread IS NOT NULL
                        THEN spread*size END)                         AS spread_w_usd,
               SUM(CASE WHEN {KEEP} AND side=1  THEN (ask_price-price)*size
                        WHEN {KEEP} AND side=-1 THEN (price-bid_price)*size END)
                                                                      AS pi_usd,
               SUM(CASE WHEN {KEEP} AND {OPEN30}  THEN notional END)  AS open30_usd,
               SUM(CASE WHEN {KEEP} AND {CLOSE30} THEN notional END)  AS close30_usd
        FROM '{sgn_path}' GROUP BY ticker
    ),
    tape AS (
        SELECT ticker,
               SUM(price*size)                              AS tape_usd,
               SUM(size)                                    AS tape_volume,
               COUNT(*)                                     AS tape_trades,
               SUM(CASE WHEN exchange = {TRF_EXCHANGE} THEN size END) AS offexch_volume,
               COUNT(CASE WHEN size < 100 THEN 1 END)       AS oddlot_trades,
               SUM(CASE WHEN size < 100 THEN price*size END) AS oddlot_usd,
               -- OP8: closing-auction volume. SIP sale conditions 8 (Closing
               -- Prints) + 19 (Market Center Closing Trade); 15 (Official
               -- Close) is the zero-volume price print and is EXCLUDED.
               -- conditions is a quoted CSV string, e.g. "14,12,37,41".
               {moc_expr}                                   AS moc_volume
        FROM (SELECT ticker, price, size, exchange, {conds_expr}
              FROM '{trades_src}') GROUP BY ticker
    )
    SELECT tape.ticker,
           COALESCE(retail_buy_usd, 0)  AS retail_buy_usd,
           COALESCE(retail_sell_usd, 0) AS retail_sell_usd,
           COALESCE(retail_buy_usd, 0) - COALESCE(retail_sell_usd, 0) AS retail_net_usd,
           COALESCE(retail_buy_sh, 0)   AS retail_buy_sh,
           COALESCE(retail_sell_sh, 0)  AS retail_sell_sh,
           COALESCE(retail_trades, 0)   AS retail_trades,
           COALESCE(retail_buy_usd, 0) + COALESCE(retail_sell_usd, 0) AS retail_usd,
           COALESCE(retail_ident_usd, 0)    AS retail_ident_usd,
           COALESCE(retail_ident_trades, 0) AS retail_ident_trades,
           COALESCE(retail_net_usd_bjzz, 0) AS retail_net_usd_bjzz,
           '{signing}' AS signing,
           tape_usd, tape_volume, tape_trades,
           COALESCE(offexch_volume, 0)  AS offexch_volume,
           oddlot_trades,
           COALESCE(moc_volume, 0)      AS moc_volume,
           -- added 2026-08 (method_version 2)
           COALESCE(half_penny, FALSE)  AS half_penny,
           COALESCE(excl_size_usd, 0)   AS excl_size_usd,
           COALESCE(excl_size_trades, 0) AS excl_size_trades,
           COALESCE(excl_cond_usd, 0)   AS excl_cond_usd,
           COALESCE(excl_cond_trades, 0) AS excl_cond_trades,
           COALESCE(atmid_usd, 0)       AS atmid_usd,
           COALESCE(atmid_trades, 0)    AS atmid_trades,
           COALESCE(noquote_usd, 0)     AS noquote_usd,
           COALESCE(noquote_trades, 0)  AS noquote_trades,
           COALESCE(oddlot_usd, 0)      AS oddlot_usd,
           COALESCE(spread_w_usd, 0)    AS spread_w_usd,
           COALESCE(pi_usd, 0)          AS pi_usd,
           COALESCE(open30_usd, 0)      AS open30_usd,
           COALESCE(close30_usd, 0)     AS close30_usd
    FROM tape LEFT JOIN retail USING (ticker)
    ORDER BY ticker
    """).df()

    buckets = None
    if with_buckets:
        # Cut over the pre-filter eligible set on purpose: that is what makes a
        # different cap or condition list recoverable without another tape pull.
        size_case = _bucket_case("notional", SIZE_BUCKETS)
        spread_case = _bucket_case("spread", SPREAD_BUCKETS)
        buckets = con.execute(f"""
        SELECT ticker, dim, bucket,
               SUM(CASE WHEN side=1  THEN notional END) AS buy_usd,
               SUM(CASE WHEN side=-1 THEN notional END) AS sell_usd,
               COUNT(*)                                 AS trades
        FROM (
            SELECT ticker, side, notional, 'size'   AS dim, {size_case}   AS bucket FROM '{sgn_path}'
            UNION ALL
            SELECT ticker, side, notional, 'cond'   AS dim, cond_group    AS bucket FROM '{sgn_path}'
            UNION ALL
            SELECT ticker, side, notional, 'spread' AS dim, {spread_case} AS bucket FROM '{sgn_path}'
        )
        WHERE bucket IS NOT NULL
        GROUP BY ticker, dim, bucket
        ORDER BY ticker, dim, bucket
        """).df()
        buckets["buy_usd"] = buckets["buy_usd"].fillna(0.0)
        buckets["sell_usd"] = buckets["sell_usd"].fillna(0.0)

    con.close()
    import shutil
    shutil.rmtree(spill, ignore_errors=True)  # per-PID spill dirs don't accumulate
    return (agg, buckets) if with_buckets else agg
