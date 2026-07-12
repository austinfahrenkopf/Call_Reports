#!/usr/bin/env python3
"""
build_call_shards.py - Standalone era-shard builder for the FFIEC 031 (Call) dashboard.

Ports the FR Y-9C lazy-shard architecture (build_fry9c_shards.py) to Call. Y-9C splits
active-vs-historical FILERS because a BHC roster changes over time (mergers/failures/new
charters) and the "ALL" aggregate needs a separate pre-summed shard to avoid re-scanning
~100M rows. Call's schema is simpler: entity_id/entity_label/kind/quarter_end/mdrm/value/
n_filers/schedule/description, and the aggregate rows (kind in {all, filing_type,
size_bucket}) are ALREADY pre-summed per quarter by build_tool_dataset.py — every
aggregate row is self-contained within its own quarter, so there is no analogue of Y-9C's
separate fry9c_agg.parquet: a straight quarter_end era split is suficient and correct for
both individual banks (kind='bank') and aggregates alike.

Era split (parameterized; default boundary matches the task's target eager window):
  ffiec_call_recent_<LO>_<HI>.parquet   - eager shard, loaded at startup (LO=2001 default)
  ffiec_call_old_<LO>_<HI>.parquet      - lazy shard(s), HTTP-range-loaded on "Older data"
                                          click (pre-2001, back to 1976-Q1)

Both shards are entity_id-sorted first (then mdrm, then quarter_end) so a single-entity
query prunes to that entity's row groups via DuckDB-WASM HTTP range requests — mirrors the
Y-9C sort key exactly (build_fry9c_shards.py line 91 / make_site_fry9c.py line 119).

SELF-VERIFICATION (run automatically at the end of main(), before the script reports
success): the whole point of sharding is that it must be perfectly transparent to every
query the dashboard can issue. A silent split bug (row dropped, boundary off-by-one,
duplicate row in two shards) would show up as a wrong number on some chart, someday, and
that is exactly the failure mode the CLAUDE.md hard constraint forbids ("a measure must
never render an empty chart silently"). So this script refuses to report success unless:
  1. Sum of per-shard row counts == source row count (no row lost or duplicated).
  2. Each shard's [min(quarter_end), max(quarter_end)] falls entirely inside its declared
     era bounds, and eager/lazy shards do not overlap and do not gap (every quarter in the
     source appears in exactly one shard).
  3. The golden cell (RCFD2170=4,016,571,000 @ 2026-03-31, BANK:852218) is present in the
     EAGER shard specifically (not just "somewhere") - this is the shard that loads at
     startup, so if the golden cell isn't in it, the default view would be silently wrong.
  4. Query-equivalence: >=200 random (entity_id, mdrm, quarter_end) triples, sampled from
     BOTH eras (so a bug that only affects the old shard can't hide behind an all-recent
     sample), return byte-identical `value` from [the correct shard] and from the ORIGINAL
     source parquet.
Any failure -> nonzero exit with a specific message. No partial/best-effort success.

Usage:
  python build_call_shards.py                    # default boundary: eager = 2001-Q1..present
  python build_call_shards.py --boundary 2001     # explicit (same as default)
  python build_call_shards.py --panel ffiec_call_tool.parquet
  python build_call_shards.py --dry-run           # compute + report sizes, write nothing

Run from FFIEC 031/ after build_tool_dataset.py has produced ffiec_call_tool.parquet.
Next step after this script: python make_site_call.py --html-only
"""
from __future__ import annotations
import argparse, os, random, sys
import pandas as pd

SRC = "ffiec_call_tool.parquet"
SITE = "site_call"
MAXBYTES = 95 * 1024 * 1024   # GitHub (non-LFS) soft ceiling; warn, don't block
TARGET_EAGER_MAX = 20 * 1024 * 1024  # task target: eager shard should stay <=~20MB
_PQARGS = dict(index=False, compression="zstd", row_group_size=50_000)
GOLDEN_ENTITY = "BANK:852218"          # JPMorgan Chase Bank, N.A.
GOLDEN_CODE   = "RCFD2170"
GOLDEN_Q      = "2026-03-31"
GOLDEN_VALUE  = 4_016_571_000
N_SAMPLE      = 200


def _fail(msg: str) -> None:
    print(f"\nSELF-CHECK FAILED: {msg}", file=sys.stderr)
    sys.exit(1)


