"""config.py — the machine-readable mirror of DASHBOARD-SPEC.md §4.

The metric registry drives the run: which metrics exist, which panel they sit
in, their source/cadence, and the phase that turns them on. `PHASE` is the one
global switch — set it to 1/2/3 to control what the run attempts (§4 legend).

Keep this in lockstep with §4. When §4 changes, this changes; the spec table is
the human contract, this dict is the executable one.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---- global run switch -----------------------------------------------------
PHASE = 3  # 1 = BBG + free · 2 = + Massive stocks · 3 = + Massive options (§4)

APP_SLUG = "market-conditions"   # S3 prefix / URL path on lens.avos.co (§2)
SIZE_BUDGET_MB = 8               # §1 self-contained page target


def _load_env_config() -> dict:
    """Parse infra/config.env (KEY=VALUE lines) — the same non-secret,
    machine-specific config file deploy.py reads."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "infra", "config.env")
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    cfg[k.strip()] = v.strip().strip("'\"")
    return cfg


# Directory where `python -m src.run` drops the agent-readable dashboard bundle
# (manifest + per-metric JSON + methodology docs) so Cowork instances can
# synthesize the dashboard without the auth-gated web page. $AGENT_BUNDLE_DIR
# wins; then infra/config.env; else None → the export step is skipped (§2).
# See src/export_bundle.py.
AGENT_BUNDLE_DIR = os.environ.get("AGENT_BUNDLE_DIR") or _load_env_config().get("AGENT_BUNDLE_DIR") or None

# ---- universes -------------------------------------------------------------
# Concrete tickers resolved at pull time; these name the sets §4/§5 reference.
UNIVERSES = {
    "spx_members": "SPX Index MEMB",         # SC1-3, SC5, VC2, MH1
    "spx_top50": "top 50 SPX members by wt",  # SC5 Phase-1 fallback (§4.0)
    "semis_gics": "GICS industry 453010",     # SC3, VC6, RF8
    "retail_top25": "top-25 retail names (RF3)",  # LV9, LV10 weighting
    "lev_etf_complex": "US leveraged ETFs (leverage=Y)",  # OP7, LV6, LV13
}

# ---- retail scale factor (decided 2026-07-09, CIO) ---------------------------
# The §5.1 classifier identifies ~1/3 of retail activity (BHJOS capture rate;
# confirmed empirically: our 6.6% identified participation vs ~20% consensus
# total). DOLLAR-DENOMINATED stock-tape retail metrics (RF1 net flow, RF2
# participation) are scaled by this factor to ESTIMATED TOTALS, labeled as such
# in every tooltip. NOT applied to options small-lot metrics (RF7/RF8 — that
# proxy already reconciles to market totals vs Citadel ch.11) nor to ratios
# (RF3/RF5, scale-invariant) nor RF4 (a regression slope shown on the identified
# floor, not ×3-scaled — its shape is scale-invariant). Provisional until RF9 fits the
# factor empirically vs Nasdaq RTAT; revisit monthly with the §7.2 re-check.
RETAIL_SCALE_FACTOR = 3.0

# ---- curated ETF universe (OP5/OP6/OP7, LV6, LV13) ---------------------------
# ticker → (category, leverage). §A3 honesty: this is a curated top-of-complex
# universe, NOT all US ETFs — coverage is labeled on every tile it feeds.
# Flows via the shares-outstanding method: flow_t = ΔSH_OUT_t × NAV_t.
ETF_UNIVERSE = {
    # broad index
    "SPY": ("broad", 1), "IVV": ("broad", 1), "VOO": ("broad", 1), "VTI": ("broad", 1),
    "QQQ": ("broad", 1), "IWM": ("broad", 1), "DIA": ("broad", 1), "RSP": ("broad", 1),
    # sector
    "XLK": ("sector", 1), "XLF": ("sector", 1), "XLE": ("sector", 1), "XLV": ("sector", 1),
    "XLI": ("sector", 1), "XLY": ("sector", 1), "XLP": ("sector", 1), "XLU": ("sector", 1),
    "XLB": ("sector", 1), "XLRE": ("sector", 1), "XLC": ("sector", 1),
    "SMH": ("sector", 1), "SOXX": ("sector", 1), "XBI": ("sector", 1), "KRE": ("sector", 1),
    # leveraged / inverse — index
    "TQQQ": ("leveraged", 3), "SQQQ": ("leveraged", -3), "QLD": ("leveraged", 2),
    "SOXL": ("leveraged", 3), "SOXS": ("leveraged", -3),
    "UPRO": ("leveraged", 3), "SPXU": ("leveraged", -3), "SSO": ("leveraged", 2),
    "SDS": ("leveraged", -2),
    # leveraged single-stock
    "NVDL": ("leveraged", 2), "TSLL": ("leveraged", 2), "TSLS": ("leveraged", -1),
    "MSTU": ("leveraged", 2), "MSTX": ("leveraged", 2), "CONL": ("leveraged", 2),
    "PLTU": ("leveraged", 2), "AMDL": ("leveraged", 2), "BITX": ("leveraged", 2),
    # options-income
    "JEPI": ("options-income", 1), "JEPQ": ("options-income", 1),
    "QYLD": ("options-income", 1), "XYLD": ("options-income", 1),
    "RYLD": ("options-income", 1), "DIVO": ("options-income", 1), "SPYI": ("options-income", 1),
    # crypto
    "IBIT": ("crypto", 1), "FBTC": ("crypto", 1), "GBTC": ("crypto", 1),
    "ETHA": ("crypto", 1), "BITO": ("crypto", 1),
}

