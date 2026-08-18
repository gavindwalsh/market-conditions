# Market Pulse Dashboard — Build Specification

- **Format:** improvised — engineering build spec; nearest library match was Quantitative Research Brief (B10), adapted per §B0 because the deliverable is a system, not a research finding.
- **Author / desk chain:** CIO + research session (Cowork, 2026-07-08) → Claude Code (builder)
- **Date:** 2026-07-08 · US date format, Eastern Time, USD throughout (§A1)
- **Purpose:** a locally run Python pipeline producing a single self-contained HTML dashboard — a daily aggregate pulse on US equity and options markets. Origin: replication and extension of Citadel Securities' "1H 2026 Market Structure & Flows" (Scott Rubner, 2026-06-30).
- **Companion:** `Citadel_1H26_Chart_Data_Source_Map.xlsx` (repo root) — per-chart source mapping that seeded this spec.
- **Canonical location:** this repo (`market-conditions-dashboard`) is the single source of truth for this spec and the build. The former copy under `equities-pm/dashboard/` is retired to a one-line pointer (decided 2026-07-08).
- **House standards:** `equities-pm/HOUSE-STANDARDS.md` is the `§A#` spine referenced throughout; `equities-pm/style_guides/` (+ `LOGOTYPE-1.png`) is the visual source of truth (§A9).
- **Deploys to:** `lens.avos.co/market-conditions`, behind the existing Cognito→Entra SSO, via the same S3 + CloudFront + `deploy.py` pattern as `avos-country-dashboard` (§2 Deployment).

---

## 1. Product definition

One command (`python -m dashboard.run`) executed locally after US close produces `outputs/dashboard/pulse_YYYY-MM-DD.html` (plus a stable `pulse_latest.html` copy):

- **Single file, fully self-contained.** All data embedded as JSON in the page; charting library inlined (no CDN calls, opens offline). Target < 8 MB.
- **Daily cadence** is the design point. Slower sources (weekly/monthly/quarterly) show their native cadence with explicit as-of stamps; nothing pretends to be fresher than it is (§A1 date-and-as-of-everything).
- **Seven panels** (§4): Structure & Concentration · Ownership & Passive · Retail Flows · Leverage & Its Price · Volatility & Character · Market Health · Issuance.
- **Every metric tile carries:** current value, as-of date, source tag, and trailing percentile vs its own history (so "high/low vs context" is computed, not eyeballed).
- **No server, no scheduler dependency.** Local cron/Task Scheduler optional; the run must also work ad hoc.

## 2. Architecture

Respect the repo's three tiers (README): Method = this spec; Machinery = `dashboard/src/`; Material = `data/dashboard/` (never committed wholesale into context).

```
dashboard/
├── DASHBOARD-SPEC.md          # this file
├── src/
│   ├── run.py                 # orchestrator: pull → compute → render
│   ├── config.py              # metric registry, universes, phase flags
│   ├── pull/                  # one module per source
│   │   ├── bbg.py             # xbbg/blpapi (Terminal must be running)
│   │   ├── fred.py            # FRED REST (API key in .env)
│   │   ├── massive.py         # Massive REST + flat files (stocks, options)
│   │   ├── edgar.py           # SEC EDGAR full-text + daily index
│   │   ├── finra.py           # FINRA Query API (OTC weekly, margin debt)
│   │   └── free.py            # Cboe stats, NY Fed HHDC, ICI, AAII/NAAIM, OCC
│   ├── compute/               # metric constructions (§5 methodologies)
│   ├── store.py               # DuckDB lake (raw+aggregates) + JSON display-layer emit
│   └── render/                # Jinja2 template + inlined ECharts + house CSS
├── tests/                     # unit tests per §7
├── build_data/                # JSON display layer the renderer embeds (deterministic)
├── deploy.py                  # push pulse_latest.html → S3 lens/market-conditions/
└── .fred_api_key, .massive_api_key, ...   # single-line gitignored key files (house convention)
```

**Storage — two layers (decided 2026-07-08).** Follows the proven `avos-country-dashboard` pattern, extended for tick/OPRA volume:

1. **Data lake (raw + aggregates).** DuckDB + incremental Parquet per source table under `data/dashboard/`. Every pull is append-only with a `pulled_at` stamp. This layer holds what JSON can't — SIP/OPRA aggregates, deep history. Raw tick/OPRA extracts are pruned after aggregation; computed aggregates are kept forever.
2. **Display layer.** The compute step emits `build_data/*.json` (the downsampled, percentile-annotated series the page actually shows — daily ≤1yr, monthly >1yr per §6). The renderer reads **only** `build_data/`, so the build is deterministic and fully offline: byte-identical rerun given pinned display JSON (house standard). The lake can be rebuilt/reprocessed without touching a shipped page.

**Secrets.** Single-line gitignored key files at repo root (`.fred_api_key`, `.massive_api_key`, …), matching `avos-country-dashboard` — not a `.env`. Bloomberg needs no key (Terminal session).

**Failure handling.** A source failing must never kill the run: render the panel with last-good data (last good `build_data/*.json`) and a visible staleness flag (yellow as-of stamp). Log per-source status to `data/dashboard/run_log.jsonl`.

**Runtime budget.** Phase 1 target < 10 min; Phase 3 (OPRA aggregation from flat files) < 60 min. Heavy pulls run first, in parallel where sources allow.