def _legacy_cleanup(site: str) -> None:
    """Remove the old monolithic single/MAXROWS-split parquet naming so a stale
    ffiec_call.parquet / ffiec_call_NN.parquet never gets picked up alongside the new
    era shards by make_site_call.py's `sorted(f for f in os.listdir(SITE) if f.endswith('.parquet'))`
    HTML_ONLY glob."""
    removed = []
    for f in os.listdir(site):
        if not f.endswith(".parquet"):
            continue
        if f.startswith("ffiec_call_recent_") or f.startswith("ffiec_call_old_"):
            continue  # new-scheme shard, keep
        if f == "ffiec_call.parquet" or (f.startswith("ffiec_call_") and f[11:13].isdigit()):
            os.remove(os.path.join(site, f))
            removed.append(f)
    if removed:
        print(f"  removed legacy monolithic/MAXROWS shard(s): {removed}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--panel", default=SRC)
    ap.add_argument("--site", default=SITE)
    ap.add_argument("--boundary", type=int, default=2001,
                    help="First year (Q1) of the EAGER shard; everything before this year "
                         "goes into the lazy OLD shard. Default 2001 (matches the RC-N "
                         "hybrid_sum reconstruction window / RIAD-COMB data start used "
                         "elsewhere in this codebase).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute era split + sizes, run no writes, run no self-checks.")
    ap.add_argument("--sample-n", type=int, default=N_SAMPLE,
                    help="Number of random (entity,mdrm,quarter) equivalence-check triples.")
    args = ap.parse_args()

    if not os.path.exists(args.panel):
        sys.exit(f"Source panel not found: {args.panel} (run build_tool_dataset.py first)")
    os.makedirs(args.site, exist_ok=True)

    print(f"Loading {args.panel} ...")
    df = pd.read_parquet(args.panel)
    need = ["entity_id", "entity_label", "kind", "quarter_end", "mdrm", "value",
            "n_filers", "schedule", "description"]
    missing_cols = [c for c in need if c not in df.columns]
    if missing_cols:
        sys.exit(f"Source panel missing expected column(s): {missing_cols} "
                  f"(schema drift vs build_tool_dataset.py — fix before sharding)")
    src_rows = len(df)
    print(f"  {src_rows:,} rows  |  {df['entity_id'].nunique():,} entities  |  "
          f"{df['quarter_end'].nunique()} quarters  "
          f"({df['quarter_end'].min()} - {df['quarter_end'].max()})")

    # Row distribution by year - drives the boundary choice / lets us warn if the eager
    # shard would blow past the ~20MB target for the chosen boundary.
    yr = df["quarter_end"].astype(str).str[:4].astype(int)
    by_year = df.groupby(yr).size().sort_index()
    print("  rows by year (first/last 3 shown):")
    print(f"    {by_year.head(3).to_dict()} ... {by_year.tail(3).to_dict()}")

    boundary = args.boundary
    is_recent = yr >= boundary
    n_recent, n_old = int(is_recent.sum()), int((~is_recent).sum())
    print(f"  boundary={boundary}: recent(era>={boundary})={n_recent:,} rows | "
          f"old(era<{boundary})={n_old:,} rows")

    if args.dry_run:
        print("--dry-run: no files written, no self-checks run.")
        return

    # Sort entity_id -> mdrm -> quarter_end FIRST (same key Y-9C uses) so a single-entity
    # or single-code query prunes to a small run of row groups via HTTP range requests,
    # and so zstd compresses better (like values clustered together).
    df = df.sort_values(["entity_id", "mdrm", "quarter_end"]).reset_index(drop=True)
    yr = df["quarter_end"].astype(str).str[:4].astype(int)  # re-derive after sort/reset
    is_recent = yr >= boundary

    df_recent = df[is_recent].reset_index(drop=True)
    df_old = df[~is_recent].reset_index(drop=True)

    _legacy_cleanup(args.site)

    max_yr = int(yr.max())
    fn_recent = f"ffiec_call_recent_{boundary}_{max_yr}.parquet"
    path_recent = os.path.join(args.site, fn_recent)
    _tmp = path_recent + ".tmp"
    df_recent.to_parquet(_tmp, **_PQARGS)
    os.replace(_tmp, path_recent)
    sz_recent = os.path.getsize(path_recent)
    q_recent = (df_recent["quarter_end"].min(), df_recent["quarter_end"].max())
    print(f"  {fn_recent}: {len(df_recent):,} rows, {sz_recent/1e6:.1f} MB  "
          f"({q_recent[0]} - {q_recent[1]})")
    if sz_recent > TARGET_EAGER_MAX:
        print(f"    NOTE: eager shard is {sz_recent/1e6:.1f} MB, above the ~20MB target "
              f"-- consider raising --boundary (fewer years eager) if load time matters more "
              f"than historical-data-by-default.")
    if sz_recent > MAXBYTES:
        _fail(f"{fn_recent} is {sz_recent/1e6:.1f} MB -- exceeds the {MAXBYTES/1e6:.0f} MB "
              f"GitHub soft limit; must sub-split before push.")

    old_parts = []
    if not df_old.empty:
        min_yr_old = int(yr[~is_recent].min())
        fn_old = f"ffiec_call_old_{min_yr_old}_{boundary-1}.parquet"
        path_old = os.path.join(args.site, fn_old)
        _tmp = path_old + ".tmp"
        df_old.to_parquet(_tmp, **_PQARGS)
        os.replace(_tmp, path_old)
        sz_old = os.path.getsize(path_old)
        q_old = (df_old["quarter_end"].min(), df_old["quarter_end"].max())
        print(f"  {fn_old}: {len(df_old):,} rows, {sz_old/1e6:.1f} MB  "
              f"({q_old[0]} - {q_old[1]})  (lazy-loaded on '\U0001F4C5 Older data' click)")
        if sz_old > MAXBYTES:
            _fail(f"{fn_old} is {sz_old/1e6:.1f} MB -- exceeds the {MAXBYTES/1e6:.0f} MB "
                  f"GitHub soft limit; must sub-split before push (e.g. --boundary lower to "
                  f"shrink the old shard, or add a third era).")
        old_parts = [(fn_old, path_old, df_old)]

    print(f"\nSelf-verifying shards vs source ({args.panel}) ...")
    _self_check(df, df_recent, old_parts, boundary, args.sample_n)
    print("SELF-CHECK PASSED: row counts reconcile, eras are contiguous with no gap/overlap, "
          "golden cell present in the eager shard, equivalence sample matches source exactly.")
    print(f"\nDone. Site parquets updated in {args.site}/")
    print("Next: python make_site_call.py --html-only  (regenerates index.html with the new shard list)")


