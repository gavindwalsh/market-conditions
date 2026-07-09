# Session notes — 2026-07-08/09 (handoff)

## What this project is
Market Pulse Dashboard: daily one-command pipeline (`python -m src.run`) → single
self-contained HTML → `python deploy.py market-conditions` → lens.avos.co/market-conditions
(behind existing Cognito/Entra SSO, same infra as country-dashboard). Canonical spec =
`DASHBOARD-SPEC.md` in THIS repo (equities-pm copy is a pointer). House standards =
`equities-pm/HOUSE-STANDARDS.md` (the §A refs). Origin: replicate/extend Citadel's
"1H 2026 Market Structure & Flows" (source map xlsx in equities-pm).

## State at handoff
- **~50 metrics live** across 9 panels (Retail / Vol+Corr / Leverage / Flows / Ownership /
  Credit / Internals / Issuance / Other). PHASE=3 in `src/config.py`.
- First deploys done; repo pushed to github.com/gavindwalsh/market-conditions (private!—
  build_data holds BBG/Massive-derived series).
- **Backfill lanes running in user terminals** (all resumable, newest-first, idempotent):
  T1 quotes 05-26→06-28 · T2 quotes 04-10→05-25 · L3 trades-only 01-02→04-09 ·
  L4 OPRA 01-02→ (was 84/134 done). `refresh_backfill.py` folds new days into charts
  (no pulls); user runs it + deploy while lanes work. Daily `src.run` supersedes after.
- Validation so far: off-exchange 43.1%, odd-lot 73%, 0DTE 31%/SPX 67%, retail options
  $15.2B/day, participation ~6.6% raw → all cross-check published/Citadel figures.
  Midpoint vs BJZZ signing: opposite signs on 07-07 (+3.6B vs −2.2B) — method vindicated.

## Key decisions (all in spec)
- **×3 scale** (`config.RETAIL_SCALE_FACTOR`) on RF1/RF2 only ("est. total", tooltips);
  NOT RF7/RF8 (small-lot already ≈ market totals) nor ratios. Provisional until RF9 fit.
- **Options plan = trades-only** (CIO): LV5 = OI-convention GEX (labeled), LV12 deferred,
  LV9 via EOD-close parity (currently sanity-gated, see unfinished).
- **Massive snapshots carry server-side IV/greeks** → §5.10 vol engine mostly unnecessary.
- RF1/RF2 = weekly bars + RF1D/RF2D daily bars. RF2 spliced with FINRA official (below).
- Panel order/names per CIO 2026-07-09; §6 records it.

## Traps we hit (WILL recur)
1. **iboss proxy TLS-intercepts some domains** (sec.gov, files.polygon.io) → Python needs
   `truststore.inject_into_ssl()` (`src/pull/_net.py`). Proxy also causes transient
   SSL bad-record-mac on long downloads (retry logic in backfill handles it) and 503s
   from Massive S3 when >2-3 heavy lanes run concurrently.
2. **Massive = api.polygon.io** (rebrand incomplete). Flat files need SEPARATE S3 creds
   (`.massive_s3_key`, labeled 3-line format). OPRA trades files are small (~80MB);
   stock trades ~3.5GB, quotes ~9GB/day.
3. **DuckDB concurrency**: cap memory (10GB) + per-call unique spill dirs — two lanes
   sharing a spill dir hard-crash silently; default 80%-RAM × 2 lanes = OOM kill.
4. **xbbg quirks**: bdp returns columns ALPHABETICALLY (px_ask before px_bid — rename by
   NAME, never position); multi-ticker bdh misaligns calculated-index calendars (pull
   per-ticker); S&P member WEIGHTS not DAPI-entitled (garbage −2.4e-14; we compute from
   float caps; ask BBG rep to unlock 2000→ SC1-3 backfill).
5. **FINRA Query API**: OAuth2 client-credentials (`.finra_api_key` = 2 labeled lines);
   dataset `otcMarket/weeklySummary` is WEEK-PARTITIONED — exact-week domainFilters
   return in ms, any range scan 504s. Post-2023 rows are per-firm/symbol (OTC_W_FIRM,
   ATS_W_FIRM; tiers T1/T2/OTCE — old *_VOL_STATS aggregates end 2023-11). Tiered
   publication lag (T1 ~2wk, T2/OTCE ~4wk) → drop tier-incomplete weeks. Per-firm sums
   double-count interdealer (~2× true share) → FINRA series rendered on own axis,
   trend-not-level.
6. **Silent str.replace no-ops**: two display bugs shipped because a patch script's
   old_string didn't match and plain `.replace()` doesn't error. Use Edit tool or
   assert-in-script, and verify the RENDERED artifact, not just intermediate JSON.
7. **classify sanity**: quarter-end/Russell-recon days (06-25/26) produce outlier retail
   nets — consider a rebalance-day flag before trusting those days in RF series.
8. Auto-mode classifier blocks `git push` to the personal remote (and blocked the whole
   chained command incl. commit — keep local git steps separate). User pushes manually.

## Unfinished / next session
1. **RF6 chart** — wholesaler-by-firm volumes; data ALREADY in lake (`finra_weekly_otc`,
   OTC_W_FIRM has marketParticipantName: Citadel/Virtu/Jane St...). Small compute.
2. **RF9 calibration** — needs free Nasdaq Data Link key (user) + 60 signed days
   (backfill delivers). Lifts "uncalibrated" banner; fit the ×3 factor empirically.
3. **Citadel June re-comparison** — after T1/T2 finish: does our June 12 print as record
   net-buy? Validation memo vs source-map chart figures (esp. ch.9/10/11/12/14).
4. **RF4** auto-unlocks at ≥20 signed days AND ≥5 SPX down days (hot tape → waiting).
5. **LV9 sanity-gated** (−1670bp): EOD-close parity too noisy — needs trade-time C/P
   pairing from OPRA trades. LV1 (Cboe 0DTE) superseded by LV2's SPX series; wire as
   §7.3 cross-check when convenient.
6. **Registry/title sync**: RF1/RF2 card titles still show old registry names; sync
   `config.py` names to the weekly framing. OP8/MH9 panel strings in retail_series.py
   `_emit` calls say ownership/health (cosmetic; renderer uses registry).
7. **Blocked externals**: AAII (403, members-only — MH8 is NAAIM-only), ICI weekly XLS
   (release-page link mining, OP4), IS1/IS3/IS5/IS6 (no DAPI path; EDGAR 424B4 parser
   sketched), LV14 broker rates = UNVERIFIED seeds in `pull/free.py` (user to verify),
   VC4 member-breadth + LV10 universe extension (snapshot loop over ~300 members).
8. **Ops end-state**: Task Scheduler job for daily `src.run` + `deploy.py` after close;
   retire refresh_backfill.py. Also §7 cross-checks as tests + determinism check.
9. Deep tape history (2016→) trades-only via L3 pattern; extend grouped bars if 200dma
   coverage needs more.

## Quick commands
```
python refresh_backfill.py              # fold backfilled days into charts (fast)
python -m src.run                       # full daily: pulls + computes + render
python -m src.run --render              # re-render only (NO computes — see trap 6)
python deploy.py market-conditions     # push to lens (user runs aws login first)
# resume a lane (idempotent):
python -u -c "import sys; sys.path.insert(0,'.'); from src.pull import massive; print(massive.backfill_tape('2026-04-10', end='2026-05-25', max_days=35))"
```
