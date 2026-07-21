# U.S. Bank Call Report Explorer

**Every public FFIEC 031/041/051 filing, 1976 Q1 → present, in your browser. No server, no
login, no tracking — one static page + parquet.**

**▶ Live: https://austinfahrenkopf.github.io/Call_Reports/app/index.html**

## Explore
- **The tree IS the form.** Line items follow the form's own numbering — including printed
  "Not applicable" placeholders and real-but-not-collected text items — so nothing silently
  disappears. Σ rows chart filed subtotals with their form-ink formulas on hover.
- **Any chart state is a URL.** Drill, compare banks, transform, then copy the link — the
  reader lands on the exact view. Try a mid-size bank's C&I delinquency:
  `…/app/index.html#e=BANK:595270&m=D_NPL_CI`
- **League tables + peer groups.** Percentile badges (direction-aware), size buckets, custom
  peer sets — aggregated as Σnumerator/Σdenominator, never an average of ratios.
- **Reports + CSV.** One-click tear-sheets; CSV exports keep the official item captions
  (interchange contract — screen labels can be friendlier, exports never drift).

## Verify
The dashboard is the cheap part; the verification machine is the product:
- Golden anchor cells re-asserted **to the dollar** on every build and in CI.
- Externally graded vs the FFIEC's own **UBPR** ratios (616 comparisons × 4 ratios,
  0 investigations).
- Every gate has a **planted negative** — a check that hasn't been watched failing loudly
  doesn't count as a check.
- Missing data renders as a gap, **never a false 0.00%** — "no silent empty charts" is a
  build-failing rule.

## Reproduce
`reproduce/` rebuilds the panel from public sources end-to-end (RUNBOOK.md inside). Data:
FFIEC CDR bulk facsimiles; free and public.

*Built with Claude. Not affiliated with the FFIEC or the Federal Reserve. Not investment
advice; filing data is presented as filed.*
