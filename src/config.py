"""config.py — the machine-readable mirror of DASHBOARD-SPEC.md §4.

The metric registry drives the run: which metrics exist, which panel they sit
in, their source/cadence, and the phase that turns them on. `PHASE` is the one
global switch — set it to 1/2/3 to control what the run attempts (§4 legend).

Keep this in lockstep with §4. When §4 changes, this changes; the spec table is
the human contract, this dict is the executable one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ---- global run switch -----------------------------------------------------
PHASE = 3  # 1 = BBG + free · 2 = + Massive stocks · 3 = + Massive options (§4)

APP_SLUG = "market-conditions"   # S3 prefix / URL path on lens.avos.co (§2)
SIZE_BUDGET_MB = 8               # §1 self-contained page target

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
# (RF3/RF4/RF5, scale-invariant). Provisional until RF9 calibration fits the
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

# Panel order + names per CIO feedback 2026-07-09 (§6 updated to match)
PANELS = [
    ("retail", "Retail Flows"),
    ("volatility", "Volatility and Correlation"),
    ("leverage", "Leverage"),
    ("flows", "Flows"),            # ETF flows + MOC (split from Ownership & Passive)
    ("ownership", "Ownership"),
    ("credit", "Credit"),          # split from Market Health
    ("internals", "Internals"),    # split from Market Health
    ("issuance", "Issuance"),
    ("other", "Other"),            # was Structure & Concentration
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
    # Panel 1 — Structure & Concentration
    _m("SC1", "other", "Top-10 weight of S&P 500", "BBG", "daily", 1, "2000→"),
    _m("SC2", "other", "Effective N / HHI", "BBG", "daily", 1, "2000→"),
    _m("SC3", "other", "Semiconductor weight of SPX", "BBG", "daily", 1, "2010→"),
    _m("SC4", "other", "Implied dispersion (DSPX)", "BBG/Cboe", "daily", 1, "inception→"),
    _m("SC5", "other", "Realized cross-sectional dispersion", "Massive/BBG-top50", "daily", 2, "2005→"),
    # Panel 2 — Ownership & Passive
    _m("OP1", "ownership", "Household equity by wealth cohort", "FRED DFA", "quarterly", 1, "1989→"),
    _m("OP2", "ownership", "Household-equity nowcast", "BBG+OP1", "daily", 1, "—", "5.6"),
    _m("OP3", "ownership", "Household cash % of financial assets", "FRED Z.1", "quarterly", 1, "1990→"),
    _m("OP4", "ownership", "Cash-ratio weekly nowcast", "ICI+FRED", "weekly", 1, "—", "5.6"),
    _m("OP5", "flows", "ETF net flows", "BBG", "daily", 1, "2015→"),
    _m("OP6", "flows", "ETF flows by category", "BBG", "daily", 1, "2018→"),
    _m("OP7", "ownership", "Leveraged ETF AUM", "BBG", "daily", 1, "2018→"),
    _m("OP8", "flows", "MOC auction share", "Massive", "daily", 2, "at feed"),
    # Panel 3 — Retail Flows
    _m("RF1", "retail", "Retail net flow ($, shares)", "Massive", "daily", 2, "2016→", "5.1"),
    _m("RF2", "retail", "Retail participation", "Massive", "daily", 2, "2016→", "5.1"),
    _m("RF3", "retail", "Retail concentration", "Massive", "daily", 2, "2016→", "5.1"),
    _m("RF4", "retail", "Buy-the-dip ratio", "Massive+BBG", "daily", 2, "2016→"),
    _m("RF5", "retail", "Avg retail trade size", "Massive", "daily", 2, "2016→"),
    _m("RF6", "retail", "Wholesaler volume (structural check)", "FINRA", "weekly", 1, "2016→"),
    _m("RF7", "retail", "Small-lot options premium (proxy)", "Massive OPRA", "daily", 3, "at feed", "5.2"),
    _m("RF8", "retail", "Small-lot call share / semi premium", "Massive OPRA", "daily", 3, "at feed", "5.2"),
    _m("RF9", "retail", "Validation series (vs RTAT10)", "Nasdaq", "daily", 2, "rolling"),
    _m("RF1D", "retail", "Retail net flow — daily bars", "Massive", "daily", 2, "at feed", "5.1"),
    _m("RF2D", "retail", "Retail participation — daily bars", "Massive", "daily", 2, "at feed", "5.1"),
    # Panel 4 — Leverage & Its Price
    _m("LV1", "leverage", "0DTE share — SPX complex", "Cboe", "daily", 1, "2022→"),
    _m("LV2", "leverage", "0DTE share — whole market", "Massive OPRA", "daily", 3, "at feed"),
    _m("LV3", "leverage", "Volume by DTE bucket", "Massive OPRA", "daily", 3, "at feed"),
    _m("LV4", "leverage", "Options/stock notional ratio", "Massive OPRA", "daily", 3, "at feed"),
    _m("LV5", "leverage", "Net delta & gamma; dealer GEX", "Massive OPRA", "daily", 3, "at feed", "5.3,5.10"),
    _m("LV6", "leverage", "Leveraged-ETF rebalance notional", "BBG+OP7", "daily", 1, "2020→"),
    _m("LV7", "leverage", "L1 Box-spread implied yield vs SOFR", "BBG/OPRA", "daily", 1, "build→", "5.4"),
    _m("LV8", "leverage", "L1 ES roll implied financing", "BBG", "daily", 1, "2018→"),
    _m("LV9", "leverage", "L2 Single-name synthetic financing", "Massive OPRA", "daily", 3, "at feed", "5.5,5.10"),
    _m("LV10", "leverage", "L2 Call-wing richness", "Massive OPRA", "daily", 3, "at feed", "5.10"),
    _m("LV11", "leverage", "L3 Variance risk premium", "BBG", "daily", 1, "2010→"),
    _m("LV12", "leverage", "L3 Realized retail toll", "Massive OPRA", "daily", 3, "at feed", "5.7"),
    _m("LV13", "leverage", "L3 Leveraged-ETF financing residual", "BBG", "weekly", 1, "2020→", "5.8"),
    _m("LV14", "leverage", "L4 Broker margin rates", "scrape", "quarterly", 1, "build→"),
    _m("LV15", "leverage", "L4 FINRA margin debt", "FINRA", "monthly", 1, "1997→"),
    _m("LV16", "leverage", "Short interest aggregate", "BBG", "biweekly", 1, "2010→"),
    # Panel — Volatility and Correlation (vol first, then correlation; CIO 2026-07-09)
    _m("VC7", "volatility", "SPX ATM implied vs realized vol", "BBG", "daily", 1, "2010→"),
    _m("VC8", "volatility", "NDX ATM implied vs realized vol", "BBG", "daily", 1, "2010→"),
    _m("VC9", "volatility", "SPX 10% OTM call/put IV", "BBG", "daily", 1, "2010→"),
    _m("VC10", "volatility", "NDX 10% OTM call/put IV", "BBG", "daily", 1, "2010→"),
    _m("VC3", "volatility", "Vol term structure", "BBG", "daily", 1, "2010→"),
    _m("VC4", "volatility", "Skew panel (SPX P1; member breadth P3)", "BBG/Massive", "daily", 1, "build→", "5.10"),
    _m("VC6", "volatility", "Top-10 semis avg 3M IV", "BBG", "daily", 1, "2016→"),
    _m("VC1", "volatility", "Implied correlation", "BBG", "daily", 1, "inception→"),
    _m("VC2", "volatility", "Implied − realized correlation spread", "BBG", "daily", 1, "2010→"),
    # VC5 spot-up/vol-up dropped 2026-07-09 (CIO: no longer interesting)
    # Panel 6 — Market Health
    _m("MH1", "internals", "Breadth", "BBG", "daily", 1, "2010→"),
    _m("MH2", "credit", "Corporate credit (IG/HY OAS)", "BBG/FRED", "daily", 1, "2010→"),
    _m("MH3", "credit", "Household credit — market-priced", "BBG", "daily", 1, "2015→"),
    _m("MH4", "credit", "Household credit — borrowing rates", "FRED+", "weekly", 1, "2015→"),
    _m("MH5", "credit", "Household credit — amounts", "FRED+NYFed", "weekly", 1, "2010→"),
    _m("MH6", "credit", "Delinquency transitions", "NY Fed HHDC", "quarterly", 1, "2003→"),
    _m("MH7", "internals", "Cross-asset context", "BBG", "daily", 1, "2010→"),
    _m("MH8", "internals", "Sentiment (AAII/NAAIM)", "scrape", "weekly", 1, "2010→"),
    _m("MH9", "internals", "Off-exchange + odd-lot share", "Massive", "daily", 2, "at feed"),
    # Panel 7 — Issuance
    _m("IS1", "issuance", "IPO forward pipeline", "BBG", "daily", 1, "build→"),
    _m("IS2", "issuance", "Filing rate (S-1/F-1)", "EDGAR", "weekly", 1, "2020→"),
    _m("IS3", "issuance", "Pricing outcomes", "BBG", "weekly", 1, "2020→"),
    _m("IS4", "issuance", "Aftermarket appetite", "BBG", "daily", 1, "2018→"),
    _m("IS5", "issuance", "Lockup calendar", "BBG", "weekly", 1, "build→"),
    _m("IS6", "issuance", "Net equity supply", "BBG", "monthly", 1, "2015→"),
    _m("IS7", "issuance", "ETF launches by category", "BBG+EDGAR", "weekly", 1, "2020→"),
    _m("IS8", "issuance", "Adoption velocity", "OP7", "weekly", 1, "2022→"),
]

REGISTRY: dict[str, Metric] = {m.id: m for m in _REGISTRY_ROWS}


def enabled_metrics(phase: int = None) -> list[Metric]:
    p = PHASE if phase is None else phase
    return [m for m in _REGISTRY_ROWS if m.phase <= p]


def metrics_for_panel(panel_key: str, phase: int = None) -> list[Metric]:
    p = PHASE if phase is None else phase
    return [m for m in _REGISTRY_ROWS if m.panel == panel_key and m.phase <= p]
