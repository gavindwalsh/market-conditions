# US Market Conditions — methodology appendix

*This page is generated from the dashboard's compute layer, so every definition
here ties directly to the code that produces the chart.*

This appendix explains how each chart on the dashboard is built — the exact
inputs, the formula, and the conventions and caveats behind the number. It is
written to stand on its own: you should not need any other document to follow it.

**How to read each entry.** Every metric is laid out the same way:

- **What it shows** — the question the chart answers, and how to read it.
- **How it's computed** — the inputs, the formula, and every term defined.
- **Caveats** — scaling, lags, coverage, and what the metric is *not*.

Each entry also carries a source-and-cadence line and, where relevant, a status
badge (for example *uncalibrated* or *proxy*) whose meaning is spelled out in
that entry's caveats. Conventions shared across many charts are collected
immediately below; individual entries refer back to them by name rather than
repeating them. Full citations for every external source appear under
References at the foot of the page.

## Shared methodology

### Retail identification and scaling
Retail trades are picked out of the consolidated tape by their execution
signature: off-exchange (TRF) prints that receive sub-penny price improvement,
the hallmark of a wholesaler internalizing a retail order. Each identified
trade is signed as a buy or a sell by comparing its price to the prevailing
national best bid/offer (NBBO) midpoint — above the midpoint is a buy, below is
a sell, and trades exactly at the midpoint are left out because their direction
is ambiguous.

This signature captures only about a third of true retail activity — the part
that leaves the sub-penny fingerprint. Dollar-denominated retail metrics are
therefore multiplied by a scale factor of ×3.0 to estimate market-wide totals.
That factor is provisional: it is flagged *uncalibrated* until it has been
fit empirically against Nasdaq's Retail Activity Tracker (RTAT), the gate being
a trailing 60-day correlation of at least 0.6 between our identified flow and
RTAT. Ratio and slope metrics — retail concentration and buy-the-dip
sensitivity — are unaffected by the scale factor and are shown unscaled.

### Small-lot options proxy
Options trades smaller than 10 contracts are used as a *proxy* for retail
options activity. This is an observed empirical regularity — small tickets
cluster in retail-favored names and behave like retail flow — not a positive
identification, and every chart built on it is labeled a proxy. The proxy
reconciles to published retail-flow totals.

### FINRA participation anchor
FINRA publishes weekly non-ATS (over-the-counter) share volume for each
reporting firm. We use only the T1 and T2 reporting tiers; OTCE (pink-sheet)
volume is excluded because it has no counterpart in our NMS-tape denominator.
FINRA's per-firm rows count both sides of an internalized trade, so its level
runs roughly twice a one-sided participation share. We rescale it onto our own
participation definition with a single multiplicative constant, `k = mean(ours ÷
FINRA)` measured over the weeks the two series overlap, and render the rescaled
FINRA line as the official trend anchor. Our own classifier estimate extends
the series through the two-to-four-week window before FINRA publishes.

### ETF flow universe
ETF-flow charts cover a curated universe of roughly 53 funds — the largest of
each complex — rather than the entire US ETF market; the coverage is labeled on
the chart. Flows use the shares-outstanding method: `flow = Δshares × NAV`, the
day-over-day change in shares outstanding times net asset value. This isolates
genuine creation and redemption activity from price moves.

### Realized-volatility conventions
Realized volatility is computed from the daily returns of index closes over a
rolling trading-day window, annualized by the square root of the number of
trading days in a year, and expressed in percentage points. Legs that tie to a
Bloomberg Terminal field follow Bloomberg's convention — log returns and √260
annualization — so the chart values match the Terminal (for example, the
360-day realized legs reproduce the VOLATILITY_360D field). Where a chart
compares implied and realized volatility at the same horizon, the realized
window is chosen to match the implied tenor (21 trading sessions ≈ one month);
where the two windows differ, the entry says so. Each entry states its exact
window and annualization factor.

## Per-metric notes

### VC7 — SPX implied (VIX) vs realized vol
*Source: BBG VIX + index closes · cadence: daily*

**What it shows.** SPX's 30-day implied volatility (VIX) against the volatility SPX has actually realized. Implied sitting above realized is the normal state — the variance risk premium that sellers of options earn; the gap narrowing, or the two lines crossing, marks stress.

**How it's computed.** The implied leg is the VIX index — 30-day, skew-inclusive expected volatility. The realized leg is 360-day realized volatility built per the realized-volatility conventions above: daily log returns of index closes, a rolling 360-trading-day window, annualized by √260 to tie to Bloomberg's VOLATILITY_360D field. The tile ranks VIX against its trailing history.

**Caveats.** The implied tenor (30 days) is far shorter than the realized window (360 days), so part of the level gap reflects that tenor mismatch rather than the risk premium alone — read the direction and size of changes in the gap, not the raw level.

### VC8 — NDX implied (VXN) vs realized vol
*Source: BBG VXN + index closes · cadence: daily*

**What it shows.** NDX's 30-day implied volatility (VXN) against the volatility NDX has actually realized. Implied sitting above realized is the normal state — the variance risk premium that sellers of options earn; the gap narrowing, or the two lines crossing, marks stress.

**How it's computed.** The implied leg is the VXN index — 30-day, skew-inclusive expected volatility. The realized leg is 360-day realized volatility built per the realized-volatility conventions above: daily log returns of index closes, a rolling 360-trading-day window, annualized by √260 to tie to Bloomberg's VOLATILITY_360D field. The tile ranks VXN against its trailing history.

**Caveats.** The implied tenor (30 days) is far shorter than the realized window (360 days), so part of the level gap reflects that tenor mismatch rather than the risk premium alone — read the direction and size of changes in the gap, not the raw level.

### VC9 — SPX 10% OTM call/put IV
*Source: BBG moneyness IV + index closes · cadence: daily*

**What it shows.** The implied volatility of 10%-out-of-the-money puts (90% moneyness) and calls (110% moneyness) on SPX, with realized vol for reference. The put wing sits above the call wing in normal markets — the standing cost of downside protection. The distance between the two wings is the skew, and it widens as hedging demand rises.

