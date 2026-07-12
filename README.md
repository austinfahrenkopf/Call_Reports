# FFIEC Call Report Dashboard

Free, reproducible browser dashboard over public FFIEC 031/041/051 Call Report filings — every U.S. commercial bank above the tool-dataset asset threshold, plus ALL / charter-type / size-bucket aggregates and custom peer groups, 1976 Q1–present, $ thousands. No server: static HTML + DuckDB-WASM; the parquet loads in your browser.

**Live:** https://austinfahrenkopf.github.io/Call_Reports/ (redirects to `app/index.html`)
**Siblings:** [Call_Reports](https://github.com/austinfahrenkopf/Call_Reports) · [FFIEC_002](https://github.com/austinfahrenkopf/FFIEC_002) · [FRY9C](https://github.com/austinfahrenkopf/FRY9C) (three hand-synced dashboards — see GLOSSARY.md "three-clone rule")

## Use it
Open the live URL. Pick a bank (name or RSSD) → click measures in the left rail, or hit ⚡ Views for one-click preset analyses. All aggregates are Σnumerator/Σdenominator. The recent era (2018+) loads at startup; click 📅 Older data (or open a pre-2018 quarter in League/Form/Export) for the full 1976+ history. Data as of: 2026-Q1 (refreshes quarterly — the rebuild path is `reproduce/RUNBOOK.md` § Tier 2).

**v1.0 (2026-07):** every schedule tree is form-true — order, membership, labels, and multi-column
layouts audited against the official form PDFs page by page, and machine-checked against a
form-derived manifest shipped in this repo (see Trust & verification). Σ Custom formulas: build
A/B-alias or raw-MDRM-code formulas in the ⚡ calc panel; **⬇ Save to file / ⬆ Load from file**
round-trips them as JSON (definitions also autosave in browser storage; importing a file re-adds
any formula chip you removed).

## Run locally
Clone → serve the app folder (`cd app; python -m http.server 8001`) → open http://localhost:8001. No build needed; the committed HTML + parquet shards are the deployable artifact. (DuckDB-WASM needs `http://`, not `file://`.)

## Rebuild from source (Tier 1 — no browser automation needed)
Requirements: Python 3.12, `pip install -r reproduce/requirements.txt` (pandas, pyarrow).
See `reproduce/RUNBOOK.md` § Tier 1: rebuild `index.html` from the committed parquet + hierarchy, then follow `reproduce/docs/DID_I_BREAK_IT.md`.

## Rebuild the DATA from scratch (Tier 2 — full pipeline)
Needs real Chrome + Playwright (the FFIEC CDR endpoints are bot-guarded; plain HTTP clients are blocked and must not be used). See `reproduce/RUNBOOK.md` § Tier 2. Everything comes from free public sources: FFIEC CDR bulk downloads (2001+), Chicago Fed historical Call files (1976–2000), the Fed MDRM dictionary, and the blank form PDFs.

## Trust & verification
Every number is traceable to public filings. Regression tripwires: a hand-verified golden cell (RCFD2170 = 4,016,571,000 @ 2026-03-31, JPMorgan Chase Bank NA — see GLOSSARY.md), `reproduce/tools/validate_build_call.py`, an EMPTY_CODES count assertion (662 — see `reproduce/docs/CONTEXT.md` history), and `reproduce/docs/REPRODUCE_VERIFIED.md` documenting a clean-room rebuild. **This repo is self-verifying (v1.0):** `python reproduce/tools/check_hierarchy_manifest.py call` checks the served `app/` schedule tree against `reproduce/config/expected_hierarchy_call.json` — the hand-audited, form-PDF-derived manifest (item membership, order, and full multi-column code sequences) — and must print `ALL COMPLETE SCHEDULES PASS`. Before changing anything, read GLOSSARY.md and `reproduce/docs/DID_I_BREAK_IT.md`.

## License
MIT (see LICENSE). The underlying data is U.S. government public-domain regulatory filings.
