"""§7.6: classifier on synthetic prints — known subpenny/midpoint cases.
Every §5.1 rule gets a print that isolates it. Run: python tests/test_retail_classifier.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src import config  # noqa: E402
from src.compute.retail import classify_day  # noqa: E402

NS = 1_000_000_000  # 1s in ns


def _write(df, path):
    df.to_parquet(path, index=False)
    return path


def _run(trades, quotes, hp=None):
    d = tempfile.mkdtemp()
    t = _write(pd.DataFrame(trades), os.path.join(d, "t.parquet"))
    q = _write(pd.DataFrame(quotes), os.path.join(d, "q.parquet"))
    return classify_day(t, q, half_penny_symbols=hp)


def test_signing_and_bands():
    quotes = [
        # ABC NBBO: 10.00 x 10.02 → mid 10.01, from t=1s
        dict(ticker="ABC", sip_timestamp=1 * NS, bid_price=10.00, ask_price=10.02),
    ]
    trades = [
        # 1) TRF subpenny ABOVE mid (10.0180, sub=80∈(60,100)) → retail BUY
        dict(ticker="ABC", sip_timestamp=2 * NS, price=10.0180, size=100, exchange=4),
        # 2) TRF subpenny BELOW mid (10.0020, sub=20∈(0,40)) → retail SELL
        dict(ticker="ABC", sip_timestamp=3 * NS, price=10.0020, size=50, exchange=4),
        # 3) TRF subpenny AT mid (10.0100 → sub=0... need at-mid subpenny: mid
        #    10.01 is a round penny, so use quote 10.00x10.03 → skip; instead
        #    exact-half-penny print 10.0050 → EXCLUDED by band rule
        dict(ticker="ABC", sip_timestamp=4 * NS, price=10.0050, size=75, exchange=4),
        # 4) TRF round-penny (10.02) → NOT retail
        dict(ticker="ABC", sip_timestamp=5 * NS, price=10.02, size=200, exchange=4),
        # 5) EXCHANGE subpenny (midpoint print on exchange 11) → NOT retail (not TRF)
        dict(ticker="ABC", sip_timestamp=6 * NS, price=10.0180, size=300, exchange=11),
        # 6) sub-$1 TRF subpenny → excluded by price floor
        dict(ticker="PNY", sip_timestamp=7 * NS, price=0.9980, size=1000, exchange=4),
        # 7) trade BEFORE any quote (t=0.5s) → no prevailing NBBO → dropped by ASOF
        dict(ticker="ABC", sip_timestamp=int(0.5 * NS), price=10.0180, size=999, exchange=4),
    ]
    quotes.append(dict(ticker="PNY", sip_timestamp=1 * NS, bid_price=0.99, ask_price=1.00))
    r = _run(trades, quotes).set_index("ticker")
    abc = r.loc["ABC"]
    assert abs(abc["retail_buy_usd"] - 10.0180 * 100) < 1e-6, abc["retail_buy_usd"]   # only #1
    assert abs(abc["retail_sell_usd"] - 10.0020 * 50) < 1e-6                          # only #2
    assert abc["retail_trades"] == 2            # 3,4,5,7 all excluded
    assert abc["tape_trades"] == 6              # all ABC prints counted on the tape
    assert abc["offexch_volume"] == 100 + 50 + 75 + 200 + 999                        # TRF only
    assert r.loc["PNY"]["retail_trades"] == 0   # price floor
    print("PASS signing + bands + TRF filter + price floor + ASOF staleness")


def test_at_mid_excluded():
    # NBBO 10.00 x 10.0360 → mid 10.0180 (subpenny!). Trade AT mid → excluded.
    quotes = [dict(ticker="MID", sip_timestamp=1 * NS, bid_price=10.00, ask_price=10.0360)]
    trades = [dict(ticker="MID", sip_timestamp=2 * NS, price=10.0180, size=100, exchange=4)]
    r = _run(trades, quotes).set_index("ticker")
    assert r.loc["MID"]["retail_trades"] == 0
    assert r.loc["MID"]["retail_net_usd"] == 0
    print("PASS at-midpoint exclusion")


def test_half_penny_regime():
    # Symbol on the half-penny grid: 20.00 x 20.01, mid 20.005.
    quotes = [dict(ticker="HPX", sip_timestamp=1 * NS, bid_price=20.00, ask_price=20.01)]
    trades = [
        # 20.0060: on the FULL-penny grid sub=60 → boundary EXCLUDED (not <40, not >60...
        # 60 is not in (60,100) open interval → excluded). On the HALF-penny grid:
        # p_e4=200060, %50 = 10 ∈ (0,20) → retail. Above mid 20.005 → BUY.
        dict(ticker="HPX", sip_timestamp=2 * NS, price=20.0060, size=10, exchange=4),
        # 20.0050 exact half-penny → excluded under BOTH regimes
        dict(ticker="HPX", sip_timestamp=3 * NS, price=20.0050, size=10, exchange=4),
    ]
    # penny regime: neither print classifies
    r0 = _run(trades, quotes).set_index("ticker")
    assert r0.loc["HPX"]["retail_trades"] == 0
    # half-penny regime: first print classifies as buy
    r1 = _run(trades, quotes, hp={"HPX"}).set_index("ticker")
    assert r1.loc["HPX"]["retail_trades"] == 1
    assert abs(r1.loc["HPX"]["retail_buy_usd"] - 20.0060 * 10) < 1e-6
    print("PASS half-penny regime bands (§5.1)")


def test_quote_update_respected():
    # NBBO moves; the SECOND quote must prevail for the later trade.
    quotes = [
        dict(ticker="QQ", sip_timestamp=1 * NS, bid_price=10.00, ask_price=10.02),  # mid 10.01
        dict(ticker="QQ", sip_timestamp=5 * NS, bid_price=10.10, ask_price=10.12),  # mid 10.11
    ]
    # 10.1020 vs OLD mid 10.01 would be a buy; vs NEW mid 10.11 it's a SELL.
    trades = [dict(ticker="QQ", sip_timestamp=6 * NS, price=10.1020, size=100, exchange=4)]
    r = _run(trades, quotes).set_index("ticker")
    assert abs(r.loc["QQ"]["retail_sell_usd"] - 10.1020 * 100) < 1e-6
    assert r.loc["QQ"]["retail_buy_usd"] == 0
    print("PASS prevailing-quote update (ASOF picks latest at-or-before)")


def test_trades_only_mode():
    # No quotes: identification still counts; midpoint columns zero; BJZZ
    # position signing emitted under its own label; signing = 'none'.
    trades = [
        # sub=80 (>0.6c) → BJZZ BUY $1001.80
        dict(ticker="TO", sip_timestamp=2 * NS, price=10.0080, size=100, exchange=4, conditions="12,37"),
        # sub=20 (<0.4c) → BJZZ SELL $500.10
        dict(ticker="TO", sip_timestamp=3 * NS, price=10.0020, size=50, exchange=4, conditions="12,37"),
        # closing auction print (condition 8) — feeds moc_volume, not retail
        dict(ticker="TO", sip_timestamp=4 * NS, price=10.00, size=5000, exchange=11, conditions="8"),
    ]
    d = tempfile.mkdtemp()
    t = _write(pd.DataFrame(trades), os.path.join(d, "t.parquet"))
    r = classify_day(t, None).set_index("ticker")
    row = r.loc["TO"]
    assert row["signing"] == "none"
    assert row["retail_trades"] == 0 and row["retail_net_usd"] == 0   # no midpoint signing
    assert row["retail_ident_trades"] == 2                            # identification intact
    assert abs(row["retail_ident_usd"] - (10.0080 * 100 + 10.0020 * 50)) < 1e-6
    assert abs(row["retail_net_usd_bjzz"] - (10.0080 * 100 - 10.0020 * 50)) < 1e-6
    assert row["moc_volume"] == 5000                                  # OP8 conditions 8/19
    print("PASS trades-only mode (ident + labeled BJZZ + MOC)")


def test_size_cap_identification_only():
    """max_print_usd excludes oversized prints from IDENTIFICATION while leaving
    every tape_* aggregate whole-market. Guards the 2026-07-31 fix: institutional
    blocks printing subpenny off-exchange were being read as retail (SPY showed
    -$11.6B net on 216-share average prints on 2026-07-30)."""
    quotes = [dict(ticker="CAP", sip_timestamp=1 * NS, bid_price=10.00, ask_price=10.02)]
    trades = [
        # small subpenny SELL below mid 10.01 → $500.10, under any sane cap
        dict(ticker="CAP", sip_timestamp=2 * NS, price=10.0020, size=50, exchange=4),
        # block subpenny SELL, same signature → $1,000,200. Institutional.
        dict(ticker="CAP", sip_timestamp=3 * NS, price=10.0020, size=100_000, exchange=4),
    ]
    d = tempfile.mkdtemp()
    t = _write(pd.DataFrame(trades), os.path.join(d, "t.parquet"))
    q = _write(pd.DataFrame(quotes), os.path.join(d, "q.parquet"))
    un = classify_day(t, q).set_index("ticker").loc["CAP"]
    cap = classify_day(t, q, max_print_usd=50_000.0).set_index("ticker").loc["CAP"]

    # uncapped: the block dominates the "retail" total
    assert un["retail_trades"] == 2
    assert abs(un["retail_sell_usd"] - (10.0020 * 50 + 10.0020 * 100_000)) < 1e-6
    # capped: only the genuine small print survives identification
    assert cap["retail_trades"] == 1
    assert abs(cap["retail_sell_usd"] - 10.0020 * 50) < 1e-6
    assert abs(cap["retail_ident_usd"] - 10.0020 * 50) < 1e-6
    assert abs(cap["retail_net_usd_bjzz"] + 10.0020 * 50) < 1e-6   # fallback capped too
    # tape_* must be untouched — RF2/MH9 denominators stay whole-market
    for col in ("tape_usd", "tape_volume", "tape_trades", "offexch_volume",
                "oddlot_trades"):
        assert un[col] == cap[col], col
    assert cap["tape_volume"] == 50 + 100_000
    # None disables the cap entirely (pre-fix behaviour reproducible)
    assert classify_day(t, q, max_print_usd=None).set_index(
        "ticker").loc["CAP"]["retail_trades"] == 2
    print("PASS per-print size cap (identification only, tape_* preserved)")


def test_condition_filter_excludes_non_market_prices():
    """Sale conditions marking a price not set by supply and demand at that
    moment are institutional by construction, and their computed prices land on
    subpennies constantly — a size cap only catches the large ones."""
    quotes = [dict(ticker="CND", sip_timestamp=1 * NS, bid_price=10.00, ask_price=10.02)]
    trades = [
        # clean retail buy above mid
        dict(ticker="CND", sip_timestamp=2 * NS, price=10.0180, size=100, exchange=4,
             conditions="12"),
        # same signature, AVERAGE PRICE TRADE (2) — small, so the cap misses it
        dict(ticker="CND", sip_timestamp=3 * NS, price=10.0180, size=100, exchange=4,
             conditions="12,2"),
        # QUALIFIED CONTINGENT (53)
        dict(ticker="CND", sip_timestamp=4 * NS, price=10.0180, size=100, exchange=4,
             conditions="53"),
        # ODD LOT (37) must NOT be excluded — odd lots are strongly retail
        dict(ticker="CND", sip_timestamp=5 * NS, price=10.0180, size=5, exchange=4,
             conditions="37,12"),
        # DERIVATIVELY PRICED (10) must NOT be excluded — see the regression
        # guard below for why it was removed from the set
        dict(ticker="CND", sip_timestamp=6 * NS, price=10.0180, size=100, exchange=4,
             conditions="10"),
    ]
    d = tempfile.mkdtemp()
    t = _write(pd.DataFrame(trades), os.path.join(d, "t.parquet"))
    q = _write(pd.DataFrame(quotes), os.path.join(d, "q.parquet"))
    # the REAL production set, so this test fails if the set ever drifts
    r = classify_day(t, q, exclude_conditions=config.RETAIL_EXCLUDE_CONDITIONS
                     ).set_index("ticker").loc["CND"]
    assert r["retail_ident_trades"] == 3, r["retail_ident_trades"]   # clean + odd lot + deriv
    assert r["excl_cond_trades"] == 2                                # avg-price + QCT
    # no cap passed, so nothing excluded for size
    assert r["excl_size_trades"] == 0
    # and the tape still sees all five
    assert r["tape_trades"] == 5
    print("PASS sale-condition filter (odd lots kept, computed prices dropped)")


def test_derivatively_priced_is_not_excluded():
    """Regression guard for the 2026-08-18 correction.

    Condition 10 was briefly in the exclusion set. On real tape it fired on
    3,140,052 prints — 17.4% of the eligible set — averaging $1,391, SMALLER
    than the $4,128 standard print. It was stripping the most retail-looking
    flow on the tape, the opposite of what a size-driven filter is for, and
    moved 2026-07-30 breadth ten points on its own. If it reappears, so has
    the regression."""
    assert 10 not in config.RETAIL_EXCLUDE_CONDITIONS, (
        "condition 10 (Derivatively Priced) excludes retail-SIZED prints — see "
        "config.RETAIL_EXCLUDE_CONDITIONS for the measured evidence")
    assert config.RETAIL_EXCLUDE_CONDITIONS == {2, 21, 52, 53}
    print("PASS condition 10 stays IN the retail set")


def test_half_penny_regime_detected_from_quotes():
    """The regime was specified but never wired — nothing passed
    half_penny_symbols, so every symbol was scored on the penny grid. It is now
    detected from the quotes file. p_e4=200060 is retail on the half-penny grid
    (%50=10, in 1-19) but NOT on the penny grid (%100=60, outside 1-39/61-99),
    so this print is identified only if detection works."""
    quotes = [
        # quotes on the half-cent marks -> half-penny tick
        dict(ticker="HALF", sip_timestamp=1 * NS, bid_price=20.005, ask_price=20.015),
        # ordinary penny-tick control
        dict(ticker="PENNY", sip_timestamp=1 * NS, bid_price=20.00, ask_price=20.02),
    ]
    trades = [
        dict(ticker="HALF", sip_timestamp=2 * NS, price=20.0060, size=100, exchange=4),
        dict(ticker="PENNY", sip_timestamp=2 * NS, price=20.0060, size=100, exchange=4),
    ]
    d = tempfile.mkdtemp()
    t = _write(pd.DataFrame(trades), os.path.join(d, "t.parquet"))
    q = _write(pd.DataFrame(quotes), os.path.join(d, "q.parquet"))
    r = classify_day(t, q).set_index("ticker")   # half_penny_symbols=None -> detect
    assert bool(r.loc["HALF"]["half_penny"]) is True
    assert bool(r.loc["PENNY"]["half_penny"]) is False
    assert r.loc["HALF"]["retail_ident_trades"] == 1     # reachable only on the 0.5c grid
    assert r.loc["PENNY"]["retail_ident_trades"] == 0    # correctly rejected on the penny grid
    print("PASS half-penny regime detected from quotes")


def test_buckets_reconcile_to_eligible_set():
    """The bucket table is what makes a future cap/condition change arithmetic
    on the lake instead of another tape pull, so every dimension must cover the
    SAME pre-filter eligible set."""
    quotes = [dict(ticker="BKT", sip_timestamp=1 * NS, bid_price=10.00, ask_price=10.02)]
    trades = [
        dict(ticker="BKT", sip_timestamp=2 * NS, price=10.0180, size=50, exchange=4,
             conditions="12"),
        dict(ticker="BKT", sip_timestamp=3 * NS, price=10.0020, size=100000, exchange=4,
             conditions="12"),
        dict(ticker="BKT", sip_timestamp=4 * NS, price=10.0180, size=200, exchange=4,
             conditions="2"),
    ]
    d = tempfile.mkdtemp()
    t = _write(pd.DataFrame(trades), os.path.join(d, "t.parquet"))
    q = _write(pd.DataFrame(quotes), os.path.join(d, "q.parquet"))
    agg, bk = classify_day(t, q, max_print_usd=200_000.0,
                           exclude_conditions={2}, with_buckets=True)
    per_dim = bk.groupby("dim")["trades"].sum()
    assert set(per_dim.index) == {"size", "cond", "spread"}
    assert per_dim.nunique() == 1 and int(per_dim.iloc[0]) == 3, per_dim.to_dict()
    # buckets span the PRE-filter set, so they exceed what identification keeps
    row = agg.set_index("ticker").loc["BKT"]
    assert row["retail_ident_trades"] == 1          # 1 excluded by size, 1 by condition
    assert float(bk[bk["dim"] == "size"][["buy_usd", "sell_usd"]].to_numpy().sum()) > \
        float(row["retail_usd"])
    print("PASS bucket table reconciles across dimensions")


if __name__ == "__main__":
    test_signing_and_bands()
    test_at_mid_excluded()
    test_half_penny_regime()
    test_quote_update_respected()
    test_trades_only_mode()
    test_size_cap_identification_only()
    test_condition_filter_excludes_non_market_prices()
    test_derivatively_priced_is_not_excluded()
    test_half_penny_regime_detected_from_quotes()
    test_buckets_reconcile_to_eligible_set()
    print("\nAll retail-classifier tests passed.")