**Deployment (decided 2026-07-08).** The daily run is **local** — Bloomberg Terminal is desktop-bound and Massive flat-file pulls run on the workstation — then `python deploy.py` pushes `pulse_latest.html` to S3 key `market-conditions/index.html` and invalidates CloudFront, exactly as `avos-country-dashboard/deploy.py` does. The existing `lens.avos.co` distribution + Cognito→Entra Lambda@Edge auto-covers any new `/<slug>`, so no new infra is needed — the page lands at `lens.avos.co/market-conditions` behind SSO. Local Task Scheduler may drive the daily run + deploy; ad-hoc runs work identically. No cloud build step (it would need the Terminal).

## 3. Data sources & credentials

| Source | Access | Auth | Used for | Cost status |
|---|---|---|---|---|
| Bloomberg Terminal | `xbbg`/`blpapi`, desktop API | Terminal session | index weights, IV/skew, ETF flows & AUM, credit OAS, rates, futures | already licensed |
| Massive (ex-Polygon) — Stocks | REST + flat files (S3, separate creds) | API key + `.massive_s3_key` | full SIP trades+NBBO: retail flow, tape imbalance, MOC share, off-exchange share | **live** — quotes tier added 2026-07-08 (base Stocks tier was trades-only) |
| Massive — Options | REST + flat files | API key | OPRA trades+quotes: premium flows, 0DTE, GEX, small-lot, box/synthetic financing | **live** (REST verified 2026-07-08; flat-file OPRA at Phase 3) |
| FRED | REST | free API key | DFA, Z.1, H.8, H.6, ICE BofA OAS fallback, PMMS, G.19 | free |
| SEC EDGAR | REST/daily index | none (UA header) | S-1/S-1A pipeline, ETF launch filings (485APOS/N-1A) | free |
| FINRA Query API | REST | free key | OTC weekly wholesaler volumes, margin debt | free |
| Cboe stats pages | CSV/scrape | none | SPX 0DTE share, DSPX/COR EOD fallback | free |
| NY Fed HHDC | XLSX download | none | household balances, originations, delinquency transitions (quarterly) | free |
| ICI | CSV/scrape | none | weekly MMF assets, weekly fund flows | free |
| Nasdaq Data Link RTAT10 | REST | free key | top-10 retail names daily — **validation only** (§7) | free tier |
| AAII / NAAIM / OCC / boxtrades.com | scrape/CSV | none | sentiment, options account-type volume, box-yield cross-check | free |

**Sourcing principle — Massive-first for raw tape (decided 2026-07-08).** Where a metric is fundamentally *raw trades or option quotes*, source it from Massive, not Bloomberg — this removes the BBG desktop-API rate ceiling and the overnight per-name OVDV batch. Concretely: per-name implied-vol surfaces, skew, GEX, box/synthetic financing, and SPX-member returns come from Massive (we compute IV/greeks ourselves — see §5.10 vol engine). This shifts those metrics into Phase 2/3 (they need the SIP/OPRA pipeline), which is fine given Massive is being procured now. Bloomberg + FRED remain the source for everything Massive does **not** carry — computed analytics and reference data: index membership/weights, GICS, credit OAS indices, correlation indices (COR/DSPX), ETF flows & AUM, dividend forecasts, rates/futures/MOVE/DXY. See §4.0 for the per-metric reassignment.

Field mnemonics/series IDs marked **[verified]** were tested live in the 2026-07-08 session; all others must be verified against the source in the first build pass before being trusted (§A2 discipline applies to the build too).

## 4. Panels & metric registry