# underlying reference for LV6 rebalance notional (leveraged funds only):
# grouped-bars ticker whose daily move drives the fund's forced EOD flow
LEV_ETF_UNDERLYING = {
    "TQQQ": "QQQ", "SQQQ": "QQQ", "QLD": "QQQ",
    "SOXL": "SMH", "SOXS": "SMH",
    "UPRO": "SPY", "SPXU": "SPY", "SSO": "SPY", "SDS": "SPY",
    "NVDL": "NVDA", "TSLL": "TSLA", "TSLS": "TSLA",
    "MSTU": "MSTR", "MSTX": "MSTR", "CONL": "COIN",
    "PLTU": "PLTR", "AMDL": "AMD", "BITX": "IBIT",
}

# top-10 semis for VC6 (cap-weighted 3M ATM IV; §4) — refreshed from the member
# snapshot at pull time when possible; this is the fallback ordering
SEMI_TOP10 = ["NVDA", "AVGO", "AMD", "TSM", "QCOM", "TXN", "MU", "ADI", "LRCX", "AMAT"]

# VC6 comparison baskets (CIO 2026-07-10): equal-weight single-name 3M ATM IV,
# SAME construction as the semis line so levels are comparable. Sector ETF IV
# (XLV/XLP) was rejected: basket-level IV embeds cross-name correlation and
# sits structurally lower (XLV ≈ 16 vs single-name avgs ≈ 30+) — trend-only
# comparability isn't worth the level confusion on one chart.
IV_BASKETS = {
    "semis": SEMI_TOP10,  # existing line, unchanged
    "hyperscalers": ["MSFT", "AMZN", "GOOGL", "META", "ORCL"],
    "healthcare": ["LLY", "UNH", "JNJ", "ABBV", "MRK"],
    "staples": ["PG", "COST", "WMT", "KO", "PEP"],
}

# Panel order + names per CIO feedback 2026-07-09 (§6 updated to match)
PANELS = [
    ("volatility", "Volatility and Correlation"),
    ("retail", "Retail"),
    ("leverage", "Leverage"),
    ("flows", "Flows"),            # ETF flows + MOC (split from Ownership & Passive)
    ("ownership", "Households"),
    ("credit", "Credit"),          # split from Market Health
    ("internals", "Internals"),    # split from Market Health
    ("issuance", "Issuance"),
    # "Other" panel retired 2026-07-10: SC1-3 killed, SC4/SC5 → Internals
]


@dataclass(frozen=True)
class Metric:
    id: str
    panel: str          # panel key from PANELS
    name: str           # plain-language name (§A12: no bare codes)
    source: str         # short source tag rendered on the tile (§1)
    cadence: str        # daily|weekly|biweekly|monthly|quarterly
    phase: int          # 1|2|3 — availability gate
    hist: str           # min history to load at first build (§4 Hist col)
    method: str = ""    # §5 methodology ref where one applies

    @property
    def enabled(self) -> bool:
        return self.phase <= PHASE


def _m(*args, **kw):
    return Metric(*args, **kw)

