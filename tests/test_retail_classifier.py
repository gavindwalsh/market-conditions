"""§7.6: classifier on synthetic prints — known subpenny/midpoint cases.
Every §5.1 rule gets a print that isolates it. Run: python tests/test_retail_classifier.py
"""
import os
import sys
import tempfile

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
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


if __name__ == "__main__":
    test_signing_and_bands()
    test_at_mid_excluded()
    test_half_penny_regime()
    test_quote_update_respected()
    test_trades_only_mode()
    test_size_cap_identification_only()
    print("\nAll retail-classifier tests passed.")
