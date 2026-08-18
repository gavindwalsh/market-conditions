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
             0 < sub5 < 20 or 30 < sub5 < 50. Regime table is per-symbol input.
  size cap : per-print notional ceiling on IDENTIFICATION (max_print_usd).
             BJZZ has no size filter — it assumes institutions are a small
             share of off-exchange subpenny prints. Battalio-Jennings-Saglam-Wu
             show that assumption fails: known institutional prints ARE
             identified as retail. BJZZ's only institutional guard is the
             0.4-0.6c exclusion band, which sheds ATS midpoint crosses but not
             VWAP/benchmark/negotiated prints struck AWAY from the mid.
             Found 2026-07-31: on 2026-07-30 that leak put SPY at -$11.6B and
             QQQ at -$8.9B identified net on 216- and 133-share average prints
             (VOO, same index but retail-held, averages 14 shares) — 64% and
             58% of each ETF's ENTIRE consolidated volume once x3-scaled, which
             is arithmetically impossible for genuine retail net flow. The cap
             applies to `cand` only, so the tape_* aggregates stay whole-market.

Engine: DuckDB out-of-core — reads trades+quotes files (csv.gz or parquet)
directly, streams the ASOF join, returns per-symbol daily aggregates. Raw tape
is never persisted (§2); the caller deletes the downloaded files.