**How it's computed.** Both wings are 30-day implied vols read at fixed moneyness — strikes set at 90% and 110% of spot. The realized leg is 360-day realized volatility built per the realized-volatility conventions above (daily log returns of index closes, rolling 360-trading-day window, annualized by √260 to tie to Bloomberg's VOLATILITY_360D field). The tile ranks the put wing.

**Caveats.** These are moneyness-based wings, not delta-based: the strikes are fixed percentages of spot rather than a fixed option delta, so they do not re-strike as volatility changes. As with the VIX/VXN charts, the 30-day wings are compared against a 360-day realized window, so part of any level gap is the tenor difference.

### VC10 — NDX 10% OTM call/put IV
*Source: BBG moneyness IV + index closes · cadence: daily*

**What it shows.** The implied volatility of 10%-out-of-the-money puts (90% moneyness) and calls (110% moneyness) on NDX, with realized vol for reference. The put wing sits above the call wing in normal markets — the standing cost of downside protection. The distance between the two wings is the skew, and it widens as hedging demand rises.

**How it's computed.** Both wings are 30-day implied vols read at fixed moneyness — strikes set at 90% and 110% of spot. The realized leg is 360-day realized volatility built per the realized-volatility conventions above (daily log returns of index closes, rolling 360-trading-day window, annualized by √260 to tie to Bloomberg's VOLATILITY_360D field). The tile ranks the put wing.

**Caveats.** These are moneyness-based wings, not delta-based: the strikes are fixed percentages of spot rather than a fixed option delta, so they do not re-strike as volatility changes. As with the VIX/VXN charts, the 30-day wings are compared against a 360-day realized window, so part of any level gap is the tenor difference.

### VC3 — Vol term structure (VIX/VIX3M)
*Source: BBG VIX/VIX3M Index · cadence: daily*

**What it shows.** The slope of the S&P 500's implied-volatility term structure — near-term expected vol (VIX, 30-day) divided by 3-month expected vol (VIX3M). Above 1 the curve is inverted: near-term fear exceeds the medium term, the classic stress signature. Below 1 the curve is in contango (upward-sloping), the calm-market norm that makes selling short-dated vol profitable to carry.

**How it's computed.** The daily ratio `VIX ÷ VIX3M` of closing index levels. The tile ranks the ratio against its trailing history.

**Caveats.** A ratio of two Cboe indices measuring 30-day and 3-month expected S&P 500 volatility. Per-name term-structure slopes for the largest single names are a possible future extension.

### VC6 — 3M IV by sector basket
*Source: BBG per-name 3M ATM IV · cadence: daily*

**What it shows.** Three-month at-the-money implied volatility, averaged across the names in each sector basket (semiconductors, hyperscalers, healthcare, staples). Because every basket is built the same way, the levels are directly comparable — you can read straight off the chart which part of the market options traders expect to be most volatile.

**How it's computed.** For each name we take Bloomberg's 3-month ATM implied vol, then equal-weight average across the basket: `IV_basket = mean(IVᵢ)`. A basket renders on a given day only if at least half its names — and no fewer than three — have data, so a thin feed can't distort the average. The tile ranks the semis basket.

**Caveats.** This is equal-weight single-name implied vol, deliberately not sector-ETF implied vol: an ETF's option vol embeds the correlation across its holdings and prints materially lower, which would not be comparable to a basket of individual names.

### VC1 — Implied correlation
*Source: BBG COR1M/COR3M Index · cadence: daily*

