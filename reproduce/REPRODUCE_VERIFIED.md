# REPRODUCE_VERIFIED — clean-room rebuild record

**Verified: 2026-07-02, against commit `368d5ef`** (M.1 packaging cycle; app/ layout + self-contained kit).
Sibling repos at this cycle: FFIEC_002 `256c108` · FRY9C `c92d60d`.

## What was run (automated, `_verify/acceptance_m1.py` in the dev workspace — mirrors RUNBOOK.md § Tier 1 verbatim)

Fresh `git clone` into an empty temp dir (no dev-workspace files reachable) → clean `venv`
(Python 3.12.1) → `pip install -r reproduce/requirements.txt` (pinned: pandas 3.0.3, pyarrow 24.0.0,
duckdb 1.5.4, playwright 1.60.0, requests 2.34.2) → RUNBOOK Tier-1 steps → checks below.

## Results — 11/11 PASS

| Check | Result |
|---|---|
| clone + venv + pinned pip install | PASS |
| `make_site_call.py --html-only` (1 eager + 1 lazy shard classified) | PASS, exit 0 |
| rebuilt `site_call/index.html` vs committed `app/index.html` | **byte-identical** after `Built <ts>` normalization (247,805 chars) |
| `validate_build_call.py` | **ALL CHECKS PASSED** (incl. `[GOLDEN] RCFD2170=4,016,571,000 at 2026-03-31 [OK]`) |
| golden cell off the cloned shard (pandas one-liner) | 4,016,571,000 exact |
| serve cloned `app/` + headless Chromium | loads, golden entity (RSSD 852218) renders, **zero console errors, zero 4xx** |

## Expected/allowed diffs
- The `Built YYYY-MM-DD HH:MM` stamp (one occurrence). Nothing else — this run was byte-identical
  otherwise. NODATA-set ordering is a theoretically-possible harmless diff; it did not occur here.

## Found-and-fixed during this verification (why the kit looks the way it does)
- `validate_build_call.py` hard-requires `_completeness_gate.py` + `expected_items.json` — these are
  workspace-root files and were missing from the kit's first cut; now committed.
- `requirements.txt` gains `duckdb` (completeness-gate dependency; the old comment claiming duckdb
  was browser-only was wrong).
- EMPTY_CODES baseline is **662** (see CONTEXT.md for the 675→662 full-history explanation).

> Historical note: an older repo (`FFIEC_Call`, HEAD `6f326c1`) predates `Call_Reports`; all
> current work lives here.
