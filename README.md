# Market Conditions Dashboard (Market Pulse)

Locally-run Python pipeline → one self-contained HTML dashboard: a daily
aggregate pulse on US equity & options market conditions, each metric read in
the context of its own history. Deploys to **lens.avos.co/market-conditions**.

Full design contract: **[DASHBOARD-SPEC.md](DASHBOARD-SPEC.md)** (canonical).
House craft/visual standards: `equities-pm/HOUSE-STANDARDS.md` (`§A#`).

## Three tiers (§2 / §A8)
- **Method** — `DASHBOARD-SPEC.md`, this README.
- **Machinery** — `src/` (pull → compute → render), `deploy.py`.
- **Material** — `data/dashboard/` (DuckDB+Parquet lake; never committed) and
  `build_data/*.json` (the display layer the page embeds).

## Quick start
```bash
python -m pip install -r requirements.txt
# drop single-line key files at repo root (gitignored):
#   .fred_api_key        (free: https://fred.stlouisfed.org/docs/api/api_key.html)
#   .massive_api_key     (Phase 2/3, when procured)
python -m src.run                 # pull → compute → render
python -m src.run --render        # re-render from build_data only (deterministic)
python deploy.py market-conditions
```
Bloomberg pulls need a running Terminal on the machine (`xbbg`/`blpapi`
auto-install on first use). The build/render step needs neither — it reads the
`build_data/` display layer.

## Layout
```
DASHBOARD-SPEC.md      the spec (canonical)
src/
  config.py            metric registry (mirror of §4) + PHASE switch
  util.py              percentile (1yr gate), display downsample, staleness
  store.py             DuckDB/Parquet lake + build_data JSON emit + run log
  run.py               orchestrator (soft-fail per source)
  pull/                fred.py (live) · bbg/massive/finra/edgar/free (landing per §8)
  compute/             metric constructions (§5) — lands per build order
  render/              Jinja2 template + house.css + ECharts boot
tests/                 unit tests (§7) — util covered; classifier/box/GEX to come
build_data/            display layer (JSON) the renderer embeds
deploy.py, infra/      S3 + CloudFront push to lens.avos.co/<slug>
```

## Phases (§8)
1. **Bloomberg + free** — SC/OP/VC/MH/IS + LV(1,6,7,8,11,13,14,15,16). Ship + deploy.
2. **+ Massive stocks** — retail flows RF1–6, OP8, MH9, SC5. Gate: RF9 ≥ 0.6.
3. **+ Massive options/OPRA** — vol engine (§5.10), LV2–5/9/10/12, RF7–8.

Set the active phase in `src/config.py` (`PHASE = 1`).

## Status (2026-07-08, slice 2)
**12 metrics live:** SC1–4 (concentration/dispersion), OP1/OP3 (FRED DFA/Z.1),
VC1/VC3 (correlation, term structure), MH2/MH4/MH7 (credit, mortgage, cross-
asset), IS2 (EDGAR filings). Page ~1.3 MB. ECharts 5.5.1 vendored.

BBG notes (see `src/pull/bbg.py`): S&P constituent **weights are not DAPI-
entitled** on this Terminal (member list works; weights garbage) → SC1–3
weights computed from float-adjusted caps per §4; ask the BBG rep about weight
entitlement to unlock the 2000→ backfill. Multi-ticker `bdh` misaligns
calculated-index calendars → per-ticker pulls.

Massive key verified for both plans (still served from `api.polygon.io`).
Behind the iboss proxy Python needs `truststore` — see `src/pull/_net.py`.

**2026-07-09 big push: 48 metrics live, PHASE=3.** All panels populated.
Stocks quotes tier added (RF1 midpoint-signed, +$3.6B on 07-07); options stay
trades-only by CIO decision → LV5 = OI-convention GEX (labeled), LV12 deferred.
OPRA daily file is small (~80MB gz, ~1 min process). Tape backfill to
2026-05-08 running. Blockers list: see the session summary / run_log —
AAII (403 members-only), ICI weekly link-mining (OP4), RF6 pagination,
LV9 sanity-gated (non-synchronous closes), IS1/3/5/6 (no DAPI path; EDGAR
424B4 route sketched), LV14 seed rates UNVERIFIED, Nasdaq key for RF9.

**2026-07-10: 27-item CIO chart review implemented.** See the §4 amendment
block in DASHBOARD-SPEC.md for the full change list. Highlights: SC1-3/VC4/
RF1D/RF2D killed; RF1 daily bars; RF2 FINRA anchor fixed (T1+T2 only, T1-only
lag segment, Friday labels); realized vol → BBG 360d convention; LV13
sign/SOFR fix; LV16 unit fix + real SI history (bdh backfill 2023-11→); LV8
roll-artifact prune; OP2/OP6 fixes; stacked bars (LV3/OP6/MH5); MH1 split;
LVT snapshot table for no-history leverage reads; VC6 = 4 sector IV baskets;
one-sentence tooltips + status badges + generated APPENDIX.md; legends +
duplicate-color fix in the renderer.

**2026-07-27: CIO round — 47 metrics live.** LV16 (short interest) killed; OP2
rebuilt as % of nominal GDP over its full 1989→ DFA history (was 12 quarters in
dollars); OP10 → % of GDP; SC5 gained a 1-month rolling-average line. See the
§4 amendment block in DASHBOARD-SPEC.md.