**What it shows.** The average correlation between S&P 500 members that index-options prices imply for the coming month. Low readings mean index options are cheap relative to single-name options — the market expects stocks to move on their own news (a dispersion, stock-picker's regime). High readings mean stocks are priced to move together, an index-like, macro-driven tape.

**How it's computed.** We plot Cboe's published COR1M (1-month) with COR3M (3-month) alongside; the tile ranks COR1M against its own trailing history. Cboe derives implied correlation from the identity that links index variance to member variances: index options price the index variance `σ_index²`, single-name options price each member's variance `σᵢ²`, and the implied average correlation `ρ` is the value that reconciles the two — `σ_index² = Σ wᵢ² σᵢ² + ρ · ΣΣ wᵢ wⱼ σᵢ σⱼ` (the double sum runs over distinct member pairs, `wᵢ` are index weights). Solving for `ρ` gives the index.

**Caveats.** This is an implied, forward-looking measure read from options prices, not correlation that has actually occurred. The gap between implied and realized correlation — the correlation risk premium — is charted separately as VC2.

### VC2 — Implied − realized correlation spread
*Source: BBG COR1M − Massive member returns · cadence: daily*

**What it shows.** How far implied correlation (VC1's COR1M) sits above the correlation S&P 500 members actually realized. That excess is the correlation risk premium — what dispersion sellers (short index vol, long single-name vol) are paid. A wide positive spread means dispersion trades were richly compensated.

**How it's computed.** We compute realized average pairwise correlation from member returns using the index-variance identity, solved for a single correlation: `ρ = (σ_idx² − Σ wᵢ² σᵢ²) / ((Σ wᵢ σᵢ)² − Σ wᵢ² σᵢ²)`, where `σ_idx` is the 21-day rolling volatility of the S&P 500, `σᵢ` the 21-day rolling volatility of member `i`, and `wᵢ` its index weight. The numerator strips each stock's own variance out of index variance, leaving the covariance term; the denominator normalizes by the same quantity under an all-pairs-equal-correlation assumption, so `ρ` is the one correlation that reproduces the observed index variance. The plotted spread is `COR1M − 100·ρ`; a realized line is shown for context. The 21-day window follows the realized-volatility conventions above, matched to COR1M's one-month tenor.

**Caveats.** Realized correlation is clipped to the [−1, 1] range before scaling. The calculation uses today's index membership and weights applied backward through history, so older values carry a survivorship bias — the same limitation noted for the realized-dispersion chart.

### LV11 — Variance risk premium
*Source: BBG VIX/VXN vs realized · cadence: daily*

**What it shows.** What a month of implied volatility turned out to cost versus what actually came to pass — one-month implied vol (VIX for the S&P 500, VXN for the Nasdaq-100) minus the volatility realized over the month that followed. Positive means option buyers overpaid and sellers earned the premium; negative means realized vol overshot what was priced. Because it looks forward to realized outcomes, the series necessarily ends about a month ago.

**How it's computed.** For each index, `VRP = IV_1M − RV_next21`, where `IV_1M` is the implied-vol index and `RV_next21` is realized volatility over the subsequent 21 trading sessions — daily returns, a rolling 21-day standard deviation, annualized by √252, then shifted back so each day is paired with the volatility that came after it. The tile ranks the S&P 500 series.

**Caveats.** The forward-looking realized leg cannot be computed for the most recent ~21 sessions, so by construction the line stops about one month short of today.

### RF1 — Retail net flow — daily (est. total)
**Status: ×3 est. · uncalibrated**
*Source: Massive tape (classifier) · cadence: daily*

**What it shows.** Estimated net dollar flow from retail traders each day — buys minus sells — scaled to a whole-market figure. Positive bars are net buying, negative bars net selling; together they track the daily push and pull of the retail crowd.

**How it's computed.** Every retail trade is identified and signed by the quote-midpoint classifier described in "Retail identification and scaling" above — off-exchange sub-penny prints, signed buy or sell against the NBBO midpoint. Each day's net identified dollars are summed and multiplied by the ×3 scale factor to estimate the market-wide total: `RF1 = 3.0 × Σ(signed identified retail $)`, plotted in billions of dollars per day.

**Caveats.** The ×3 factor is provisional, so the series carries an *uncalibrated* badge until it is fit against Nasdaq's Retail Activity Tracker and clears the calibration gate (see the shared section). Trades executing exactly at the midpoint are excluded because their direction is ambiguous, so this is a net-direction estimate, not a gross-volume one — for gross retail dollar volume see RF10.

### RF10 — Retail dollar volume — weekly (est. total)
**Status: ×3 est. · uncalibrated**
*Source: FINRA weekly OTC × Massive tape · cadence: weekly*

**What it shows.** Estimated total dollars retail traded each week — gross activity, buys and sells added together, not a net direction. For the net buy-minus-sell view see RF1.

**How it's computed.** Our recent weeks are the identified retail dollars ×3-scaled to an estimated total and summed by week (weeks with fewer than three trading days dropped). The history is anchored to FINRA: FINRA T1+T2 non-ATS share volume is turned into dollars using that week's volume-weighted tape price (`$/share`), then rescaled onto our estimated-total definition per the FINRA participation anchor in Shared methodology. The most recent weeks — inside FINRA's roughly four-week publication lag — use the ×3 classifier estimate. The two segments are spliced into one series.

**Caveats.** This is gross volume, not net flow — FINRA's weekly OTC data carries no buy/sell split, which is why RF1 remains the only net view. The ×3 factor is provisional, so the series carries an *uncalibrated* badge until it clears the RTAT calibration gate (see Shared methodology).

### RF2 — Retail participation — weekly (est. total)
**Status: ×3 est. · uncalibrated**
*Source: FINRA weekly OTC × Massive tape (classifier) · cadence: weekly*

**What it shows.** Retail's share of total tape volume each week, scaled to a market-wide estimate. A rising line means retail is accounting for a larger slice of everything that trades.

**How it's computed.** Our weekly reading is identified retail dollars ÷ total tape dollars, ×3-scaled to an estimated total, averaged over the week (weeks with fewer than three trading days are dropped). The official trend anchor, though, is FINRA's own data: weekly non-ATS (T1+T2) share volume divided by our tape volume, rescaled onto our definition per the FINRA participation anchor described in Shared methodology. Because FINRA T1 tiers publish about two weeks ahead of T2, a T1-only segment is rescaled separately to bridge that gap, and our own ×3 classifier estimate fills the most recent weeks FINRA has not yet published. The chart shows the three segments in sequence: FINRA-anchored (solid), the T1-only bridge, then our estimate.

**Caveats.** The classifier captures only about a third of retail, so the level depends on the provisional ×3 factor and the series carries an *uncalibrated* badge until that factor clears the RTAT calibration gate (see Shared methodology). The tile reads the last complete week, excluding the in-progress one.

### RF3 — Retail concentration
**Status: classifier floor**
*Source: Massive tape × SPX membership · cadence: daily*

**What it shows.** Where retail dollars pile up. The bars are estimated total retail dollars flowing into the ten largest S&P 500 names; the lines are the share of all retail dollars going to those top-10 names and to semiconductors. A rising line means retail is crowding into a narrower set of names.

**How it's computed.** Bars: identified retail dollars in the top-10 names, ×3-scaled to an estimated total ($B). Lines: top-10 retail $ ÷ all retail $, and semis retail $ ÷ all retail $ — shares computed on identified dollars only. Index membership is the latest Bloomberg S&P 500 snapshot; semis are GICS sub-industry 453010. See Retail identification and scaling above for the classifier.

**Caveats.** The bars depend on the provisional ×3 factor; the share lines are ratios and so are scale-invariant. Membership is applied backward through history, so the name list carries a survivorship bias. The *classifier floor* badge marks the metric provisional until calibration.

### RF4 — Buy-the-dip sensitivity
**Status: classifier floor**
*Source: Massive tape × BBG SPX · cadence: daily*

**What it shows.** How hard retail leans into weakness — the dollars of net retail buying that arrive per 1% S&P 500 decline. A positive, rising reading means retail buys harder as the market falls (dip-buying); a negative reading means retail sells into declines.

**How it's computed.** A rolling ordinary-least-squares slope of daily identified retail net flow (in $B) regressed on the S&P 500 daily percent return, sign-flipped so that a positive value means buying into declines: `RF4 = −slope(net flow $B on SPX % return)`. Two contemporaneous windows are shown — a 63-day (3-month) primary trend and a 21-day (1-month) context line that reacts faster but is roughly 4× noisier. See Retail identification and scaling above for the underlying classifier.

**Caveats.** Built on the identified floor of retail flow, not the ×3-scaled total — but as a regression slope its shape is scale-invariant. It unlocks only once at least 63 signed days carrying an S&P 500 return have accumulated, and carries the *classifier floor* badge marking it provisional until calibration.

### RF7 — Small-lot options premium (proxy)
*Source: Massive OPRA trades · cadence: daily*

**What it shows.** The dollar premium spent on small (fewer than 10 contracts) option trades each day — a proxy for retail options activity — with that small-lot premium also shown as a share of all option premium. A rising line means retail is leaning harder into options.

**How it's computed.** Small-lot premium is summed each day and smoothed with a 5-day trailing mean; the share is small-lot premium ÷ total option premium. The under-10-contract cutoff is the retail proxy described in Small-lot options proxy above.

**Caveats.** This is a proxy — an observed regularity, not a positive identification of retail. Days when the feed reports zero premium are drawn as gaps rather than dropped to zero, since they are feed artifacts, not genuine days of no activity.

### RF8 — Small-lot call share / semi premium
*Source: Massive OPRA trades · cadence: daily*

**What it shows.** How much of retail's small-lot option spending goes to calls versus puts — the call share is a directional, speculative read — shown alongside the small-lot premium spent in semiconductors, a perennial retail favorite.

**How it's computed.** Call share is small-lot call premium ÷ small-lot total premium; the semis line is small-lot premium summed across the top semiconductor names. Both are 5-day trailing means. The under-10-contract cutoff is the retail proxy described in Small-lot options proxy above.

**Caveats.** A proxy, not an identification of retail flow.

### LV3 — Volume by DTE bucket
*Source: Massive OPRA trades · cadence: daily*

**What it shows.** How option volume splits by time to expiry — same-day (0 DTE), 1–5, 6–30, and more than 30 days — as weekly stacked bars that sum to 100%. A growing 0 DTE stack is the signature of short-dated, retail-heavy speculation.

**How it's computed.** Each day, contract volume is sorted into the four days-to-expiry buckets; each bucket's daily share of total volume is then averaged over the Friday-ended week. The tile shows the latest 0 DTE share.

**Caveats.** This is whole-market OPRA volume, not retail-only, and the shares are of contract count, not premium dollars.

### LV15 — L4: FINRA margin debt (% of GDP)
*Source: FINRA margin statistics · FRED GDP · cadence: monthly*

**What it shows.** Total debit balances in securities margin accounts — the outstanding stock of investor leverage — as a percentage of nominal GDP, so the level is comparable across decades rather than drifting up with the size of the economy. A classic risk-appetite gauge: sharp rises tend to accompany late-cycle exuberance.

**How it's computed.** FINRA's monthly margin statistics (debit balances, in billions of dollars) divided by nominal GDP — FRED series GDP, quarterly in billions at a seasonally-adjusted annual rate, carried forward to each month — times 100. Margin-debt history stitches a 1997–2021 archive to the live FINRA page table.

**Caveats.** Margin debt is reported with roughly a three-week lag after month-end; GDP is quarterly, so the denominator steps at quarter boundaries.

### LV13 — L3: Leveraged-ETF financing residual
**Status: new methodology**
*Source: BBG NAV × total-return index · cadence: weekly*

**What it shows.** The financing spread buried inside leveraged-ETF returns — the cost these funds effectively pay on their embedded swaps, versus SOFR. It is the recurring toll of levered index exposure.

**How it's computed.** The return model is `nav_ret = L·r_tr − fee/252 − (L−1)·fin/252`, so embedded financing backs out as `fin = −[nav_ret − (L·r_tr − fee/252)]·252/(L−1)`. Crucially `r_tr` is the underlying's TOTAL return (SPTR / XNDX), not price return: the funds' swaps earn the index total return, so using price-only return would leave the dividend yield in the residual and read as spurious negative financing. We take a 60-day mean, the median across TQQQ/QLD/UPRO/SSO, minus same-day SOFR, in basis points, with a flat 0.9% fee assumed.

**Caveats.** New-methodology badge. The residual amplifies tiny NAV-versus-index tracking errors into large financing swings, so the level is noisy — read the trend, not the point. Long 2×/3× S&P and Nasdaq funds only (inverse funds' estimator has the opposite sign).

### LV6 — Leveraged-ETF rebalance notional
*Source: BBG AUM × Massive moves · cadence: daily*

**What it shows.** How much leveraged ETFs must trade to keep their exposure on target as the market moves — both the structural capacity per 1% index move and the realized forced end-of-day flow. Large capacity means these funds can amplify late-day moves.

**How it's computed.** Rebalance capacity per 1% move is `Σ AUM·|L·(L−1)|·0.01`; realized forced flow is `Σ AUM·L·(L−1)·(underlying daily return)`, both summed across 17 major leveraged funds (a curated universe — see ETF flow universe in Shared methodology). `L` is each fund's leverage factor. Because the forced flow chases the day's move into the close, it flips sign every day; we plot its magnitude on a 5-day mean as rebalance *intensity* rather than the sign-flipping signed series.

**Caveats.** Covers the curated leveraged universe, not every leveraged ETF. The flow line is a magnitude, so it shows how much rebalancing there is, not its direction.

### LV8 — L1: ES roll implied financing
*Source: BBG ES1/ES2 + FRED SOFR · cadence: daily*

**What it shows.** The implied cost of index leverage read from the S&P 500 (ES) futures roll, quoted as a spread over SOFR. A rich (positive) spread means leverage demand is paying up to be long the index via futures.

**How it's computed.** The calendar between the front and second ES contracts implies a financing rate, `ln(ES2/ES1)/Δt` with `Δt ≈ 0.25y` (91/365). Because holding futures forgoes dividends, we add back the trailing S&P 500 dividend yield — estimated from the one-year SPTR-minus-SPX return drift — and subtract SOFR, leaving a spread in basis points.

**Caveats.** On generic-contract roll days the front/second ratio explodes (verified swings of several hundred bp around quarterly expiry), so days within ±2 business days of the March/June/September/December third Friday are dropped, backed by a ±150bp-versus-60-day-median filter.

### OP5 — ETF net flows
*Source: BBG Δshares × NAV · cadence: daily*

**What it shows.** Cumulative net flows into the ETF universe over the year to date, with the last few years overlaid so the current year's pace stands against its predecessors.

**How it's computed.** Daily net flow via the shares-outstanding method (`flow = Δshares × NAV`, see ETF flow universe in Shared methodology), summed across the universe and accumulated within each calendar year. Each year is plotted on a common day-of-year axis so the curves line up. The tile is the trailing 20-day sum.

**Caveats.** Covers the curated 53-fund universe — the largest of each complex — not every US ETF.

### OP6 — ETF flows by category
*Source: BBG Δshares × NAV · cadence: daily*

**What it shows.** Weekly net ETF flows stacked by category (equity, leveraged, and so on) — creations up, redemptions down. It shows where money is rotating across the fund complex.

**How it's computed.** The same shares-outstanding flows (see ETF flow universe in Shared methodology) summed per category per Friday-ended week, with about three years shown. The tile is the latest complete leveraged-category week.

**Caveats.** Covers the curated 53-fund universe, not every US ETF; the in-progress week is dropped.

### OP7 — Leveraged ETF AUM
*Source: BBG FUND_TOTAL_ASSETS · cadence: daily*

**What it shows.** Total assets in the leveraged-ETF complex, with the single-stock-leveraged slice broken out — a gauge of how much levered exposure investors are holding, and how fast the single-stock corner is growing.

**How it's computed.** Bloomberg FUND_TOTAL_ASSETS summed across the curated leveraged universe (see ETF flow universe in Shared methodology); the single-stock line is the funds that track individual names rather than QQQ, SPY, or SMH.

**Caveats.** Covers the curated leveraged universe, not every US leveraged ETF.

### OP1 — Household equity by wealth cohort
*Source: FRED DFA [verified 2026-07-08] · cadence: quarterly*

**What it shows.** How much corporate equity US households own, split into four wealth cohorts — bottom 50%, 50th–90th, 90th–99th, and top 1%. The tile is the bottom-50%'s share of the total, a direct read on how concentrated equity ownership is.

**How it's computed.** Federal Reserve Distributional Financial Accounts (DFA) equity levels, shown in trillions of dollars per cohort; the tile ranks the bottom-50% share of aggregate household equity against its own history.

**Caveats.** The DFA is released about 11 weeks after quarter-end; OP2 is the daily nowcast that bridges that lag.

### OP2 — Household equity — nowcast (% of GDP)
*Source: FRED DFA × BBG SPTR · FRED GDP · cadence: daily*

**What it shows.** How large US households' equity holdings are relative to the economy, brought up to date daily. The official quarterly print is rolled forward with the S&P 500's total return, so you can see roughly where household equity stands today rather than a quarter ago. Because the level is scaled by GDP rather than left in dollars, the full history back to 1989 is directly comparable — today's reading can be read against the 2000 and 2007 peaks instead of only against the last few years.

**How it's computed.** The four DFA wealth-cohort equity levels are summed (billions of dollars) and divided by nominal GDP — FRED series GDP, quarterly in billions at a seasonally-adjusted annual rate — times 100. DFA levels are dated to quarter-end and paired with their own quarter's GDP. For the nowcast, the last dollar level is grown by the S&P 500 total return since quarter-end, holding each cohort's share fixed, and divided by the most recent published GDP print. The official prints are drawn solid; the rolled-forward segment is dashed.

**Caveats.** A nowcast, not data — cohort shares are frozen between DFA prints (the next, Q2 2026, is expected 2026-09-11). The current quarter's GDP is not published yet, so the nowcast holds the last GDP print flat; while the economy grows, that slightly overstates the ratio. History starts in 1989 Q3, the first DFA observation.

### OP3 — Household cash % of financial assets
*Source: FRED Z.1 B.101 [verified 2026-07-08] · cadence: quarterly*

**What it shows.** How much of household financial assets sit in cash-like holdings — a dry-powder and risk-appetite gauge. A rising line means households are holding back; a falling one means cash is being put to work.

**How it's computed.** `(checkable deposits + currency + time & savings deposits + money-market funds) ÷ total household financial assets`, from the Fed's Z.1 Financial Accounts (table B.101), quarterly.

**Caveats.** Quarterly; the weekly nowcast (OP4) that would bridge the reporting lag is pending ICI and Fed H.8 data.

### OP9 — Personal saving rate
*Source: FRED PSAVERT · cadence: monthly*

**What it shows.** Personal saving as a share of disposable income — how much of what they earn households are setting aside. A lower rate can signal confidence or financial stretch; a higher one, caution.

**How it's computed.** The BEA's PSAVERT series — personal saving ÷ disposable personal income — monthly, seasonally adjusted.

**Caveats.** The y-axis is capped below the 2020–21 stimulus spikes so they run off-chart rather than flattening the rest of the history.

### OP10 — Personal saving (% of GDP)
*Source: FRED PMSAVE · FRED GDP · cadence: monthly*

**What it shows.** How much households are saving relative to the size of the economy — the same household behavior as the saving rate (OP9), but measured against GDP rather than against disposable income. Scaling by GDP keeps the whole history comparable: the dollar level rises with the economy, so a 1960s reading and a 2020s reading cannot be read side by side.

**How it's computed.** The BEA's PMSAVE series — personal saving in billions of dollars at a seasonally-adjusted annual rate, monthly — divided by nominal GDP (FRED series GDP, quarterly, also in billions at a seasonally-adjusted annual rate), times 100. Both are annual-rate figures, so the ratio is directly meaningful; the quarterly GDP print is carried forward onto each month.

**Caveats.** The y-axis is capped below the 2020–21 stimulus spikes so they run off-chart rather than flattening the rest of the history. GDP is quarterly, so the denominator steps at quarter boundaries.

### OP11 — Debt service ratio
*Source: FRED TDSP · cadence: quarterly*

**What it shows.** Required household debt payments — mortgage plus consumer — as a share of disposable income. It measures how burdened household balance sheets are by debt service; rising readings squeeze spending power.

**How it's computed.** The Federal Reserve's TDSP series (total debt-service payments ÷ disposable personal income), quarterly.

**Caveats.** Quarterly and released with a lag.

### MH2 — Corporate credit (IG/HY OAS)
*Source: Bloomberg LUACOAS / LF98OAS · cadence: daily*

**What it shows.** Corporate credit spreads — the extra yield investors demand over Treasuries to hold investment-grade and high-yield bonds — plus the gap between them. Wider spreads mean the market is pricing more credit risk, a classic stress signal that often leads equity weakness.

**How it's computed.** US investment-grade and high-yield corporate option-adjusted spreads (OAS), in basis points, with the HY−IG difference drawn as a third line. The tile ranks HY OAS against its full history.

**Caveats.** Bloomberg's LUACOAS (IG, from 1990) and LF98OAS (HY, from 1994) are the primary source, giving multi-cycle history through the 1998, 2008 and 2020 stress episodes. FRED's ICE BofA OAS is the recent-only fallback and cross-check (its history here begins mid-2023).

### MH3 — Household credit — market-priced (MBS CC spread)
*Source: BBG MTGEFNCL − FRED 5/10y blend · cadence: daily*

**What it shows.** The market's live price of mortgage credit and prepayment risk — the agency MBS current-coupon spread. It widens when investors demand more to hold mortgage risk, a market-based complement to the survey-based mortgage-rate spread in MH4.

**How it's computed.** The Fannie Mae current-coupon yield (Bloomberg MTGEFNCL) minus a 50/50 blend of the 5- and 10-year Treasury yields (FRED DGS5, DGS10), in basis points — the blend approximates the ~7-year effective life of a current-coupon MBS.

**Caveats.** Agency MBS only in v1; the consumer ABS legs (credit cards, autos) await Bloomberg index-ticker verification.

### MH4 — Household credit — borrowing rates
*Source: FRED PMMS − DGS10 · cadence: weekly*

**What it shows.** What new mortgage borrowers pay over the 10-year Treasury — the primary-mortgage spread. It isolates the cost of household credit beyond the risk-free rate, so it moves with lender risk appetite and capacity rather than with the level of rates.

**How it's computed.** Freddie Mac's PMMS 30-year mortgage rate minus the 10-year Treasury yield (FRED DGS10), aligned to the weekly PMMS release, in basis points.

**Caveats.** This is the mortgage spread alone for now; daily lock data (Optimal Blue), credit-card APR over fed funds (G.19), auto-loan rates, and the FHFA rate lock-in gap are slated to extend this row later.

### MH5 — Household credit — balances by product
*Source: NY Fed HHDC · cadence: quarterly*

**What it shows.** Total household debt broken into products — mortgage, auto, credit card, student, HELOC, other — as a stacked bar, so the height is aggregate household debt and the segments show its composition over time.

**How it's computed.** New York Fed Household Debt & Credit balances, quarterly, stacked largest-first (mortgage at the base) in trillions of dollars. The tile is the total across products.

**Caveats.** Quarterly; monthly (Fed G.19) and weekly (Fed H.8) nowcast legs are slated to extend this row later.

### MH6 — Delinquency transitions (30+)
*Source: NY Fed HHDC · cadence: quarterly*

**What it shows.** The flow of household balances newly falling 30 or more days past due, by product — the earliest read on household credit stress, visible well before loans are charged off.

**How it's computed.** The New York Fed HHDC 'new delinquent balances by loan type' — the share of balances transitioning into 30+ day delinquency each quarter. The tile tracks credit cards.

**Caveats.** Quarterly, and reported with the usual HHDC lag.

### SC4 — Implied dispersion (DSPX)
*Source: BBG DSPX Index · cadence: daily*

**What it shows.** Cboe's implied dispersion index (DSPX) — how much single stocks are expected to move independently of the index over the coming month. High dispersion is a stock-picker's environment; low dispersion means names are expected to move together. It is the implied, forward-looking counterpart to the realized dispersion in SC5.

**How it's computed.** The published Cboe S&P 500 Dispersion Index (DSPX), with full history from its inception; the tile ranks the latest level against that history.

**Caveats.** Sourced from Bloomberg, with Cboe's end-of-day CSV as a fallback if the Terminal pull fails.

### SC5 — Realized cross-sectional dispersion
**Status: survivorship**
*Source: Massive grouped bars × SPX membership · cadence: daily*

**What it shows.** Realized cross-sectional dispersion — how widely S&P 500 members' same-day returns spread out. It is the realized counterpart to DSPX (SC4): high readings mean big winners and losers on the same day, a stock-picker's tape; low readings mean the index moves as one. A second line carries the one-month rolling average, since the daily reading is jumpy enough to obscure the trend.

**How it's computed.** Each day, the standard deviation of member daily returns (×100), computed only on days with at least 400 members reporting a return, so a thin cross-section can't distort it. The smoothed line is the trailing 21-session (about one month) mean of that daily series, drawn once at least 10 sessions are available.

**Caveats.** Survivorship badge: the calculation uses today's membership applied backward until historical membership lands, so older readings carry that bias while recent ones are exact.

### MH1 — Breadth (% above moving averages)
*Source: Massive grouped bars × membership · cadence: daily*

**What it shows.** Market breadth — the share of S&P 500 members trading above their 50-day and 200-day moving averages. High readings mean broad participation; a falling line while the index itself holds up is the signature of narrowing, mega-cap-driven leadership.

**How it's computed.** From daily member closes on the grouped tape, the percent of members above each moving average, computed only on days when at least 400 members have data (and enough history for the lookback).

**Caveats.** Membership is the current S&P 500 list applied backward, so older readings carry a survivorship bias. The 200-day series appears once the tape backfill provides a deep enough lookback. Leadership ratios live in MH1B.

### MH1B — Leadership (RSP/SPY, NDX/SPX)
*Source: Massive grouped bars + BBG · cadence: daily*

**What it shows.** Two leadership ratios — equal-weight versus cap-weight (RSP/SPY) and mega-cap growth versus the broad market (NDX/SPX) — each rebased to 100. A falling RSP/SPY means a handful of the largest names are carrying the index; a rising NDX/SPX means growth is leading.

**How it's computed.** The RSP÷SPY and NDX÷SPX daily-close ratios, both rebased to 100 at their common start date (2016-01-04) so they share the axis fairly.

**Caveats.** These are rebased indices, not levels — read the moves relative to that common starting point, not the absolute numbers.

### MH7 — Cross-asset context
*Source: BBG MOVE/UST · cadence: daily*

**What it shows.** The rates backdrop for equities on one chart — bond-market volatility (the MOVE index) alongside the 10-year Treasury yield and the 2s10s curve slope. Rising rates vol or a sharply moving curve is a headwind for risk assets, so this is the cross-asset context for everything else on the dashboard.

**How it's computed.** The MOVE index is plotted on its own left (level) axis; the 10-year yield and the 2s10s slope — the 10-year minus 2-year yield, in percentage points — share the right (%) axis.

**Caveats.** The dollar index (DXY) was dropped from this chart on 2026-07-10: its narrow range flatlined beneath MOVE on the shared axis and added the least as context.

### MH8 — Sentiment (NAAIM exposure)
*Source: NAAIM · cadence: weekly*

**What it shows.** How much equity exposure active managers are actually running — the NAAIM survey. A reading of 0 is flat, 100 is fully invested, and values above 100 (up to ±200) mean leverage or net-short. A crowded-long reading can flag complacency; a washed-out one, capitulation.

**How it's computed.** The weekly NAAIM Exposure Index — the mean equity exposure reported by member managers.

**Caveats.** NAAIM only for now — the AAII bull-minus-bear sentiment leg is blocked because that survey file is now members-only.

### MH9 — Off-exchange + odd-lot share
*Source: Massive SIP tape (classifier) · cadence: daily*

**What it shows.** Two market-structure gauges that have climbed alongside retail and internalization: the share of volume printing away from the lit exchanges (bars, left axis) and the share of trades that are odd lots — fewer than 100 shares (line, right axis).

**How it's computed.** Off-exchange share is FINRA TRF print volume ÷ total tape volume; odd-lot share is the count of sub-100-share trades ÷ total trade count. Both are daily.

**Caveats.** Off-exchange share captures all internalized and dark volume, not retail alone, so read it as a structure indicator rather than a pure retail gauge.

### IS2 — Filing rate (S-1/F-1)
*Source: SEC EDGAR form index · cadence: weekly*

**What it shows.** The IPO pipeline forming — the number of new S-1 and F-1 registration statements filed each month. A rising count signals more companies queuing to go public, typically well ahead of the deals themselves.

**How it's computed.** Calendar-month counts of new S-1 and F-1 filings from the SEC EDGAR form index, deduplicated per filing by CIK; the partial current month is dropped so it can't read artificially low.

**Caveats.** Counts only for now — the amendment (S-1/A) share and an offering-dollar figure parsed from the filings are available extensions.

### IS4 — Aftermarket appetite (IPO ETF vs SPY)
*Source: Massive grouped bars · cadence: daily*

**What it shows.** Whether investors are rewarding recent IPOs — the Renaissance IPO ETF measured against SPY. A rising line means the recent-issue basket is outperforming the broad market, a sign of healthy aftermarket appetite for new deals; a falling one means new issues are out of favor.

**How it's computed.** The daily close of the Renaissance IPO ETF (ticker IPO) ÷ the SPY close, indexed to 100 on 2024-01-02. The anchor date is fixed so the level's meaning doesn't drift as the data window moves.

**Caveats.** A rebased relative-strength ratio — read moves against the anchor date, not the absolute number.

### IS6 — US net corporate equity issuance (% of GDP)
**Status: carried estimate**
*Source: FRED NCBCEBQ027S · FRED GDP · BBG RAY buybacks · cadence: daily*

**What it shows.** How much equity the US nonfinancial corporate sector is issuing or retiring, as a share of the economy. Negative means companies are buying back more than they sell — equity is being withdrawn from the market. This is the definitive level: it is the only measure on the dashboard that captures **cash M&A retirement** (a company bought for cash retires its shares) and **shares issued to employees** through RSU vesting and option exercise. It is scaled by GDP rather than shown in dollars because the published history reaches 1947, and on a dollar axis the first fifty years are invisible against today's magnitudes.

**How it's computed.** FRED series `NCBCEBQ027S` (Fed Z.1 Financial Accounts — Nonfinancial Corporate Business; Corporate Equities; Liability, Transactions), quarterly at a seasonally-adjusted annual rate, divided by nominal GDP. Each quarterly print is already annualized, so the four-quarter mean is the trailing-twelve-month level in the same units — the solid line. The lighter points are the prints as published. Between Fed releases the solid line is extended daily (dashed) by subtracting the change in Russell 3000 gross buybacks since the last published quarter-end: rising buybacks make net issuance more negative. That operator beat both a flat carry and a net-based delta on an 85-quarter backtest. Carried 115 days past the 2026-03-31 quarter-end.

**Caveats.** Carried-estimate badge on the dashed segment: it is directional, not a measurement — the historical error on the carry is roughly ±1 standard deviation of $130bn at the quarter horizon, which is a large fraction of a typical reading, and observing it daily does not shrink it. **Excludes financial-sector companies**, so bank and insurer buybacks are absent; the obvious FRED series for adding them counts ETF and closed-end-fund share creation as a financial equity liability and would swamp the measure. The Fed publishes roughly ten weeks after quarter-end and revises prior quarters. This measure and the Russell 3000 cash-flow chart alongside it are **not the same thing and should not be netted** — they differ by roughly 3× for reasons that are only partly resolved.

### IS6B — Corporate cash-equity flow (Russell 3000)
*Source: BBG RAY CF_DECR/INCR_CAP_STOCK · cadence: daily*

**What it shows.** What Russell 3000 companies spent buying back their own stock, against what they raised selling stock, straight from company cash-flow statements. Buybacks plot below the axis as a cash outflow, issuance above it, and the net line between them. Trailing twelve months at every point, updated daily as companies file — so it steps during earnings season and is flat between.

Everything is shown as a **share of market capitalization** rather than in dollars, and that choice matters: buybacks are at a record dollar level ($1.30tn trailing twelve months, against $282bn of stock sold) and simultaneously near the low end of their range as a share of the market they have to absorb. Reading the dollars alone would report a record corporate bid at a moment when the bid is historically thin relative to the market it is bidding for.

**How it's computed.** `CF_DECR_CAP_STOCK` (gross buybacks) and `CF_INCR_CAP_STOCK` (gross equity issued for cash) on `RAY Index`, daily from March 1998. These return index points, so each is divided by the index price to give a share of market cap. That ratio is deliberately **not** derived from the dollar figures: Bloomberg does not expose the Russell divisor, so dollars require reconstructing it as `CUR_MKT_CAP ÷ PX_LAST` (it drifted from about $20.4bn per index point in 1998 to $18.1bn in 2026), whereas index points ÷ index price cancels the divisor algebraically and carries none of its error.

**Caveats.** **This is not net issuance.** It excludes **cash M&A**: when a company is acquired for cash it simply leaves the index, so the single largest form of share retirement is invisible here. It also excludes **shares issued to employees** through RSU vesting, which create shares but no cash flow. The issuance line combines follow-on offerings, at-the-market programs and option-exercise proceeds and cannot be broken out further. **Financials are included**, unlike the Fed measure alongside it. The dollar figures quoted above are likely biased about 5-7% high because the reconstructed divisor is full-cap while the true index divisor is float-adjusted; the plotted percent-of-market-cap series is immune to this, which is why it is the basis shown. A step means a large company reported, not that money moved that day — the lag is filing lag, four to six weeks after quarter-end. Steps in late June may be Russell reconstitution rather than corporate behaviour.

### IS7 — Fund registration filings (485APOS + N-1A)
*Source: SEC EDGAR 485APOS + N-1A · cadence: weekly*

**What it shows.** The fund-launch pipeline — weekly counts of new fund registrations. It is a proxy for how fast asset managers are bringing new products to market, a gauge of product-side risk appetite.

**How it's computed.** Friday-ended weekly counts of 485APOS filings (new series of existing trusts) plus N-1A filings (brand-new funds) from SEC EDGAR, with insurance-product registrants — variable annuity and separate-account filers — excluded by company name. The partial current week is dropped.

**Caveats.** A filing-pipeline proxy, not a count of actual launches by category; that breakdown, along with fund closures, arrives with the Bloomberg fund screen (the OP5–OP7 work).

### IS9 — IPO issuance pace — cumulative $ (YTD vs prior years)
**Status: real 2026 $**
*Source: Bloomberg EQS + Ritter roster · cadence: daily*

**What it shows.** The pace of IPO issuance this year set against prior years — cumulative operating-company IPO proceeds by day of the year, in real 2026 dollars. This year runs through today while the comparison years show the full calendar, so you can read at a glance whether the current year is running ahead of or behind past cycles.

**How it's computed.** A Ritter-comparable operating-company universe — excluding SPACs, ADRs, REITs, closed-end funds, banks, and unit offerings, with an offer price of at least $5. Each year is anchored to the Jay Ritter IPO roster, and proceeds are offer price × shares from Bloomberg. Prior-year dollars are inflation-adjusted to 2026 (×1.95 for 2000, ×1.24 for 2021) so every curve is in real 2026 dollars, and every deal is included — mega-deals such as SpaceX (~$75B) show as real steps rather than being smoothed away.

**Caveats.** The *real 2026 $* badge marks the inflation adjustment; older Ritter-anchored years carry an estimated tail for deals the roster leaves incomplete. 2000 is Ritter-anchored ($64.8B nominal across 390 operating-cos), of which $10.1B across 75 tail deals is estimated.

### IS10 — IPO issuance pace — cumulative deal count (YTD vs prior years)
*Source: Bloomberg EQS + Ritter roster · cadence: daily*

**What it shows.** The same issuance-pace comparison as IS9, but counting deals rather than dollars — cumulative operating-company IPO count by day of the year, this year against prior years. Reading it next to IS9 separates a few mega-deals from a genuinely broad issuance wave.

**How it's computed.** The companion count view to IS9, built on the same Ritter-comparable universe; comparison years run the full calendar and the current year stops at today.

**Caveats.** Same universe and the same Ritter-anchoring caveats as IS9.

## References

- **Retail trade identification (sub-penny prints + midpoint signing).** Barber, Huang, Jorion, Odean & Schwarz, "A (Sub)penny for Your Thoughts: Tracking Retail Investor Activity in TAQ," *Journal of Finance*, 2024.
- **Retail-flow calibration benchmark.** Nasdaq Retail Activity Tracker (RTAT), Nasdaq Data Link.
- **Small-lot options proxy.** Citadel Securities equity market-structure notes (ch. 11); full external citation to be confirmed.
- **FINRA participation & margin data.** FINRA OTC (non-ATS) Transparency Data — weekly firm-level share volume — and FINRA monthly margin statistics, Financial Industry Regulatory Authority.
- **Cboe volatility & correlation indices.** VIX, VIX3M, VXN, S&P 500 Implied Correlation (COR1M / COR3M), and S&P 500 Dispersion (DSPX) index methodologies, Cboe Global Markets.
- **Realized-volatility & Terminal fields.** Bloomberg VOLATILITY_360D and related fields — trading-day window, log returns, √260 annualization.
- **Rates volatility.** ICE BofA MOVE Index (bond-market implied volatility).
- **Corporate credit spreads.** Bloomberg US Corporate (LUACOAS, IG) and US High Yield (LF98OAS, HY) option-adjusted spreads, primary (deep history); ICE BofA OAS via FRED as the recent cross-check.
- **Mortgage & agency MBS.** Freddie Mac Primary Mortgage Market Survey (PMMS); Fannie Mae current-coupon yield (Bloomberg MTGEFNCL).
- **Household debt & delinquency.** Federal Reserve Bank of New York, Household Debt and Credit Report (HHDC).
- **Household balance sheet & saving.** Federal Reserve Z.1 Financial Accounts (table B.101) and Distributional Financial Accounts (DFA); household debt-service ratio (TDSP); U.S. Bureau of Economic Analysis personal saving (PSAVERT, PMSAVE) — accessed via FRED.
- **Treasury yields & SOFR.** Federal Reserve H.15 constant-maturity Treasury yields (DGS2 / DGS5 / DGS10) and the Secured Overnight Financing Rate (SOFR), via FRED.
- **Manager sentiment.** NAAIM Exposure Index, National Association of Active Investment Managers.
- **Box-spread cross-check.** boxtrades.com (LV7 implied-yield reference).
- **Broker margin rates.** Posted margin schedules of major retail brokers (manual quarterly capture).
- **ETF flows & assets.** Fund shares outstanding, net asset value, and total assets, Bloomberg; fund universe curated in-house.
- **IPO issuance.** Jay R. Ritter, "Initial Public Offerings: Updated Statistics," University of Florida (deal roster); Bloomberg EQS (proceeds and shares); Renaissance Capital IPO ETF (IS4 aftermarket basket).
- **New-issue & fund-registration filings.** U.S. Securities and Exchange Commission, EDGAR full-text and form indexes (S-1 / F-1, 485APOS, N-1A).
