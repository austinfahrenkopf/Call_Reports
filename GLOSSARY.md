# GLOSSARY — read this first if any term below is new to you
(Ships identically in all three repos. Written for a maintainer — human or AI — with zero project history.)

**MDRM code** — a Federal Reserve data-dictionary identifier for one reported cell, e.g. `RCFD2170`. First 4 chars = reporting domain/form series ("RCFD"), last 4 = item number ("2170" = total assets). The Fed's MDRM dictionary maps codes to captions.

**RCFD / RCON / RCFN** — domain prefixes on Call/002 codes: RCFD = consolidated (domestic + foreign offices), RCON = domestic offices only, RCFN = foreign offices only. A bank reports one or more variants of the same item. IBF = International Banking Facility (books foreign-type business inside the U.S.).

**COMB (this project's convention)** — the "combined" value per bank per quarter = `COALESCE(RCFD, RCON, RCFN)` — take RCFD if reported, else RCON, else RCFN. It is a **coalesce, never a sum** (RCFD already contains RCON; adding them double-counts). The ONLY sanctioned additive use is deposit synthesis on FFIEC 031: `RCFD2200 = RCON2200 + RCFN2200` for IBF filers.

**BHCK / BHCT / BHDM / BHFN** — Y-9C (holding company) domain prefixes; BHCK = consolidated. Y-9C has no COMB layer.

**Aggregation rule (hard constraint)** — any ratio over a group of banks = Σ(numerators) / Σ(denominators). NEVER the average of individual ratios.

**Golden cell** — one hand-verified value per dashboard used as a regression tripwire. If a rebuild changes it, the build is wrong:
- Call: `RCFD2170 = 4,016,571,000` @ 2026-03-31, JPMorgan Chase Bank NA (RSSD 852218)
- Y-9C: `BHCK2170 = 4,900,475,000` @ 2026-03-31, JPMorgan Chase & Co (RSSD 1039502)
- 002: `RCFD2170 = 245,557,856` @ 2026-03-31, MUFG Bank NY Branch (RSSD 444819)

**RSSD** — the Fed's permanent ID for an institution. Entity ids in the Call parquet look like `BANK:852218`.

**DERIV** — this engine's built-in derived measures (ratios/sums like `D_NPL`), defined in a JS object near the top of the emitted script. `hybrid_sum`/`hybrid_ratio` DERIVs splice reported totals with component sums across reporting-gap eras.

**Hierarchy JSON** — the collapsible left-rail tree, built from the form's blank PDF (structure) + MDRM dictionary (captions). A "mis-nest" = an item under the wrong parent → wrong subtotals.

**EMPTY_CODES / NODATA** — codes present in the hierarchy but with zero rows in the shipped parquet; greyed in the UI so nothing renders a silently-empty chart. The expected count is asserted at build time (Call: 662 as of 2026-07-02 — full 1976-2026 history).

**Shards / eager / lazy** — the site parquet is split by era. The recent shard loads at startup; older eras load on demand ("📅 Older data" or automatically when a view requests an old quarter).

**The three-clone rule** — this repo is one of THREE sibling dashboards (Call_Reports, FFIEC_002, FRY9C) whose engines are hand-synced copies, NOT a shared library. If you change engine/UI code here, the same change almost certainly belongs in the other two — but NEVER copy a data code across forms without proving it exists in that form's parquet.
