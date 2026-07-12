# DID I BREAK IT? â€” mandatory checklist after ANY change
(Ships identically in all three repos, adapted names per form. If any step fails, your change is wrong or unverified â€” do not push.)

1. **Rebuild**: `python make_site_call.py --html-only` (fast; no data pipeline). It must exit 0 and print the site size + EMPTY_CODES count.
2. **Validator**: run this repo's `validate_build_call.py` â†’ must print ALL CHECKS PASSED.
3. **Golden cell**: confirm the value in GLOSSARY.md is unchanged. Fastest: open the dashboard, enter the golden entity's RSSD, click Total assets, hover 2026-03-31 â€” or query the SQL box.
4. **EMPTY_CODES**: must equal the expected count printed by the last known-good build (see CONTEXT.md history). ANY unexplained change = stop and root-cause; a change is only acceptable with a written explanation of exactly which codes changed and why that is correct.
5. **Browser smoke** (2 min, use a SPECIFIC named bank â€” never the ALL aggregate, it hides per-bank bugs): default load draws; pick the golden entity; add a second entity; toggle QoQ/Stacked; open League table + Entity Report; open a pre-boundary quarter (older-era shard must auto-load, never silently show empty); check the browser console for errors (F12 â†’ zero red).
6. **If you changed anything in the aggregation/COMB/DERIV area**: additionally recompute ONE affected number by hand from the parquet (DuckDB: `SELECT ... FROM '<parquet>' WHERE ...`) and match it to the screen. Numbers on screen must be traceable to filings â€” that is this project's entire value.
7. **Three-clone rule**: state explicitly (in your commit message) whether the change needs porting to the two sibling repos, and why/why not.

Never claim a check passed that you did not actually run and observe.