# ---- registry (seeded from §4; phase gates match §4.0 Massive-first) --------
_REGISTRY_ROWS = [
    # SC1/SC2/SC3 (concentration trio) killed 2026-07-10 per CIO.
    # SC4/SC5 moved to the Internals panel (same decision).
    _m("SC4", "internals", "Implied dispersion (DSPX)", "BBG/Cboe", "daily", 1, "inception→"),
    _m("SC5", "internals", "Realized cross-sectional dispersion", "Massive/BBG-top50", "daily", 2, "2005→"),
    # Panel 2 — Ownership & Passive
    _m("OP1", "ownership", "Household equity by wealth cohort", "FRED DFA", "quarterly", 1, "1989→"),
    _m("OP2", "ownership", "Household-equity nowcast", "BBG+OP1", "daily", 1, "—", "5.6"),
    _m("OP3", "ownership", "Household cash % of financial assets", "FRED Z.1", "quarterly", 1, "1990→"),
    _m("OP4", "ownership", "Cash-ratio weekly nowcast", "ICI+FRED", "weekly", 1, "—", "5.6"),
    # OP9-11 — household saving + debt burden (FRED), Households panel.
    # OP12 (FODSP) killed 2026-07-10 — series discontinued by the Fed at 2023-Q3.
    _m("OP9", "ownership", "Personal saving rate", "FRED PSAVERT", "monthly", 1, "1959→"),
    _m("OP10", "ownership", "Personal saving (level)", "FRED PMSAVE", "monthly", 1, "1959→"),
    _m("OP11", "ownership", "Debt service ratio", "FRED TDSP", "quarterly", 1, "2005→"),
    _m("OP5", "flows", "ETF net flows", "BBG", "daily", 1, "2015→"),
    _m("OP6", "flows", "ETF flows by category", "BBG", "daily", 1, "2018→"),
    # OP7 moved Households->Flows 2026-07-10 (end of Flows).
    # OP8 (MOC auction share) killed 2026-07-10 — not interesting.
    _m("OP7", "flows", "Leveraged ETF AUM", "BBG", "daily", 1, "2018→"),
    # Panel 3 — Retail Flows
    # RF1D/RF2D dropped 2026-07-10 per CIO — RF1/RF2 are now the daily views.
    _m("RF1", "retail", "Retail net flow — daily (est. total)", "Massive", "daily", 2, "2016→", "5.1"),
    # RF10 sits 2nd in the panel (dollar-level companion to RF1's net view);
    # id is internal — cards render by registry order, not id number.
    _m("RF10", "retail", "Retail dollar volume — weekly (est. total)", "Massive+FINRA", "weekly", 2, "2023→", "5.1"),
    _m("RF2", "retail", "Retail participation (FINRA-anchored)", "Massive+FINRA", "weekly", 2, "2016→", "5.1"),
    _m("RF3", "retail", "Retail concentration", "Massive", "daily", 2, "2016→", "5.1"),
    _m("RF4", "retail", "Buy-the-dip sensitivity", "Massive+BBG", "daily", 2, "2016→"),
    _m("RF5", "retail", "Avg retail trade size", "Massive", "daily", 2, "2016→"),
    _m("RF6", "retail", "Wholesaler volume (structural check)", "FINRA", "weekly", 1, "2016→"),
    _m("RF7", "retail", "Small-lot options premium (proxy)", "Massive OPRA", "daily", 3, "at feed", "5.2"),
    _m("RF8", "retail", "Small-lot call share / semi premium", "Massive OPRA", "daily", 3, "at feed", "5.2"),
    # LV3 moved Leverage->Retail 2026-07-10 (retail 0DTE/short-dated options mix);
    # LV2 (0DTE share whole-market) dropped same day — redundant given LV3's 0DTE bucket.
    _m("LV3", "retail", "Volume by DTE bucket", "Massive OPRA", "daily", 3, "at feed"),
    _m("RF9", "retail", "Validation series (vs RTAT10)", "Nasdaq", "daily", 2, "rolling"),
    # Panel 4 — Leverage & Its Price
    _m("LV1", "leverage", "0DTE share — SPX complex", "Cboe", "daily", 1, "2022→"),
    _m("LV4", "leverage", "Options/stock notional ratio", "Massive OPRA", "daily", 3, "at feed"),
    # LV5/LV7/LV10/LV14 render via the LVT snapshot table until history accrues
    # (CIO 2026-07-10); computes keep accumulating their series JSONs.
    _m("LV6", "leverage", "Leveraged-ETF rebalance notional", "BBG+OP7", "daily", 1, "2020→"),
    _m("LV8", "leverage", "L1 ES roll implied financing", "BBG", "daily", 1, "2018→"),
    _m("LV9", "leverage", "L2 Single-name synthetic financing", "Massive OPRA", "daily", 3, "at feed", "5.5,5.10"),
    _m("LV12", "leverage", "L3 Realized retail toll", "Massive OPRA", "daily", 3, "at feed", "5.7"),
    _m("LV13", "leverage", "L3 Leveraged-ETF financing residual", "BBG", "weekly", 1, "2020→", "5.8"),
    _m("LV15", "leverage", "L4 FINRA margin debt", "FINRA", "monthly", 1, "1997→"),
    _m("LV16", "leverage", "Short interest aggregate", "BBG", "biweekly", 1, "2023-11→"),
    # LVT: no-history leverage measures (LV5 GEX, LV7 box, LV10 wings, LV14
    # rates) shown as one snapshot TABLE until each accrues chartable history
    # (CIO 2026-07-10). Their computes keep writing series JSONs so the flip
    # back to charts is automatic later.
    _m("LVT", "leverage", "Leverage levels — snapshot", "derived", "daily", 1, "—"),
    # Panel — Volatility and Correlation (vol first, then correlation; CIO 2026-07-09)
    _m("VC7", "volatility", "SPX ATM implied vs realized vol", "BBG", "daily", 1, "2010→"),
    _m("VC8", "volatility", "NDX ATM implied vs realized vol", "BBG", "daily", 1, "2010→"),
    _m("VC9", "volatility", "SPX 10% OTM call/put IV", "BBG", "daily", 1, "2010→"),
    _m("VC10", "volatility", "NDX 10% OTM call/put IV", "BBG", "daily", 1, "2010→"),
    _m("VC3", "volatility", "Vol term structure", "BBG", "daily", 1, "2010→"),
    # VC4 (skew panel) dropped 2026-07-10 per CIO — combined two unrelated reads
    _m("VC6", "volatility", "3M IV by sector basket", "BBG", "daily", 1, "2016→"),
    _m("VC1", "volatility", "Implied correlation", "BBG", "daily", 1, "inception→"),
    _m("VC2", "volatility", "Implied − realized correlation spread", "BBG", "daily", 1, "2010→"),
    # LV11 moved Leverage->Volatility 2026-07-10 (it's a vol metric: VIX/VXN vs realized)
    _m("LV11", "volatility", "Variance risk premium", "BBG", "daily", 1, "2010→"),
    # VC5 spot-up/vol-up dropped 2026-07-09 (CIO: no longer interesting)
    # Panel 6 — Market Health
    # MH1 split 2026-07-10 per CIO: MH1 = moving-average breadth only;
    # MH1B = leadership ratios (was crammed into one 5-line chart)
    _m("MH1", "internals", "Breadth (% above moving averages)", "BBG", "daily", 1, "2010→"),
    _m("MH1B", "internals", "Leadership (RSP/SPY, NDX/SPX)", "Massive+BBG", "daily", 1, "2023-11→"),
    _m("MH2", "credit", "Corporate credit (IG/HY OAS)", "BBG/FRED", "daily", 1, "2010→"),
    _m("MH3", "credit", "Household credit — market-priced", "BBG", "daily", 1, "2015→"),
    _m("MH4", "credit", "Household credit — borrowing rates", "FRED+", "weekly", 1, "2015→"),
    _m("MH5", "credit", "Household credit — amounts", "FRED+NYFed", "quarterly", 1, "2010→"),
    _m("MH6", "credit", "Delinquency transitions", "NY Fed HHDC", "quarterly", 1, "2003→"),
    _m("MH7", "internals", "Rates backdrop (MOVE, 10y, 2s10s)", "BBG", "daily", 1, "2010→"),
    _m("MH8", "internals", "Sentiment (AAII/NAAIM)", "scrape", "weekly", 1, "2010→"),
    _m("MH9", "internals", "Off-exchange + odd-lot share", "Massive", "daily", 2, "at feed"),
    # Panel 7 — Issuance
    _m("IS1", "issuance", "IPO forward pipeline", "BBG", "daily", 1, "build→"),
    _m("IS2", "issuance", "Filing rate (S-1/F-1)", "EDGAR", "weekly", 1, "2020→"),
    _m("IS3", "issuance", "Pricing outcomes", "BBG", "weekly", 1, "2020→"),
    _m("IS4", "issuance", "Aftermarket appetite (IPO ETF vs SPY)", "Massive", "daily", 1, "2023-11→"),
    _m("IS5", "issuance", "Lockup calendar", "BBG", "weekly", 1, "build→"),
    _m("IS6", "issuance", "Net equity supply", "BBG", "monthly", 1, "2015→"),
    _m("IS7", "issuance", "Fund registration filings (pipeline proxy)", "EDGAR", "weekly", 1, "2020→"),
    _m("IS8", "issuance", "Adoption velocity", "OP7", "weekly", 1, "2022→"),
    # IS9/IS10 — IPO issuance pace: 2026 YTD vs prior years, cumulative $ and
    # deal count by day-of-year (Ritter-comparable universe; see compute/ipo.py).
    _m("IS9", "issuance", "IPO issuance pace — cumulative $ (YTD vs prior years)", "BBG+Ritter", "daily", 1, "build→"),
    _m("IS10", "issuance", "IPO issuance pace — cumulative count (YTD vs prior years)", "BBG+Ritter", "daily", 1, "build→"),
]

REGISTRY: dict[str, Metric] = {m.id: m for m in _REGISTRY_ROWS}


def enabled_metrics(phase: int = None) -> list[Metric]:
    p = PHASE if phase is None else phase
    return [m for m in _REGISTRY_ROWS if m.phase <= p]


def metrics_for_panel(panel_key: str, phase: int = None) -> list[Metric]:
    p = PHASE if phase is None else phase
    return [m for m in _REGISTRY_ROWS if m.panel == panel_key and m.phase <= p]