Known properties (§5.1, rendered in panel footnotes): captures ~⅓ of retail
trades; levels are floors, not totals; trends are the product.
"""
from __future__ import annotations

import pandas as pd

# Expected input columns (Massive flat-file names):
#  trades: sip_timestamp, ticker, price, size, exchange, conditions
#  quotes: sip_timestamp, ticker, bid_price, ask_price
TRF_EXCHANGE = 4  # FINRA TRF/ADF exchange id on the Massive/Polygon feed


def classify_day(trades_src: str, quotes_src: str | None = None,
                 half_penny_symbols: set[str] | None = None,
                 max_print_usd: float | None = None) -> pd.DataFrame:
    """Run the classifier over one day's tape.

    trades_src / quotes_src: paths DuckDB can read (parquet or csv[.gz]).
    quotes_src=None → trades-only mode: identification runs in full (it needs
      no quotes); midpoint-signed columns come back 0 and `signing` = 'none'.
      BJZZ position-signed columns (retail_*_bjzz) are ALWAYS computed from the
      subpenny position alone — the §5.1-forbidden fallback, emitted under its
      own explicit label so a rendering decision (not this engine) controls
      whether it is ever shown. Discovered necessary 2026-07-08: the Massive
      Stocks tier carries trades but no quotes entitlement.
    half_penny_symbols: symbols on the half-penny tick regime (§5.1).
    max_print_usd: per-print notional ceiling for retail IDENTIFICATION (see the
      module docstring). None disables it, reproducing the pre-cap behaviour.
      Every retail_* column is affected; every tape_* column is NOT — the cap
      lands in `cand`, while the tape CTE reads unfiltered `trades`.

    Returns per-symbol aggregates:
      [ticker, retail_buy_usd, retail_sell_usd, retail_net_usd, retail_buy_sh,
       retail_sell_sh, retail_trades, retail_usd, retail_ident_usd,
       retail_ident_trades, retail_net_usd_bjzz, signing,
       tape_usd, tape_volume, tape_trades, offexch_volume, oddlot_trades,
       moc_volume]
    """
    import os
    import tempfile

    import duckdb
    con = duckdb.connect()  # in-memory; spills to disk for the big ASOF sort
    # PER-PROCESS spill dir: two backfill lanes sharing one spill directory
    # collide on spill files and hard-crash DuckDB (found 2026-07-09 — solo
    # runs fine, concurrent dies minutes in with no Python traceback)
    import uuid
    spill = os.path.join(tempfile.gettempdir(),
                         f"duckdb_spill_{os.getpid()}_{uuid.uuid4().hex[:8]}")
    os.makedirs(spill, exist_ok=True)
    con.execute(f"SET temp_directory='{spill}'")
    con.execute("SET preserve_insertion_order=false")  # allows streaming aggregation
    # hard cap: two backfill lanes ran concurrent classifies at DuckDB's default
    # (~80% RAM each) and OOM-killed each other (2026-07-09). 10GB each keeps
    # two lanes + OS comfortable; DuckDB spills the excess to disk.
    con.execute("SET memory_limit='10GB'")
    con.execute("SET threads=4")
    hp = sorted(half_penny_symbols or set())
    con.execute("CREATE TEMP TABLE hp_syms(ticker VARCHAR)")
    if hp:
        con.executemany("INSERT INTO hp_syms VALUES (?)", [(t,) for t in hp])

    signing = "midpoint" if quotes_src else "none"
    # size cap: identification-side only. Guarded as a float literal so the
    # f-string can never carry caller text into the SQL.
    cap_expr = ("" if max_print_usd is None
                else f" AND t.price * t.size <= {float(max_print_usd)}")
    cols = {r[0] for r in con.execute(
        f"DESCRIBE SELECT * FROM '{trades_src}' LIMIT 0").fetchall()}
    has_conds = "conditions" in cols
    conds_expr = "conditions" if has_conds else "NULL AS conditions"
    moc_expr = ("""SUM(CASE WHEN (',' || CAST(conditions AS VARCHAR) || ',') LIKE '%,8,%'
                          OR (',' || CAST(conditions AS VARCHAR) || ',') LIKE '%,19,%'
                        THEN size END)""" if has_conds else "SUM(NULL)")
    if quotes_src:
        signed_cte = f"""
    quotes AS (
        SELECT ticker, sip_timestamp, bid_price, ask_price
        FROM '{quotes_src}'
        WHERE bid_price > 0 AND ask_price > bid_price
    ),
    signed AS (
        SELECT c.ticker, c.price, c.size, c.p_e4,
               CASE WHEN c.price > (q.bid_price + q.ask_price)/2 THEN 1
                    WHEN c.price < (q.bid_price + q.ask_price)/2 THEN -1
                    ELSE 0 END AS side
        FROM cand c
        ASOF JOIN quotes q
          ON c.ticker = q.ticker AND c.sip_timestamp >= q.sip_timestamp
        WHERE c.is_subpenny
    ),"""
    else:
        signed_cte = """
    signed AS (
        SELECT ticker, price, size, p_e4, 0 AS side
        FROM cand WHERE is_subpenny
    ),"""

    q = f"""
    WITH trades AS (
        SELECT ticker, sip_timestamp, price, size, exchange, {conds_expr},
               CAST(ROUND(price * 10000) AS BIGINT) AS p_e4
        FROM '{trades_src}'
    ),
    -- candidate retail prints: TRF, >= $1, subpenny per tick regime
    cand AS (
        SELECT t.*,
               CASE WHEN h.ticker IS NOT NULL
                    THEN (p_e4 % 50)  BETWEEN 1 AND 19 OR (p_e4 % 50)  BETWEEN 31 AND 49
                    ELSE (p_e4 % 100) BETWEEN 1 AND 39 OR (p_e4 % 100) BETWEEN 61 AND 99
               END AS is_subpenny
        FROM trades t LEFT JOIN hp_syms h USING (ticker)
        WHERE t.exchange = {TRF_EXCHANGE} AND t.price >= 1.0{cap_expr}
    ),
    {signed_cte}
    retail AS (
        SELECT ticker,
               SUM(CASE WHEN side=1  THEN price*size END)  AS retail_buy_usd,
               SUM(CASE WHEN side=-1 THEN price*size END)  AS retail_sell_usd,
               SUM(CASE WHEN side=1  THEN size END)        AS retail_buy_sh,
               SUM(CASE WHEN side=-1 THEN size END)        AS retail_sell_sh,
               COUNT(CASE WHEN side<>0 THEN 1 END)         AS retail_trades,
               -- identification-only totals (valid without any signing)
               SUM(price*size)                             AS retail_ident_usd,
               COUNT(*)                                    AS retail_ident_trades,
               -- BJZZ position signing (§5.1-FORBIDDEN as primary; labeled
               -- fallback only): sub-position < 0.4c => sell, > 0.6c => buy.
               SUM(CASE
                     WHEN (p_e4 % 100) BETWEEN 61 AND 99 THEN  price*size
                     WHEN (p_e4 % 100) BETWEEN 1  AND 39 THEN -price*size
                   END)                                    AS retail_net_usd_bjzz
        FROM signed GROUP BY ticker
    ),
    tape AS (
        SELECT ticker,
               SUM(price*size)                              AS tape_usd,
               SUM(size)                                    AS tape_volume,
               COUNT(*)                                     AS tape_trades,
               SUM(CASE WHEN exchange = {TRF_EXCHANGE} THEN size END) AS offexch_volume,
               COUNT(CASE WHEN size < 100 THEN 1 END)       AS oddlot_trades,
               -- OP8: closing-auction volume. SIP sale conditions 8 (Closing
               -- Prints) + 19 (Market Center Closing Trade); 15 (Official
               -- Close) is the zero-volume price print and is EXCLUDED.
               -- conditions is a quoted CSV string, e.g. "14,12,37,41".
               {moc_expr}                                   AS moc_volume
        FROM trades GROUP BY ticker
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
           COALESCE(moc_volume, 0)      AS moc_volume
    FROM tape LEFT JOIN retail USING (ticker)
    ORDER BY ticker
    """
    out = con.execute(q).df()
    con.close()
    import shutil
    shutil.rmtree(spill, ignore_errors=True)  # per-PID spill dirs don't accumulate
    return out