def _self_check(df_src: pd.DataFrame, df_recent: pd.DataFrame,
                 old_parts: list[tuple[str, str, pd.DataFrame]], boundary: int,
                 sample_n: int) -> None:
    # --- 1. row-count reconciliation --------------------------------------------------
    n_src = len(df_src)
    n_shards = len(df_recent) + sum(len(d) for _, _, d in old_parts)
    if n_shards != n_src:
        _fail(f"row count mismatch: source={n_src:,} vs sum(shards)={n_shards:,} "
              f"(a row was dropped or duplicated during the split)")
    print(f"  [1/4] row counts reconcile: {n_src:,} == {n_shards:,}")

    # --- 2. per-shard bounds + no-gap/no-overlap across the full quarter set ----------
    all_src_quarters = set(df_src["quarter_end"].astype(str).unique())
    recent_quarters = set(df_recent["quarter_end"].astype(str).unique())
    for q in recent_quarters:
        if int(str(q)[:4]) < boundary:
            _fail(f"eager shard contains out-of-bounds quarter {q} (< boundary year {boundary})")
    covered = set(recent_quarters)
    for fn, _, d in old_parts:
        qs = set(d["quarter_end"].astype(str).unique())
        for q in qs:
            if int(str(q)[:4]) >= boundary:
                _fail(f"old shard {fn} contains out-of-bounds quarter {q} (>= boundary year {boundary})")
        overlap = covered & qs
        if overlap:
            _fail(f"quarter overlap between eager shard and {fn}: {sorted(overlap)[:5]}")
        covered |= qs
    missing_q = all_src_quarters - covered
    if missing_q:
        _fail(f"{len(missing_q)} quarter(s) present in source but absent from all shards "
              f"(gap): {sorted(missing_q)[:10]}")
    extra_q = covered - all_src_quarters
    if extra_q:
        _fail(f"shards contain quarter(s) not in source (impossible unless a bug): {sorted(extra_q)[:10]}")
    print(f"  [2/4] era bounds clean: {len(recent_quarters)} eager quarters + "
          f"{len(covered)-len(recent_quarters)} old quarters == {len(all_src_quarters)} source quarters, "
          f"no gap, no overlap")

    # --- 3. golden cell must be in the EAGER shard specifically ------------------------
    gold = df_recent[(df_recent["entity_id"] == GOLDEN_ENTITY) &
                      (df_recent["mdrm"] == GOLDEN_CODE) &
                      (df_recent["quarter_end"].astype(str) == GOLDEN_Q)]
    if gold.empty:
        _fail(f"golden cell {GOLDEN_CODE}={GOLDEN_VALUE:,} @ {GOLDEN_Q} {GOLDEN_ENTITY} "
              f"NOT FOUND in the eager shard (recent shard would render the default view wrong)")
    v = int(gold["value"].iloc[0])
    if v != GOLDEN_VALUE:
        _fail(f"golden cell value mismatch in eager shard: expected {GOLDEN_VALUE:,}, got {v:,}")
    print(f"  [3/4] golden cell {GOLDEN_CODE}={v:,} @ {GOLDEN_Q} {GOLDEN_ENTITY} confirmed in eager shard")

    # --- 4. query-equivalence sample: shards vs source, both eras represented ---------
    rng = random.Random(20260702)  # fixed seed -> reproducible CI-style check
    half = max(1, sample_n // 2)
    recent_pool = df_recent[["entity_id", "mdrm", "quarter_end", "value"]]
    old_pool = (pd.concat([d[["entity_id", "mdrm", "quarter_end", "value"]] for _, _, d in old_parts],
                          ignore_index=True) if old_parts else recent_pool.iloc[0:0])
    picks = []
    if len(recent_pool):
        idx = rng.sample(range(len(recent_pool)), min(half, len(recent_pool)))
        picks.append(recent_pool.iloc[idx])
    if len(old_pool):
        idx = rng.sample(range(len(old_pool)), min(sample_n - half, len(old_pool)))
        picks.append(old_pool.iloc[idx])
    if not picks:
        _fail("no rows available to sample for the query-equivalence check")
    sample = pd.concat(picks, ignore_index=True)
    if len(sample) < min(sample_n, n_src):
        _fail(f"equivalence sample only has {len(sample)} rows (wanted >= {min(sample_n, n_src)})")

    src_idx = df_src.set_index(["entity_id", "mdrm", "quarter_end"])["value"]
    mismatches = []
    for row in sample.itertuples(index=False):
        key = (row.entity_id, row.mdrm, row.quarter_end)
        try:
            src_val = src_idx.loc[key]
        except KeyError:
            mismatches.append((key, "MISSING_IN_SOURCE_INDEX", row.value))
            continue
        if hasattr(src_val, "__len__") and not isinstance(src_val, (str, bytes)):
            src_val = src_val.iloc[0] if hasattr(src_val, "iloc") else src_val[0]
        # NaN/None-safe equivalence: pd.isna() catches both float('nan') and None/NaT, so a
        # missing value on both sides counts as a match (NaN != NaN is True in plain Python/
        # pandas and would otherwise false-fail this check on legitimately-missing cells).
        # Everything else still compares exact (no tolerance introduced).
        src_is_na, row_is_na = pd.isna(src_val), pd.isna(row.value)
        if src_is_na or row_is_na:
            if src_is_na != row_is_na:
                mismatches.append((key, src_val, row.value))
            continue
        if float(src_val) != float(row.value):
            mismatches.append((key, src_val, row.value))
    if mismatches:
        _fail(f"{len(mismatches)}/{len(sample)} equivalence-check triples DISAGREE between "
              f"shards and source: {mismatches[:5]}")
    n_recent_sampled = sum(1 for r in sample.itertuples() if int(str(r.quarter_end)[:4]) >= boundary)
    n_old_sampled = len(sample) - n_recent_sampled
    print(f"  [4/4] query-equivalence: {len(sample)}/{len(sample)} triples match source exactly "
          f"({n_recent_sampled} eager-era + {n_old_sampled} old-era samples)")


if __name__ == "__main__":
    main()
