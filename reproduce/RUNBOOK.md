# RUNBOOK — FFIEC Call Report Dashboard (rebuild, verify, deploy)

Quarterly panel of U.S. commercial banks (FFIEC 031/041/051 filers above the tool-dataset asset
threshold) plus ALL / charter-type / size-bucket aggregates, 1976 Q1 → present, free public data.
New here? Read `../GLOSSARY.md` first, and `../DID_I_BREAK_IT.md` before pushing any change.

**State as of 2026-07-02:** see `REPRODUCE_VERIFIED.md` for the commit SHA this kit was last
clean-room-verified against. Golden: RCFD2170=4,016,571,000 @ 2026-03-31 (JPMorgan Chase Bank NA
RSSD 852218). EMPTY_CODES baseline: **662** (see CONTEXT.md for the 675→662 history).

**Environment: Python 3.12.** `pip install -r requirements.txt` (versions pinned — the exact set
the clean-room verification used).

---

## TIER 1 — rebuild the dashboard HTML (minutes, no browser automation)

Works from a fresh clone with nothing but Python 3.12 + the pinned requirements. Run from
`reproduce/`:

```powershell
# 1. stage the committed site data next to the engine (the engine reads/writes site_call/)
New-Item -ItemType Directory -Force site_call | Out-Null
Copy-Item ..\app\*.parquet site_call\
Copy-Item ..\app\ffiec_call_hierarchy.json site_call\

# 2. rebuild the HTML from the committed era shards + hierarchy (~seconds)
python make_site_call.py --html-only

# 3. validator (reads ffiec_call_tool.parquet — committed in this kit — plus site_call/)
python validate_build_call.py
```

Expected `--html-only` output: `PARTS (eager): ['ffiec_call_recent_2018_2026.parquet'] |
OLD_PARTS (lazy): ['ffiec_call_old_1976_2017.parquet']` then the site size + EMPTY_CODES count.

### Acceptance test (if ANY line fails, the build is NOT good — stop, do not push)
- `make_site_call.py --html-only` exits 0 and classifies exactly 1 eager + 1 lazy shard.
- `site_call/index.html` matches the committed `..\app\index.html` **byte-for-byte except the
  `Built YYYY-MM-DD HH:MM` timestamp** (and, in principle, NODATA-set ordering — a documented,
  harmless non-determinism; the CODES themselves must be the same set — count 662).
- `validate_build_call.py` prints `ALL CHECKS PASSED` and exits 0 (includes the golden-cell check).
- Golden cell: JPMorgan Chase Bank NA (RSSD 852218) `RCFD2170` @ 2026-03-31 = **4,016,571,000**.
  Manual re-check straight off the committed shard:
  `python -c "import pandas as pd; d=pd.read_parquet('../app/ffiec_call_recent_2018_2026.parquet'); print(d[(d.entity_id=='BANK:852218')&(d.mdrm=='RCFD2170')&(d.quarter_end=='2026-03-31')].value.iloc[0])"`
- Serve and open it: `cd site_call; python -m http.server 8001` → http://localhost:8001 loads with
  ZERO console errors (F12), fetches ONLY the recent shard at startup, and the 📅 Older data button
  extends the chart to 1976 when clicked.

A golden-cell mismatch, a changed EMPTY_CODES count, or a changed check count = real break.
A timestamp diff = expected.

## Serve the committed dashboard (no rebuild at all)

```powershell
cd ..\app; python -m http.server 8001    # DuckDB-WASM needs http://, not file://
```

## Re-split the era shards (only when the source panel changes)

```powershell
python build_call_shards.py --boundary 2018    # MUST match SHARD_BOUNDARY in make_site_call.py
```
The splitter self-verifies (row reconciliation, era bounds no-gap/no-overlap, golden-in-eager,
200-sample query equivalence) and exits nonzero on any failure. If you change the boundary, update
`SHARD_BOUNDARY` in `make_site_call.py` to match, delete any prior-boundary `ffiec_call_recent_*`/
`ffiec_call_old_*` files from `site_call/` (the splitter does NOT remove other boundaries' output,
and `_era_split()` treats EVERY `recent_*` file as eager), and rerun `make_site_call.py --html-only`.
On Windows, run with `PYTHONIOENCODING=utf-8` (the splitter prints an emoji).

---

## TIER 2 — rebuild the DATA from scratch (hours; needs real Chrome + Playwright)

The FFIEC CDR bulk endpoints are Akamai-guarded: **real Chrome via Playwright only — plain
curl/wget/requests are blocked and must not be used against them.** One-time setup on top of
Tier 1: `playwright install chrome` (Google Chrome must be installed).

Run all steps from `reproduce/`:

| # | Command | Produces | Notes |
|---|---|---|---|
| 1 | `python cdr_download_031.py` | quarterly CDR bulk ZIPs (2001-Q1+) | real-Chrome, Akamai-safe. Resumable. |
| 2 | `python cdr_parse_031.py` then `python cdr_parse_call.py` | parsed long CSVs per quarter | |
| 3 | `python build_call_hist_cf.py` / `python pull_historical.py` | Chicago Fed historical Call data 1976–2000 | plain requests OK for Chicago Fed. |
| 4 | `python build_tool_dataset.py` | `ffiec_call_tool.parquet` (the source panel: banks above the asset threshold + pre-summed ALL/type/size aggregate rows) | |
| 5 | `python build_hierarchy.py` then `python number_call_hierarchy.py` (+ `apply_partab.py`/`map_partab.py` for RC-R Part I/II tabs) | `ffiec_call_hierarchy.json` | reads the blank form PDFs + dictionary + overrides. |
| 6 | `python enrich_call.py` / `python enhance_call_roster.py` | captions + roster enrichment | |
| 7 | `python build_segments_call.py` | `ffiec_call_segments.parquet` (income COMBs for the validator's RIAD check) | optional — validator skips with a note if absent. |
| 8 | **`python validate_build_call.py`** | (exit 0 = pass) | **QA gate — must pass before site build.** |
| 9 | `python build_call_shards.py --boundary 2018` | the two era shards in `site_call/` | self-verifying (see above). |
| 10 | `python make_site_call.py --html-only` | `site_call/index.html` | the dashboard. |

### What's pre-built in this kit (so Tier 2 is optional)
- `ffiec_call_tool.parquet` — the full source panel (71MB, 11.7M rows, 1976–2026). Lets the
  validator and the shard splitter run immediately with no data pull.
- `ffiec_call_hierarchy.json` + `_overrides.json` + `_completeness_exclusions.json` — curated form tree.
- `ffiec_call_dictionary.csv`, `ffiec_call_captions.csv`, `ffiec_call_roster.csv` — MDRM captions + roster.
- `FFIEC031/041/051_202606_f.pdf` — blank form PDFs (hierarchy builder inputs).

## Deploy (GitHub Pages)

Copy `site_call/index.html` + `site_call/ffiec_call_recent_*.parquet` +
`site_call/ffiec_call_old_*.parquet` + `ffiec_call_hierarchy.json` → `..\app\`, commit, push.
Pages serves `main`; the root `index.html` redirects to `app/`. Then run the full
`../DID_I_BREAK_IT.md` checklist against the live URL.
