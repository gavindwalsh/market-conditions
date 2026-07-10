"""bbg.py — Bloomberg pulls via xbbg/blpapi (§3). Terminal must be running.

Live findings (2026-07-08 session, all verified against the Terminal):
  * INDX_MWEIGHT / INDX_MWEIGHT_HIST return the member LIST fine but the
    weights come back as garbage (-2.4e-14 for every row) — S&P constituent
    weights are not entitled on this Terminal via DAPI. Weights are therefore
    COMPUTED from float-adjusted market caps (CUR_MKT_CAP × EQY_FREE_FLOAT_PCT),
    which is the spec's sanctioned path anyway (§4 SC1 "daily from member caps").
    → Ask the BBG rep about weight entitlement; until then percentiles for
    SC1-3 accumulate from build date (young-series rule §6).
  * Multi-ticker bdh misaligns calendars for calculated indices (DSPX/COR/MOVE
    came back NaN in a mixed call but perfect alone) → hist() pulls per ticker.
  * Verified fields: CUR_MKT_CAP, EQY_FREE_FLOAT_PCT, GICS_INDUSTRY (code,
    e.g. 452020), PX_LAST via bdp/bdh; DVD_HIST bds.

Auto-install (Terminal machines only):
  pip install xbbg
  pip install blpapi --index-url=https://blpapi.bloomberg.com/repository/releases/python/simple/
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

CHUNK = 80  # bdp tickers per request — stay well inside DAPI limits


def _blp():
    try:
        from xbbg import blp
        return blp
    except ImportError as e:  # pragma: no cover - environment dependent
        raise RuntimeError(
            "Bloomberg packages not installed. On a Terminal machine run:\n"
            "  pip install xbbg\n"
            "  pip install blpapi --index-url="
            "https://blpapi.bloomberg.com/repository/releases/python/simple/"
        ) from e


def index_members(index: str = "SPX Index") -> list[str]:
    """Member tickers for an index. The weights column from this field is NOT
    trusted (see module docstring); only the ticker list is used."""
    blp = _blp()
    w = blp.bds(index, "INDX_MWEIGHT")
    col = "member_ticker_and_exchange_code"
    if col not in w.columns:  # field variant
        col = w.columns[0]
    return [f"{t} Equity" for t in w[col].tolist()]


def member_snapshot(tickers: list[str]) -> pd.DataFrame:
    """Per-member snapshot: [ticker, mkt_cap, float_pct, gics_industry, weight].
    Weight = float-adjusted cap / Σ float-adjusted caps (S&P-style float weighting)."""
    blp = _blp()
    fields = ["CUR_MKT_CAP", "EQY_FREE_FLOAT_PCT", "GICS_INDUSTRY"]
    frames = []
    for i in range(0, len(tickers), CHUNK):
        frames.append(blp.bdp(tickers[i:i + CHUNK], fields))
    df = pd.concat(frames)
    df.columns = ["mkt_cap", "float_pct", "gics_industry"][:len(df.columns)]
    df = df.reset_index().rename(columns={"index": "ticker"})
    df["float_cap"] = df["mkt_cap"] * df["float_pct"].fillna(100.0) / 100.0
    df["weight"] = df["float_cap"] / df["float_cap"].sum() * 100.0
    df["date"] = date.today().isoformat()
    return df


def hist(ticker: str, start: str, field: str = "PX_LAST") -> pd.DataFrame:
    """Daily history for ONE ticker → tidy [date, value]. Per-ticker by design
    (multi-ticker bdh misaligns calculated-index calendars — see docstring)."""
    blp = _blp()
    h = blp.bdh(ticker, field, start, date.today().isoformat())
    if h.empty:
        return pd.DataFrame(columns=["date", "value"])
    out = h.copy()
    out.columns = ["value"]
    out = out.reset_index().rename(columns={"index": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    return out.dropna()


# ---- orchestrated Phase-1 pull ----------------------------------------------
INDEX_SERIES = {
    # §4: SC4, VC1, VC3, MH7 — mnemonic → (ticker, history start)
    "dspx": ("DSPX Index", "2013-01-01"),        # inception→
    "cor1m": ("COR1M Index", "2010-01-01"),      # inception→
    "cor3m": ("COR3M Index", "2010-01-01"),
    "vix": ("VIX Index", "2010-01-01"),
    "vix3m": ("VIX3M Index", "2010-01-01"),
    "move": ("MOVE Index", "2010-01-01"),
    "dxy": ("DXY Index", "2010-01-01"),
    "ust10y": ("USGG10YR Index", "2010-01-01"),
    "ust2y": ("USGG2YR Index", "2010-01-01"),
    "spx": ("SPX Index", "2010-01-01"),          # RF4, VC5 denominators
    "spx_tr": ("SPTR Index", "2010-01-01"),      # OP2 nowcast + LV13 (total return)
    "ndx": ("NDX Index", "2010-01-01"),          # MH1 NDX/SPX rel, VC5
    "ndx_tr": ("XNDX Index", "2010-01-01"),      # LV13 (Nasdaq-100 total return)
    "vxn": ("VXN Index", "2010-01-01"),          # VC5 spot-up/vol-up (NDX 1M IV)
    "es1": ("ES1 Index", "2018-01-01"),          # LV8 roll financing
    "es2": ("ES2 Index", "2018-01-01"),
    "fncc": ("MTGEFNCL Index", "2015-01-01"),    # MH3 FNMA current coupon (verify)
}


ETF_FIELDS = ["EQY_SH_OUT", "FUND_NET_ASSET_VAL", "FUND_TOTAL_ASSETS"]
IV_3M_ATM = "3MTH_IMPVOL_100.0%MNY_DF"
IV_30D = {"put_wing": "30DAY_IMPVOL_90.0%MNY_DF",
          "atm": "30DAY_IMPVOL_100.0%MNY_DF",
          "call_wing": "30DAY_IMPVOL_110.0%MNY_DF"}


def etf_history(ticker: str, start: str = "2015-01-01") -> pd.DataFrame:
    """One ETF's [date, sh_out, nav, aum] history (shares-out flow method)."""
    blp = _blp()
    h = blp.bdh(f"{ticker} US Equity", ETF_FIELDS, start, date.today().isoformat())
    if h.empty:
        return pd.DataFrame()
    h.columns = [c[1].lower() for c in h.columns]
    h = h.rename(columns={"eqy_sh_out": "sh_out", "fund_net_asset_val": "nav",
                          "fund_total_assets": "aum"})
    out = h.reset_index().rename(columns={"index": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["ticker"] = ticker
    return out


def pull_etf_universe() -> int:
    """Pull the config ETF universe histories into the lake (one table per
    ticker: bbg_etf_<ticker>). Chunky one-time backfill; daily reruns are
    incremental in wall-time only (full re-pull, small)."""
    from .. import config, store
    pulled_at = datetime.now().isoformat(timespec="seconds")
    n = 0
    for t in config.ETF_UNIVERSE:
        try:
            df = etf_history(t)
            if not df.empty:
                store.append_parquet(f"bbg_etf_{t.lower()}", df, pulled_at=pulled_at)
                n += 1
        except Exception as e:  # noqa: BLE001 — fail soft per source (§2)
            store.log_run("bbg:etf", "fail", f"{t}: {str(e)[:80]}")
    return n


def pull_iv_histories() -> int:
    """VC4/VC6 IV histories: SPX 30d wings + ATM (2010→), semi 3M ATM (2016→).
    Moneyness-based (90/100/110%) — the delta-based §4 fields are OVDV-only."""
    from .. import config, store
    pulled_at = datetime.now().isoformat(timespec="seconds")
    n = 0
    for name, field in IV_30D.items():
        df = hist("SPX Index", "2010-01-01", field=field)
        store.append_parquet(f"bbg_spx_iv_{name}", df, pulled_at=pulled_at); n += 1
        df = hist("NDX Index", "2010-01-01", field=field)
        store.append_parquet(f"bbg_ndx_iv_{name}", df, pulled_at=pulled_at); n += 1
    for t in config.SEMI_TOP10:
        try:
            df = hist(f"{t} US Equity", "2016-01-01", field=IV_3M_ATM)
            store.append_parquet(f"bbg_iv3m_{t.lower()}", df, pulled_at=pulled_at); n += 1
            for name, field in (("atm30", IV_30D["atm"]), ("call30", IV_30D["call_wing"])):
                df = hist(f"{t} US Equity", "2016-01-01", field=field)
                store.append_parquet(f"bbg_iv_{name}_{t.lower()}", df, pulled_at=pulled_at); n += 1
        except Exception as e:  # noqa: BLE001
            store.log_run("bbg:iv", "fail", f"{t}: {str(e)[:80]}")
    # VC6 comparison baskets (semis already covered above)
    for basket, names in config.IV_BASKETS.items():
        if basket == "semis":
            continue
        for t in names:
            try:
                df = hist(f"{t} US Equity", "2016-01-01", field=IV_3M_ATM)
                store.append_parquet(f"bbg_iv3m_{t.lower()}", df, pulled_at=pulled_at); n += 1
            except Exception as e:  # noqa: BLE001
                store.log_run("bbg:iv", "fail", f"{t}: {str(e)[:80]}")
    return n


def pull_short_interest_history(start: str = "2023-11-01") -> int:
    """LV16 backfill: biweekly SHORT_INT + SHORT_INT_RATIO prints per member via
    bdh (verified live 2026-07-10: bdh serves the biweekly history). One-time —
    ~500 sequential per-ticker calls; the daily pull_short_interest snapshot
    keeps the table current afterwards."""
    from .. import store
    from datetime import datetime as _dt
    tickers = index_members()
    pulled_at = _dt.now().isoformat(timespec="seconds")
    frames = []
    for t in tickers:
        try:
            h = _blp().bdh(t, ["SHORT_INT", "SHORT_INT_RATIO"], start, date.today().isoformat())
            if h.empty:
                continue
            h.columns = [c[1].lower() for c in h.columns]
            h = h.rename(columns={"short_int": "short_int", "short_int_ratio": "short_int_ratio"})
            out = h.reset_index().rename(columns={"index": "date"})
            out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
            out["ticker"] = t
            frames.append(out)
        except Exception as e:  # noqa: BLE001
            store.log_run("bbg:si_hist", "fail", f"{t}: {str(e)[:80]}")
    if not frames:
        return 0
    df = pd.concat(frames, ignore_index=True)
    store.append_parquet("bbg_short_interest_hist", df, pulled_at=pulled_at)
    store.log_run("bbg:si_hist", "ok", f"{len(frames)} tickers, {len(df)} rows since {start}")
    return len(frames)


def pull_short_interest(tickers: list[str]) -> pd.DataFrame:
    """LV16: per-member short interest snapshot (biweekly print; accumulate)."""
    from .. import store
    blp = _blp()
    frames = []
    for i in range(0, len(tickers), CHUNK):
        frames.append(blp.bdp(tickers[i:i + CHUNK], ["SHORT_INT", "SHORT_INT_RATIO"]))
    df = pd.concat(frames)
    df.columns = ["short_int", "short_int_ratio"][:len(df.columns)]
    df = df.reset_index().rename(columns={"index": "ticker"})
    df["date"] = date.today().isoformat()
    store.append_parquet("bbg_short_interest", df,
                         pulled_at=datetime.now().isoformat(timespec="seconds"))
    return df


def pull_box_yield() -> pd.DataFrame:
    """LV7 §5.4: SPX box-spread implied yield at ~1M and ~3M from chain quotes.

    box(K1,K2) = [C(K1)−C(K2)] − [P(K1)−P(K2)] at midpoints; the box pays
    (K2−K1) at expiry with certainty → implied rate = ln((K2−K1)/box)/T.
    Median across strike pairs; one row per tenor per day (accumulates)."""
    import re

    from .. import store
    blp = _blp()
    chain = blp.bds("SPX Index", "OPT_CHAIN")
    descs = chain[chain.columns[0]].astype(str)
    spot = float(blp.bdp("SPX Index", "PX_LAST").iloc[0, 0])

    rx = re.compile(r"SPXW?\s+US\s+(\d{2}/\d{2}/\d{2})\s+([CP])(\d+)\s+Index")
    parsed = []
    for d in descs:
        m = rx.match(d)
        if m:
            parsed.append({"desc": d, "exp": m.group(1), "cp": m.group(2),
                           "k": int(m.group(3)), "weekly": d.startswith("SPXW")})
    df = pd.DataFrame(parsed)
    if df.empty:
        raise RuntimeError("SPX chain parse produced no contracts")
    df["exp_date"] = pd.to_datetime(df["exp"], format="%m/%d/%y")
    today = pd.Timestamp(date.today())

    rows = []
    for tenor, days_target in (("1M", 30), ("3M", 91)):
        cand = df[(df["exp_date"] - today).dt.days.between(15, days_target + 45)]
        if cand.empty:
            continue
        exp = cand.loc[((cand["exp_date"] - today).dt.days - days_target).abs().idxmin(), "exp_date"]
        sub = df[(df["exp_date"] == exp) & df["weekly"]]
        if sub.empty:
            sub = df[df["exp_date"] == exp]
        strikes = sorted(set(sub[sub["cp"] == "C"]["k"]) & set(sub[sub["cp"] == "P"]["k"]))
        near = [k for k in strikes if 0.90 * spot <= k <= 1.05 * spot and k % 100 == 0]
        pairs = [(k1, k2) for k1 in near for k2 in near if 300 <= k2 - k1 <= 600][:5]
        if not pairs:
            continue
        legs = sorted({s for p in pairs for k in p
                       for s in sub[(sub["k"] == k)]["desc"].tolist()})
        q = blp.bdp(legs, ["PX_BID", "PX_ASK"])
        q = q.rename(columns={"px_bid": "bid", "px_ask": "ask"})  # name-based: xbbg sorts columns
        q["mid"] = (q["bid"] + q["ask"]) / 2.0
        q = q[(q["bid"] > 0) & (q["ask"] > q["bid"])]
        mids = {}
        for desc in q.index:
            m = rx.match(desc)
            if m:
                mids[(m.group(2), int(m.group(3)))] = q.loc[desc, "mid"]
        T = (exp - today).days / 365.0
        rates = []
        for k1, k2 in pairs:
            try:
                box = (mids[("C", k1)] - mids[("C", k2)]) - (mids[("P", k1)] - mids[("P", k2)])
            except KeyError:
                continue
            if box <= 0:
                continue
            rates.append(float(np_log((k2 - k1) / box) / T) * 100.0)
        if rates:
            rows.append({"date": date.today().isoformat(), "tenor": tenor,
                         "rate": float(pd.Series(rates).median()), "n_pairs": len(rates),
                         "expiry": exp.strftime("%Y-%m-%d")})
    out = pd.DataFrame(rows)
    if not out.empty:
        store.append_parquet("bbg_box_yield", out,
                             pulled_at=datetime.now().isoformat(timespec="seconds"))
    return out


def np_log(x):
    import numpy as np
    return np.log(x)


def pull(members_index: str = "SPX Index") -> dict:
    """Phase-1 BBG pull: index-level daily histories + the member snapshot.
    Index histories are cheap; re-pulled in full (idempotent, small). The
    member snapshot appends one row-set per run day → SC1-3 series accumulate."""
    from .. import store
    pulled_at = datetime.now().isoformat(timespec="seconds")
    out = {}
    for m, (ticker, start) in INDEX_SERIES.items():
        df = hist(ticker, start)
        store.append_parquet(f"bbg_{m}", df, pulled_at=pulled_at)
        out[m] = df
    tickers = index_members(members_index)
    snap = member_snapshot(tickers)
    store.append_parquet("bbg_spx_members", snap, pulled_at=pulled_at)
    out["spx_members"] = snap
    return out
