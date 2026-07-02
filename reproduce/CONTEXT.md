# CONTEXT — FFIEC Call Report Dashboard (architecture & standing decisions)

Read `../GLOSSARY.md` for every term used here (MDRM, COMB, DERIV, golden cell, shards, …).
This file is the "why does the code make these choices" record. `RUNBOOK.md` is the "how do I
rebuild it" record. Neither goes stale silently: `REPRODUCE_VERIFIED.md` states the commit SHA the
whole kit was last verified against.

## The three-clone rule (read before changing engine code)

This engine (`make_site_call.py`) is one of THREE hand-synced near-identical clones:
[Call_Reports](https://github.com/austinfahrenkopf/Call_Reports) ·
[FFIEC_002](https://github.com/austinfahrenkopf/FFIEC_002) ·
[FRY9C](https://github.com/austinfahrenkopf/FRY9C). There is no shared library. If you fix a
bug in the engine/UI here, the same bug almost certainly exists in the other two — check before
assuming it's isolated. But NEVER copy a data code (an MDRM) across forms without proving it
exists in that form's parquet; that exact mistake caused real bugs in this project's history.

## What this is

One Python script (`make_site_call.py`) emits a single self-contained `index.html` that runs
DuckDB-WASM in the browser over era-sharded parquet files. No server-side anything. The source
panel (`ffiec_call_tool.parquet`, 11.7M rows, 1976–2026, 384 entities) holds individual banks
above an asset threshold PLUS pre-summed aggregate rows (`ALL`, per filing-type 031/041/051, per
size bucket) computed by `build_tool_dataset.py`.

## Standing decisions (do not re-litigate without new evidence)

1. **Aggregation**: any cross-bank ratio = Σnumerators / Σdenominators. Never average-of-ratios.
   Aggregate entities' rows are pre-summed per quarter and self-contained.
2. **COMB = coalesce** (RCFD ?? RCON ?? RCFN), never a sum. The ONLY additive exception is IBF
   deposit synthesis: `RCFD2200 = RCON2200 + RCFN2200` where RCFD2200 is absent.
3. **Era shards (2026-07-02, cycle 3)**: `SHARD_BOUNDARY=2018`. Eager
   `ffiec_call_recent_2018_2026.parquet` (23.4MB) loads at startup; lazy
   `ffiec_call_old_1976_2017.parquet` (40.4MB) HTTP-range-loads on 📅 Older data OR automatically
   when League/Form/Export Builder open (they `await ensureOldCall()`), OR via the recompute
   safety-net when a BANK entity returns all-empty. Boundary 2018 chosen by the "eager ≤ 25MB"
   rule (2001 default gave 53.3MB). `_era_split()` in the engine classifies site parquets by
   filename — every `ffiec_call_recent_*` file counts as eager, so never leave multiple
   boundaries' shards in the same directory; it also falls back to a legacy monolith name for
   pre-sharding site dirs, so never leave a stale `ffiec_call.parquet` around either.
4. **EMPTY_CODES baseline = 662** (hierarchy codes with zero rows across the UNION of both
   shards). History: the count was 675 through 2026-07-01, but that number was computed against
   the old site monolith which only contained 2001+ data. When sharding shipped the full
   1976–2026 history (2026-07-02), 13 pre-2001-only legacy codes (RCFD3557, RCFD5594/5595,
   RCON5596/6860/6861/6979/6999/8773/8775/8776, RIAD4769, RIADA530) gained their data and
   correctly un-greyed → 662. Any OTHER change to this count = stop and root-cause (see
   `../DID_I_BREAK_IT.md`). The NODATA scan must always union ALL shards — scanning only
   `PARTS[0]` is a known bug class.
5. **Golden cell**: JPMorgan Chase Bank NA (RSSD 852218) `RCFD2170` @ 2026-03-31 =
   **4,016,571,000** ($k). Checked by `validate_build_call.py`, by the shard splitter's
   self-check, and manually per RUNBOOK. Cross-validated 9/9 cells against TD Bank USA's filed
   PDF (cert 33947) on 2026-07-01.
6. **Entity Report "Noncurrent" = 90+ days past due + nonaccrual (1407+1403)** — NOT 30–89
   (1406). A bucket-swap here was a real P0 bug (fixed 2026-07-01); the tear-sheet carries a
   footnote stating the definition. KPI codes have RCON fallbacks so 041/051 filers get credit
   KPIs. The NPL card deliberately has NO percentile badge (the old badge was keyed to a
   nonaccrual-only $ percentile — mislabeled).
7. **RC-N item 9 hybrid_sum** (COMB1406/1407/1403): reported totals exist only outside
   2001–2016; inside that window the row is reconstructed from items 1–8 components (±0.8%;
   2001–2010 understated ~4% because consumer sub-codes weren't collected). The chart tooltip
   discloses this. DERIV keys for coalesced codes must be `COMB<base>`, not the raw MDRM.
8. **League/size buckets use COMB2170 via `perFilerValues`** (raw RCFD-only dropped RCON-only
   filers). League sorts are cached per `measure::quarter::bucket`; header clicks resort
   client-side from the cache.
9. **UI conventions**: transforms (QoQ/YoY Δ/4Q avg/Share %) are display-only panes, date-based
   lookback (never index-based — a filing gap must produce null, not a wrong average); Share % is
   share of REPORTING entities that quarter; `recompute()` must end with
   `await recomputeExtraCharts()` (entity changes must re-derive extra-chart series — a missing
   call here was a real bug); pinned-tooltip ✕ needs its own listener on the tip element (tips are
   `document.body` children — clicks never bubble to `#panes`).
10. **`#showmerged` is a no-op for Call** (lineage not available for this form) — do not wire it.

## Data sources (all free/public)
- FFIEC CDR bulk downloads, 2001+ (Akamai-guarded → Playwright + real Chrome ONLY).
- Chicago Fed historical Call files, 1976–2000 (plain HTTP is fine there).
- Fed MDRM dictionary (captions), blank form PDFs (hierarchy structure).

## Current state
Live at https://austinfahrenkopf.github.io/Call_Reports/ (`app/` layout, root redirect).
See `REPRODUCE_VERIFIED.md` for the verified commit SHA and the clean-room test record.
Sibling repo SHAs at the time of this packaging cycle are recorded there too.
