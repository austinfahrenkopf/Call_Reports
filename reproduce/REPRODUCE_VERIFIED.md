# FFIEC Call Report Reproduce Kit — Verification Record

**Current HEAD:** `35da46b2d440ea916b94006ad30ae8bd5d4e5405` (2026-07-01)
**Pages build:** ✅ SUCCESS 2026-07-01T20:36:33Z (Actions run 28546144182)
**Live URL:** https://austinfahrenkopf.github.io/Call_Reports/
**GitHub repo:** `austinfahrenkopf/Call_Reports`

> **Note on two repos:** This is the current active repository (`Call_Reports`). An older repo
> (`FFIEC_Call`, HEAD `6f326c1`) still exists with comprehensive reproduce/ docs (CONTEXT.md,
> RUNBOOK.md, REPRODUCE_VERIFIED.md for earlier commits). Clone `Call_Reports` for the latest
> features; refer to `FFIEC_Call_repo` for pipeline documentation.

---

## What's at HEAD (`35da46b`)

Commit `35da46b` corrects the data-sync issue in the prior commit (`de35c7d`): the working folder
was 12 days behind the repo's June-19 data (951KB hierarchy / 62.3MB parquet → stale; should be
1.21MB / 66.6MB). The fix synced the build folder from the live repo and rebuilt.

| Commit | What it added |
|---|---|
| `de35c7d` | Sigma Calc fix + extra-chart controls (ported from FRY9C `5ec3601` + FFIEC_002 `a68fa1a`) |
| `35da46b` | Data-sync fix: rebuild from June-19 data → 675 EMPTY_CODES (was 54 from stale data) |

---

## Build + validate (HTML-only, fastest path)

From the repo root:
```powershell
# Requires: pip install pandas pyarrow duckdb playwright; playwright install chrome
# Data files already present in repo root: ffiec_call.parquet (66.6 MB), ffiec_call_hierarchy.json (1.21 MB)
python reproduce/make_site_call.py --html-only
```

Expected output: `site_call/index.html` (225,511 bytes). Run validator:
```powershell
python validate_build_call.py   # from External Bank Data/ workspace
```
Expected: ALL CHECKS PASSED.

---

## Golden cell

**JPMorgan Chase Bank (RSSD 852218) RCFD2170 @ 2026-03-31 = 4,016,571,000** ($ thousands) ✓

This cell must be unchanged after any rebuild. It validates: parquet integrity, COMB coalesce,
hierarchy wiring, site generation.

---

## Features at HEAD (`35da46b`)

| Feature | Details |
|---|---|
| Sigma Calc formula builder | DOM-safe code-search (`createElement`/`textContent`; `rawCode=r.m` closure); Save→Blob download `ffiec_call_formulas.json`; Load→hidden `#calcImportFile`; localStorage `ffiec_call_formulas`. Playwright 38/38 PASS (RSSD 852218). |
| Extra-chart controls | Per-chart `ec-legend-<id>` div; `renderEcLegend(chart)`; "⌯ Labels" checkbox; snap-beside layout via `#charts-flex`. **Call-specific:** `.chartbox` wrapper (new for Call — pane() previously returned bare SVG); chart IDs start at 1 (`_nextChartId=1` → `ec-panes-1`, `ec-legend-1`). Playwright 38/38 PASS. |
| Data (June-19) | 675 EMPTY_CODES recomputed from June-19 hierarchy (1.21MB) + parquet (66.6MB). `ffiec_call_completeness_exclusions.json` updated (+483 spurious codes for new June-19 hierarchy items + ENT sequence gap items 2-6). |
| Normden dropdown | `COMB2170/2122/2200/3210` (assets/loans/deposits/equity). Note: COMB2200 includes IBF synthesis from §IBF-DEPOSIT-REBUILD (`0f1e51d` in `FFIEC_Call_repo`) — deposits now $20.548T vs $18.828T before fix. |
| League table | 353 measure options via `buildLGMEAS` HIER walk. No `hybrid_sum` special case (unlike FRY9C). |
| RC-N item 9 hybrid_sum | COMB1406 (30-89 PD), COMB1407 (90+ PD), COMB1403 (Nonaccrual); fills 64q gap 2001-Q1→2016-Q4; ±0.8% mean error vs reported values. Committed in `FFIEC_Call_repo` at `6f326c1` (earlier; in both repos). |
| NODATA non-determinism | `--html-only` produces 225,511 bytes but non-bit-reproducible SHA-256 (NODATA set is serialized from a Python `set`; iteration order is hash-randomized per process). Use `validate_build_call.py` (not hash comparison) to verify correctness. |

---

## Key technical notes (Call-specific)

- **COMB2170** (not raw RCFD2170) is the standard Total Assets tree measure (`showRaw=false` by default).
- **RCFN is NOT additive to RCFD**: IBF figures (RCFN) are already inside RCFD for 031 filers.
- **COMB2200 includes IBF**: RCFD2200 = RCON2200 + RCFN2200 synthesized at build time (see §IBF-DEPOSIT-REBUILD). Do not remove the synthesis step in `build_segments_call.py`.
- **Call repo is FLAT**: `index.html` at root (no `app/` subdirectory). FRY9C and FFIEC_002 use `app/`. Standardization to `app/` is an OPEN follow-up.
- **Comprehensive pipeline docs** (CONTEXT.md, RUNBOOK.md with full step-by-step rebuild) are in the older `FFIEC_Call_repo` at `C:\Users\Austin Fahrenkopf\Desktop\Claude\FFIEC_Call_repo\reproduce\`. Those docs were last fully updated at commit `48aae0f` (§REPO-READINESS-CALL); they do not reflect sigma calc / extra-chart fixes (which are JS-only and don't affect the data pipeline).

---

## Data-pipeline summary (from FFIEC_Call_repo docs)

Full pipeline (4–6 hours from scratch):
1. `cdr_download_031.py` — pull quarterly CDR bulk ZIPs (Playwright real Chrome)
2. `cdr_parse_call.py` — ZIP → long panel parquet
3. `build_segments_call.py` — aggregates; step 0a synthesizes RCFD2200 for IBF filers
4. `build_tool_dataset.py` — per-bank (≥$10B) dataset with RCFD2200 synthesis
5. `quick_enrich.py` — MDRM re-enrichment without Fed download (use instead of `enrich_call.py` if blocked)
6. `make_site_call.py` — HTML + site parquet
7. `validate_build_call.py` — gate (must pass before deploy)

See `FFIEC_Call_repo/reproduce/RUNBOOK.md` for detailed steps.