> **Amendment 2026-07-27 (CIO review — supersedes the 2026-07-10 block below
> where they conflict; `config.py` registry is authoritative):**
> - **Killed:** LV16 (short interest aggregate) — not helpful. Registry row and
>   compute removed; the BBG `SHORT_INT` pull keeps accruing prints so the card
>   could return without a fresh backfill. Supersedes the two LV16 items in the
>   2026-07-10 block.
> - **OP2 → % of nominal GDP, full history.** Was the last 12 quarters in
>   dollars; now the whole DFA record (1989 Q3→) divided by FRED `GDP`, so
>   today's reading is comparable to the 2000 and 2007 peaks. The nowcast rolls
>   the dollar level with SPX TR and divides by the last published GDP print
>   (held flat — the current quarter's GDP is unpublished).
> - **OP10 → % of nominal GDP.** Personal saving (`PMSAVE`, $B SAAR) over FRED
>   `GDP` ($B SAAR); both annual-rate, quarterly denominator carried forward to
>   monthly. The pandemic y-axis clip is kept.
> - **SC5:** added a 1-month (21-session) rolling-average line over the daily
>   cross-section, which is too jumpy to read a trend off.

> **Amendment 2026-07-10 (CIO chart review, 27 items — supersedes conflicting
> rows below; `config.py` registry is authoritative):**
> - **Killed:** SC1, SC2, SC3 (concentration trio), VC4 (skew panel — combined
>   two unrelated reads), RF1D/RF2D (RF1/RF2 are now the daily/weekly views).
> - **Moved:** SC4, SC5 → Internals panel; the "Other" panel is retired.
> - **Split:** MH1 → MH1 (% above 50/200dma) + MH1B (leadership: RSP/SPY +
>   NDX/SPX rebased at common start; cumulative A/D line dropped). MH7 slimmed
>   to MOVE + UST10y + 2s10s (DXY dropped).
> - **New:** LVT — snapshot table for no-history leverage reads (LV5 GEX, LV7
>   box, LV10 wings, LV14 broker rates); each returns to a chart as history
>   accrues. LV16 gained real history via a SHORT_INT bdh backfill (2023-11→).
> - **Chart forms:** RF1 daily line, 0-100% with a 50% reference line;
>   RF3 bars ($B) + share lines dual-axis; MH9 bars+line dual-axis; LV3/OP6/MH5 stacked bars; LV6
>   signed daily bars; IS2 monthly bars; IS7 weekly bars (insurance filers
>   excluded from the count).
> - **Fixes:** realized-vol legs → BBG convention (360 trading days, log
>   returns, √260) + tenor-matched 1M leg on VC7/VC8; RF2 FINRA anchor
>   restricted to T1+T2 tiers with a T1-only lag segment and Friday-aligned
>   week labels; LV13 sign error + missing SOFR subtraction fixed (long index
>   funds only); LV8 pruned of quarterly-roll artifacts; LV16 float-cap unit
>   fix; OP2 display anchoring; OP6 ghost-row staleness fix; LV2 5d-avg +
>   holiday filter with the SPX line parked for LV1.
> - **VC6:** four equal-weight single-name 3M-IV baskets (semis, hyperscalers,
>   healthcare, staples) — sector-ETF IV rejected (embeds correlation, not
>   level-comparable).
> - **Presentation:** one-sentence tooltips + visible status badges
>   (uncalibrated/provisional/unverified/building); long methodology moved to
>   the generated `APPENDIX.md`; legends on multi-series charts; two-pass
>   palette assignment (no duplicate line colors).

Legend: **Phase** 1 = Bloomberg + free sources · 2 = + Massive stocks · 3 = + Massive options. **Hist** = minimum history to load at first build. Chart forms per house standards §A9.3 (direct series labels where space allows).

### §4.0 Source assignment under Massive-first (decided 2026-07-08)

The §3 principle reassigns the heavy per-name pulls off Bloomberg. Reassigned metrics (their rows below carry the updated Source/Phase):

- **SC5** realized cross-sectional dispersion → Massive equities member returns (Phase 2 primary; BBG member returns remain a Phase-1 fallback for the top-50 by weight so the tile is live before Phase 2).
- **LV10 / VC4 (member-inverted-skew view) / SC4 (Cboe-fallback aside)** → per-name IV by delta computed from Massive OPRA quotes via the §5.10 vol engine (Phase 3). Bloomberg OVDV is dropped as the primary — no more overnight member batch.
- **Index-level and small-N options metrics stay on Bloomberg in Phase 1:** SPX box (LV7) and ES roll (LV8) are index-level and cheap; VC6 (10 semis) and the SPX-level skew in VC4/SC4 are ≤10 names. These do not hit the rate ceiling, so they remain BBG Phase-1 for early availability, with Massive OPRA as the Phase-3 precision source where noted.

### Panel 1 — Structure & Concentration

| ID | Metric | Construction | Source / field | Cadence | Phase | Hist |
|---|---|---|---|---|---|---|
| SC1 | Top-10 weight of S&P 500 | sum of 10 largest member weights | BBG `SPX Index` MEMB / `INDX_MWEIGHT_HIST` (monthly) + daily from member caps | daily | 1 | 2000→ |
| SC2 | Effective N / HHI | 1 / Σw² over SPX members | same pull as SC1 | daily | 1 | 2000→ |
| SC3 | Semiconductor weight of SPX | Σ weights, GICS industry 453010 | SC1 pull + `GICS_INDUSTRY` per member | daily | 1 | 2010→ |
| SC4 | Implied dispersion | level + trailing percentile | BBG `DSPX Index`; Cboe EOD fallback | daily | 1 | inception→ |
| SC5 | Realized cross-sectional dispersion | std-dev of SPX member daily returns | Massive equities member returns (2); BBG top-50 fallback (1) | daily | 2 | 2005→ |

### Panel 2 — Ownership & Passive

| ID | Metric | Construction | Source | Cadence | Phase | Hist |
|---|---|---|---|---|---|---|
| OP1 | Household equity by wealth cohort | DFA levels, all four cohorts + bottom-50% highlight | FRED `WFRBLB50095`/`WFRBLN40068`/`WFRBLN09041`/`WFRBLT01014` + share series `WFRBS*` **[verified 2026-07-08]** (original seed `WFRBLB50107` was wrong) | quarterly (~11-wk lag) | 1 | 1989→ |
| OP2 | OP1 nowcast | roll last DFA level forward with SPX total return; cohort shares held constant (§5.6) | BBG SPX TR + OP1 | daily | 1 | — |
| OP3 | Household cash % of financial assets | Z.1 B.101 cash components / total financial assets | FRED `BOGZ1FL193020005Q`+`BOGZ1FL193030205Q`+`BOGZ1FL193034005Q` / `BOGZ1FL194090005Q` **[verified 2026-07-08]** | quarterly | 1 | 1990→ |
| OP4 | OP3 weekly nowcast | ICI MMF weekly + H.8 deposits over rolled-forward denominator (§5.6) | ICI + FRED H.8/H.6 | weekly | 1 | — |
| OP5 | ETF net flows | daily + cumulative-YTD vs each prior year (Citadel ch.7 form) | BBG ETF fund-flow aggregation across US ETFs | daily (T+1) | 1 | 2015→ |
| OP6 | ETF flows by category | broad-index / sector / leveraged / options-income / crypto | OP5 pull + fund classification | daily | 1 | 2018→ |
| OP7 | Leveraged ETF AUM | total + by underlying exposure (tech, semis, single-stock) | BBG ETF screen leverage=Y, `FUND_TOTAL_ASSETS` | daily | 1 | 2018→ |
| OP8 | MOC auction share | closing-auction volume / total volume, SPX universe | Massive trades (auction condition codes) | daily | 2 | at feed |

### Panel 3 — Retail Flows

| ID | Metric | Construction | Source | Cadence | Phase | Hist |
|---|---|---|---|---|---|---|
| RF1 | Retail breadth | share of names with ≥$10M identified retail where retail was a net buyer (§5.1) | Massive trades + NBBO | daily | 2 | 2016→ backfill |
| RF2 | Retail participation | identified retail $ / total tape $, ×capture factor, anchored to FINRA retail-wholesaler volume | RF1 pipeline + FINRA | weekly | 2 | 2016→ |
| RF10 | Retail dollar volume | identified retail $ ×capture factor, spliced onto FINRA retail-wholesaler history | RF1 pipeline + FINRA | weekly | 2 | 2023→ |
| RF3 | Retail concentration | share of retail $ in top-10 SPX names; in semis; in leveraged ETFs | RF1 × SC1/SC3 memberships | daily | 2 | 2016→ |
| RF4 | Buy-the-dip sensitivity | rolling OLS slope of daily retail **breadth** on SPX % return, sign-flipped (+ = buys dips); 63d + 21d windows | RF1 + BBG SPX returns | daily | 2 | 2016→ |
| RF6 | Wholesaler volume (structural check) | weekly non-ATS volume, top wholesalers | FINRA Query API `WeeklySummary` | weekly (2–4-wk lag) | 1 | 2016→ |
| RF7 | Small-lot options premium | premium $ where trade size < 10 contracts (retail **proxy**, labeled as such) | Massive OPRA trades | daily | 3 | at feed |
| RF8 | Small-lot call share / semi premium | RF7 split call/put; filtered to semi underlyings | RF7 pipeline | daily | 3 | at feed |

### Panel 4 — Leverage & Its Price

*The four-layer leverage-cost structure agreed 2026-07-08: (L1) collateralized market rate → (L2) demand-driven richness → (L3) realized toll → (L4) explicit retail margin.*

| ID | Metric | Construction | Source | Cadence | Phase | Hist |
|---|---|---|---|---|---|---|
| LV1 | 0DTE share — SPX complex | Cboe published stat | Cboe (free) | daily | 1 | 2022→ |
| LV2 | 0DTE share — whole market | volume where expiry = trade date / total | Massive OPRA by expiry | daily | 3 | at feed |
| LV3 | Volume by DTE bucket | 0 / 1–5 / 6–30 / >30 days, contracts + premium | Massive OPRA | daily | 3 | at feed |
| LV4 | Options/stock notional ratio | delta-notional traded / stock $ volume, per underlying + aggregate | Massive OPRA + trades | daily | 3 | at feed |
| LV5 | Net delta & gamma traded; dealer GEX | signed option flow → dealer positioning estimate (§5.3) | Massive OPRA trades+quotes | daily | 3 | at feed |
| LV6 | Leveraged-ETF rebalance notional | Σ AUM×L×(L−1)×daily index move (forced end-of-day flow estimate) | OP7 + BBG index returns | daily | 1 | 2020→ |
| LV7 | **L1** Box-spread implied yield vs SOFR | SPX box from chain quotes (§5.4); boxtrades.com cross-check | BBG SPX chain (1) / OPRA (3) | daily | 1 | build→ |
| LV8 | **L1** ES roll implied financing | calendar-spread richness vs SOFR (Citadel ch.16 analog) | BBG ES futures | daily (roll windows flagged) | 1 | 2018→ |
| LV9 | **L2** Single-name synthetic financing index | put-call-parity implied financing per retail-favorite name, spread over LV7, volume-weighted composite (§5.5) | Massive OPRA quotes | daily | 3 | at feed |
| LV10 | **L2** Call-wing richness | 25Δ call IV − ATM IV, volume-weighted across retail-heavy names; % SPX members inverted (Citadel ch.20) | Massive OPRA quotes → §5.10 vol engine (per-name IV by delta) | daily | 3 | at feed |
| LV11 | **L3** Variance risk premium | 1M implied − subsequent realized, SPX & NDX | BBG | daily | 1 | 2010→ |
| LV12 | **L3** Realized retail toll | small-lot premium paid vs settlement value of those contracts, aggregate daily $ (§5.7) | Massive OPRA + closes | daily | 3 | at feed |
| LV13 | **L3** Leveraged-ETF financing residual | NAV return regressed on L×index − fee; residual = embedded swap spread (§5.8) | BBG NAV + index series | weekly estimate | 1 | 2020→ |
| LV14 | **L4** Broker margin rates | posted rates: IBKR, Schwab, Robinhood Gold, Fidelity | scrape, quarterly refresh | quarterly | 1 | build→ |
| LV15 | **L4** FINRA margin debt | level + YoY | FINRA (monthly, ~3-wk lag) | monthly | 1 | 1997→ |
| LV16 | Short interest aggregate | SPX-universe short interest + days-to-cover | BBG (biweekly) | biweekly | 1 | 2010→ |

### Panel 5 — Volatility & Character

| ID | Metric | Construction | Source | Cadence | Phase | Hist |
|---|---|---|---|---|---|---|
| VC1 | Implied correlation | `COR1M Index`, `COR3M Index` + percentiles | BBG | daily | 1 | inception→ |
| VC2 | Implied − realized correlation spread | VC1 minus realized member correlation (rolling 1M/3M) | BBG member returns | daily | 1 | 2010→ |
| VC3 | Vol term structure | VIX/VIX3M ratio; per-name 1M/3M slopes for top names | BBG | daily | 1 | 2010→ |
| VC4 | Skew panel | SPX 25Δ put skew + semi call richness (BBG, P1); % SPX members with inverted 1M call skew (=LV10 view, Massive P3) | BBG OVDV (SPX/semis) + Massive OPRA via §5.10 (member breadth) | daily | 1 (member view 3) | build→ |
| ~~VC5~~ | ~~Spot-up/vol-up frequency~~ | **DROPPED 2026-07-09 (CIO)** | — | — | — | — |
| VC7 | SPX ATM implied vs realized vol | 30d ATM IV (call=put by parity) vs trailing-252d realized | BBG moneyness IV + SPX | daily | 1 | 2010→ |
| VC8 | NDX ATM implied vs realized vol | as VC7 for NDX | BBG + NDX | daily | 1 | 2010→ |
| VC9 | SPX 10% OTM call/put IV | 30d IV at 90%/110% mny (put/call wings) vs realized | BBG | daily | 1 | 2010→ |
| VC10 | NDX 10% OTM call/put IV | as VC9 for NDX | BBG | daily | 1 | 2010→ |
| VC6 | Top-10 semis avg 3M IV | cap-weighted 3M ATM IV of 10 largest semis (Citadel ch.18) | BBG per-name IV | daily | 1 | 2016→ |

### Panel 6 — Market Health

| ID | Metric | Construction | Source | Cadence | Phase | Hist |
|---|---|---|---|---|---|---|
| MH1 | Breadth | % SPX members > 200dma / 50dma; A/D line; RSP/SPY ratio; NDX/SPX rel | BBG | daily | 1 | 2010→ |
| MH2 | **Corporate credit** | IG OAS, HY OAS, HY−IG differential, each with 4-yr range + percentile | BBG `LUACOAS Index`, `LF98OAS Index` **[verified 2026-07-08]**; FRED `BAMLC0A0CM`/`BAMLH0A0HYM2` fallback | daily | 1 | 2010→ |
| MH3 | **Household credit — market-priced** | agency MBS current-coupon spread vs Treasuries; consumer ABS OAS (cards, autos); non-agency tail optional | BBG index tickers (verify at build) | daily | 1 | 2015→ |
| MH4 | Household credit — borrowing rates | PMMS 30y − 10y (`MORTGAGE30US`−`DGS10`); Optimal Blue daily lock rate; card APR − fed funds (G.19); auto 60mo; **lock-in gap** = outstanding avg mortgage coupon (FHFA NMDB) − new rate | FRED + Optimal Blue + FHFA | weekly/quarterly mix | 1 | 2015→ |
| MH5 | Household credit — amounts | G.19 revolving/nonrevolving (monthly); NY Fed HHDC balances & originations by product and score (quarterly); **H.8 weekly bank consumer-loan nowcast** | FRED + NY Fed | weekly→quarterly | 1 | 2010→ |
| MH6 | Delinquency transitions | early-stage (30+) transition rate by product | NY Fed HHDC | quarterly | 1 | 2003→ |
| MH7 | Cross-asset context | MOVE, DXY, 10y, curve slope | BBG | daily | 1 | 2010→ |
| MH8 | Sentiment | AAII bull−bear; NAAIM exposure | scrape (weekly) | weekly | 1 | 2010→ |
| MH9 | Off-exchange + odd-lot share | TRF share of volume; odd-lot share | Massive trades | daily | 2 | at feed |

*Student loans: federal rates are set annually by statutory formula (10y auction + fixed margin) — display level + balance only; no market spread exists.*

### Panel 7 — Issuance

| ID | Metric | Construction | Source | Cadence | Phase | Hist |
|---|---|---|---|---|---|---|
| IS1 | IPO forward pipeline | deal count + expected $ next 4/12 weeks, by sector | BBG IPO/ECDR; Nasdaq/NYSE calendars cross-check | daily | 1 | build→ |
| IS2 | Filing rate | S-1/F-1 count & $ per week; S-1/A amendment rate | EDGAR daily index | weekly | 1 | 2020→ |
| IS3 | Pricing outcomes | rolling 4-wk: % above/within/below range; % upsized; avg first-day return; postponements | BBG + calendars | weekly | 1 | 2020→ |
| IS4 | Aftermarket appetite | Renaissance IPO ETF vs SPY relative strength | BBG | daily | 1 | 2018→ |
| IS5 | Lockup calendar | $ of expirations next 4/12 weeks | BBG / trackers | weekly | 1 | build→ |
| IS6 | Net equity supply | IPO + follow-on + convert issuance − announced buybacks, monthly | BBG ECM data | monthly | 1 | 2015→ |
| IS7 | ETF launches | weekly count **by category** (leveraged single-stock / buffer-income / crypto / thematic / plain); closures | BBG ETF screen + EDGAR 485APOS/N-1A pipeline | weekly | 1 | 2020→ |
| IS8 | Adoption velocity | days-to-$100M AUM for launches in trailing 12m | OP7 AUM histories | weekly | 1 | 2022→ |

## 5. Methodology appendix (constructions that need exact definitions)

**5.1 Retail flow classifier (RF1).** Universe: all US common + ETPs, price ≥ $1. **Identify:** off-exchange (TRF) prints with subpenny price improvement — price fractional part in (0, 0.4)¢ or (0.6, 1.0)¢; exclude exact half-penny and round-penny. **Sign:** **quote-midpoint method** (Barber-Huang-Jorion-Odean-Schwarz, JF 2024): trade below prevailing NBBO midpoint → retail sell; above → buy; at midpoint → excluded. Do **not** use original BJZZ subpenny-position signing (28% error rate; midpoint signing ≈ 5%). **Two filters BJZZ lacks (added 2026-08).** BJZZ assumes institutions are a small share of off-exchange subpenny prints; Battalio-Jennings-Saglam-Wu show that assumption fails, and it failed badly here — on 2026-07-30 SPY and QQQ carried scaled net flow at 64% and 58% of their own consolidated volume, which is arithmetically impossible for retail. (a) **Per-print size cap** `config.RETAIL_MAX_PRINT_USD` = $200k: BJZZ's only institutional guard is the 0.4–0.6¢ exclusion band, which sheds ATS midpoint crosses but not VWAP/benchmark/negotiated prints struck away from the mid. (b) **Sale-condition filter** `config.RETAIL_EXCLUDE_CONDITIONS` = {2, 21, 52, 53} — average-price, price-variation and contingent trades are institutional by construction and their computed prices land on subpennies constantly. Each member is justified by its MEASURED average print size against the $4,128 standard print: avg-price $15,461, contingent $1,883,432, QCT $559,284. Condition 10 (derivatively priced) was in this set until 2026-08-18 and was removed — it fired on 17.4% of eligible prints averaging $1,391, i.e. SMALLER than a standard print, so it stripped the most retail-looking flow on the tape and moved one day's breadth by ten points on its own. Filters here must remove flow too LARGE to be retail; that one did the opposite. Odd-lot (37) and ISO (14) are deliberately kept. Both filters apply to `retail_*` only; every `tape_*` aggregate stays whole-market. Both are also **measured** (`excl_size_*`, `excl_cond_*`), and `massive_retail_buckets` stores the pre-filter eligible set cut by notional / condition / spread so either threshold can be re-cut from the lake without another tape pull. **Scaling.** RF1 is a COUNT (breadth) and carries no scale factor. Dollar metrics (RF2, RF10, RF3 bars) are scaled by a **capture factor fitted against FINRA's reported retail-wholesaler volume** — `mean(FINRA wholesaler $ ÷ identified $)` over overlapping weeks (`retail_series._fit_capture`), falling back to `config.RETAIL_SCALE_FACTOR` and keeping the *uncalibrated* badge when too few weeks overlap. NOT applied to options small-lot metrics (RF7/RF8) nor to scale-invariant ratios (RF3 share lines, RF4 slope). **Half-penny tick regime:** for symbols on the SEC half-penny tick (phased from Nov 2025) the subpenny bands are recomputed on the 0.5¢ grid. The per-symbol regime is **detected from the quotes file** (a symbol quoting on the half-cent grid is on the half-penny tick) and stored per symbol-day, so it maintains itself as the reform phases symbols in. NOTE: this was specified but never wired until 2026-08 — every day before that was scored on the penny grid regardless of actual tick. **Methodology version:** every stored day carries `method_version`; a bump forces a full reprocess and the compute refuses to build while the lake holds mixed versions.

**5.2 Small-lot proxy (RF7/RF8, LV12).** Trades < 10 contracts = retail proxy. Label as *proxy* everywhere it renders (§A3 honesty: this is an observed regularity, not an identification).

**5.3 Dealer GEX (LV5).** *Revised 2026-07-08 — options plan is trades-only (no OPRA quotes; CIO decision).* Flow-signing (sign via quote position) is not possible without quotes; LV5 ships as **OI-convention GEX**: per contract, open interest × gamma (both from Massive EOD snapshots — Massive computes IV/greeks server-side) with the standard dealer convention (dealers long customer-sold calls, short customer-bought puts → net: calls contribute +γ, puts −γ). Gamma dollarized per 1% move: Σ ±γ × OI × contract multiplier × S² × 1%. Report SPX/NDX complex, top single names, aggregate; render labeled *"OI-convention GEX"*. Coarser than flow-signed (levels less trustworthy; changes are the signal — the standing caveat applies doubly). Upgrade path back to flow-signed §5.3-original if OPRA quotes are ever added.

**5.4 Box-spread yield (LV7).** For SPX expiries ~1M/3M: box = (C(K1)−C(K2)) − (P(K1)−P(K2)) with wide, liquid strikes; implied rate = ln((K2−K1)/box price)/T. Use NBBO midpoints, size-filter quotes, median across strike pairs. Spread over matched-maturity SOFR/OIS. Cross-check vs boxtrades.com daily print.

**5.5 Synthetic financing index (LV9).** Per name: from put-call parity, implied forward F = K + (C−P)·e^{rT} at the ATM strike; implied financing = ln(F/S)/T − dividend yield (BBG dividend forecast). Spread over LV7 box rate = name-specific leverage-demand premium. Composite: option-volume-weighted across top-25 retail names (from RF3), reported with per-name detail.

**5.6 Fed nowcasts (OP2/OP4).** Equity cohorts: last DFA level × cumulative SPX total return since quarter-end; cohort shares frozen; flagged "nowcast" until the next official print (next: Q2 2026 on 2026-09-11). Cash ratio: numerator from ICI weekly MMF (retail split) + H.8 deposits interpolated; denominator rolled forward with a 60/40 market-return blend. Both series render the official prints as solid and the nowcast segment as dashed (§A9: simulated/estimated segments labeled on the visual).

**5.7 Realized retail toll (LV12).** **DEFERRED 2026-07-08** — requires signing each small-lot trade (paid vs received), which needs OPRA quotes; the options plan is trades-only (CIO decision to not license options quotes). No honest substitute exists (an unsigned "all-premium" variant would redefine the metric). Revisit if options quotes are added. Original method, preserved for that day: for each small-lot trade, premium paid (buys) or received (sells); hold-to-expiry settlement from underlying close at expiry; daily toll = Σ premium paid − Σ settlement value, by expiry cohort; label *hold-to-expiry toll*, upper-bound estimate; trend is the signal.

**5.8 Leveraged-ETF financing residual (LV13).** For each 2x/3x fund: daily NAV return − [L × index return − fee/252]; rolling 20d mean of residual × 252 / (L−1) ≈ embedded financing spread. Report median across the major leveraged complex; compare to LV7/LV8.

**5.10 Vol-surface engine (feeds LV5, LV9, LV10, VC4 member view; Phase 3).** *Largely superseded 2026-07-08:* Massive's EOD option snapshots carry per-contract IV + full greeks computed server-side (verified live), so LV10/VC4/VC6/LV4/LV5-OI read the snapshot directly — no in-house inversion needed. The engine below is retained for (a) LV9's forward/rate solve, and (b) §7 validation of snapshot IV vs BBG OVDV on the VC6 10-name set. Original design, where still used — per underlying, per snapshot (EOD trade prices in lieu of NBBO midpoints, size-filtered):
- **Forward & rate:** solve the ATM forward `F` and implied rate from put-call parity across the two nearest strikes to spot (consistent with §5.5); dividend yield from BBG dividend forecast.
- **IV inversion:** Black-76 on forwards; invert each option's midpoint to IV via a bounded solver (Brent), discarding quotes with crossed/locked or stale markets, zero bid, or width > a per-liquidity threshold.
- **Surface points:** interpolate IV at fixed deltas (25Δ put, ATM, 25Δ call) and fixed tenors (1M, 3M) per name, in delta space, using a monotone spline; flag extrapolation.
- **Greeks/GEX (LV5):** analytic Black-76 gamma × contract multiplier × S² × 1% for dollar-gamma; sign per §5.3.
- **Outputs consumed by:** LV10 (25Δcall−ATM), VC4 member-inverted-skew breadth, LV9 (synthetic financing uses the forward/rate solve), VC6 (cross-check vs the 10-name BBG set — a QC gate, §7).

Validation: reconcile engine IV vs BBG OVDV on the VC6 10-name set within ±1 vol point before the member-breadth views render as trusted (§7).

## 6. Rendering & house style (§A9 applied to HTML)

- **Page:** background Light Grey `#EFEFEF`; panels as White `#FFFFFF` cards; headings Near-Black `#1E1E1E`; body Dark Grey `#5A5A5A`; lead/callout strip in Avos Green `#4B5C3E`. Header: `avos` wordmark (use `LOGOTYPE-1.png` from `style_guides/`; never retype), title, run date, data-as-of block.
- **Type:** Inter Tight for all page text; **Roboto inside charts** (both embedded as woff2 or system-fallback stacked — no external font fetch).
- **Series palette, in order:** Purple `#7F77B2` · Brown `#A66C3D` · Green `#4F5C41` · Sage `#A6B296` · Blue-Lavender `#9AA3C2` · Slate `#637F8F`. Avos-computed series lead in Green; external benchmark/comparison series in Brown.
- **Charts:** direct series labels next to lines where space permits; gridlines grey, baseline black; 4px data lines (screen scale); every chart titled with its metric ID + plain-language name; every chart carries its as-of and source tag. Estimated/nowcast segments dashed and labeled (§A9.5 spirit).
- **Chart library:** ECharts or Chart.js, whichever the builder prefers, **inlined** into the HTML at render time. One library for the whole page.
- **Layout (revised 2026-07-09, CIO feedback):** render order departs from §4's registry grouping: **Retail Flows · Volatility and Correlation · Leverage · Flows (ETF flows + MOC, split from Ownership & Passive) · Ownership · Credit (split from Market Health) · Internals (breadth/cross-asset/sentiment/tape structure) · Issuance · Other (= Structure & Concentration)**. §4's metric IDs and tables are unchanged — only display grouping/order moved (registry `panel` keys in `config.py` are authoritative). Each panel opens with a row of stat tiles (value + Δ + percentile) then charts; a top "Today" strip surfaces the 6–8 largest percentile moves across all panels since the prior run.
- **Provenance (§A8):** page footer lists build version (git hash), run timestamp ET, per-source pull status, and links each metric ID back to this spec section.

## 7. Validation & QC (definition of done, per §A11/§A12 adapted)

1. **Deterministic rerun:** same stored inputs → byte-identical HTML (excluding run timestamp).
2. **Capture-factor calibration:** the dollar retail metrics (RF2, RF10, RF3 bars) render as trusted only once the capture factor is FITTED against FINRA reported retail-wholesaler volume over at least `config.RETAIL_FIT_MIN_WEEKS` overlapping weeks; below that they fall back to the assumed factor and carry an explicit "uncalibrated" badge. (Replaced the RF9/RTAT gate 2026-08: RTAT is itself BJZZ-derived and so likely carries the same institutional contamination, making it unfit as an independent anchor.) Separately, an **arithmetic-impossibility alarm** flags any liquid symbol-day whose scaled net exceeds 50% of that symbol's own consolidated volume — it alarms, it never corrects.
3. **Cross-checks wired as tests:** LV7 vs boxtrades.com (±25bp); MH2 BBG vs FRED ICE BofA (±10bp); LV1 vs LV2 SPX subset (Phase 3); OP5 vs ICI weekly direction; **§5.10 vol engine vs BBG OVDV on the VC6 10-name set (±1 vol pt) before member-breadth views (LV10, VC4) render trusted**.
4. **Sanity bounds per metric** (config-declared): e.g., shares sum to ≤ 100%, spreads ≥ 0 where structural, ratios within historical ×5 band → violations flag, never silently clip.
5. **Every displayed number** has as-of date + source tag; every proxy labeled proxy; every nowcast dashed. No unlabeled estimate ships (§A3).
6. **Unit tests:** classifier on synthetic prints (known subpenny/midpoint cases); box-yield on a hand-computed example; GEX signs on constructed trades; percentile function edge cases.
7. **Self-QC pass** before first CIO delivery: all Phase-1 tiles populated or explicitly n/a with reason; glossary section in the HTML footer defines every acronym on the page (OAS, GEX, 0DTE, DTE, VRP, MOC, TRF, NBBO, DFA, PMMS, HHDC…), per §A12.

## 8. Build order

1. **Phase 1 (Bloomberg + free):** scaffold, layered store, render skeleton with house style → SC(1–4) / OP(1–7) / VC(1–3,5,6, SPX-level 4) / MH / IS / LV(1,6,7,8,11,13,14,15,16) → first full HTML, deployed to `lens.avos.co/market-conditions`. SC5 shows the BBG top-50 fallback until Phase 2. *Gate: dashboard useful daily with zero new data spend.*
2. **Phase 2 (Massive stocks):** trades+NBBO ingestion, RF1–RF6, RF10, capture-factor calibration, OP8, MH9, SC5 (full member set), 2016→ backfill. *Gate: capture factor fitted against FINRA, not assumed.*
3. **Phase 3 (Massive options/OPRA):** flat-file aggregation, §5.10 vol engine, LV2–LV5, LV9, LV10, LV12, VC4 member-breadth view, RF7–RF8. *Gates: LV1 vs LV2 SPX cross-check passes; engine IV vs BBG OVDV on the VC6 10-name set within ±1 vol pt.*
4. Each phase folds into the existing page — same tiles, new rows — never bolt-on sections (§A12).

## 9. Out of scope / future

- Nasdaq RTAT/UREF paid tiers (revisit if firm pricing confirms cheap), VandaTrack, Cboe DataShop — the source map xlsx carries the full alternatives analysis.
- Intraday refresh (UREFINT-class), alerting/scheduled delivery, portfolio overlay vs Addepar holdings — natural extensions after the daily loop is trusted.

## 10. Glossary & sources

**Glossary:** BJZZ — Boehmer-Jones-Zhang-Zhang subpenny retail identification (JF 2021) · quote-midpoint signing — Barber et al. correction (JF 2024) · OAS — option-adjusted spread · GEX — dealer gamma exposure · 0DTE — same-day-expiry option · box spread — synthetic riskless bond from options · TRF — FINRA Trade Reporting Facility · NBBO — national best bid/offer · DFA — Fed Distributional Financial Accounts · PMMS — Freddie Mac Primary Mortgage Market Survey · HHDC — NY Fed Household Debt & Credit report · MOC — market-on-close · VRP — variance risk premium.

**Sources:** Citadel Securities "1H 2026 Market Structure & Flows" (citadelsecurities.com, 2026-06-30) · Boehmer, Jones, Zhang, Zhang, *Tracking Retail Investor Activity*, JF 2021 · Barber, Huang, Jorion, Odean, Schwarz, *A (Sub)penny for Your Thoughts*, JF 2024 · Fed Z.1/DFA release calendar (federalreserve.gov) · FINRA OTC Transparency (finra.org; developer.finra.org) · Cboe index/dispersion pages (cboe.com) · Massive docs (massive.com/docs) · Nasdaq Data Link RTAT docs (data.nasdaq.com/databases/RTAT) · session source map: `Citadel_1H26_Chart_Data_Source_Map.xlsx`.
