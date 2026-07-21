#!/usr/bin/env python3
"""
build_tool_dataset.py
Explorer 'tool' dataset = segment aggregates (ALL / 031 / 041 / 051 / size buckets)
PLUS each LARGE bank (assets >= --min-assets) as its own selectable entity. Small
banks are NOT individual — they live only in the size buckets / type sums.

Combines:
  ffiec_call_segments.parquet   (from enrich_call.py)  -> the aggregate entities
  cdr_parquet/*.parquet         (the master)           -> the big banks, individual
  ffiec_call_roster.csv         -> bank names
  ffiec_call_dictionary.csv     -> schedule + title per MDRM

Output: ffiec_call_tool.parquet
  columns: entity_id, entity_label, kind, quarter_end, mdrm, value, n_filers, schedule, description

Setup:  pip install duckdb pandas pyarrow
Run:    python build_tool_dataset.py                 # individual >= $10B
        python build_tool_dataset.py --min-assets 1000000   # >= $1B (more banks, bigger file)
"""
import argparse
import duckdb, pandas as pd

ap=argparse.ArgumentParser()
ap.add_argument("--min-assets", type=int, default=10_000_000, help="$thousands; default 10,000,000 = $10B")
a=ap.parse_args(); T=a.min_assets

con=duckdb.connect(); con.execute("PRAGMA threads=4")
con.execute("CREATE VIEW t AS SELECT * FROM read_parquet('cdr_parquet/*.parquet')")

# dictionary + roster
dic = pd.read_csv("ffiec_call_dictionary.csv")
sched = dict(zip(dic["mdrm"], dic["schedule"].fillna("")))
title = dict(zip(dic["mdrm"], dic["title"].fillna("")))
ros = pd.read_csv("ffiec_call_roster.csv", dtype={"id_rssd":"int64"})
nm  = dict(zip(ros["id_rssd"], ros["institution_name"].fillna("")))

# --- aggregate entities: reuse the enriched segment file --------------------
seg = pd.read_parquet("ffiec_call_segments.parquet")
seg = seg.rename(columns={"segment":"entity_id","segment_type":"kind"})
seg["entity_label"] = seg["entity_id"]
seg = seg[["entity_id","entity_label","kind","quarter_end","mdrm","value","n_filers","schedule","description"]]

# --- AQ-S5-1 (owner contract, signed mailbox [49], executed [51]): materialized ALT aggregates.
# ALT alternative-group measures (D_NPL_CI) carry per-FILER first-present-of semantics that a
# flat sum over the stored aggregate rows cannot express (the 031-vs-041/051 family split), so
# the engines render aggregate scopes honest-absent for them. These pseudo-code rows ARE the
# ALT-resolved aggregates: per filer-quarter, resolve each group with the ENGINE's own token
# rules (8-char literal = exact code; bare 4-char = prefix-coalesce RCFD->RCON->RIAD->RCFA->
# RCFW->RCOA->RCOW->RCFN, mirroring tokVal; group sum = sum of PRESENT members; the FIRST group
# with any member present wins), then DECIMAL(38,6)-sum over each scope's membership (the same
# ft/size-bucket rules as build_segments_call.py; replication-anchored against the stored RAW
# aggregate rows to the dollar). RAW-code aggregate rows are UNTOUCHED — the pseudo codes ride
# beside them under reserved non-MDRM names (collision-swept 2026-07-21). Rows exist only where
# the family resolves (pre-1984 quarters produce nothing -> honest-absent, disclosed).
# Rollback = drop the ALTD rows + revert the engines' ALT_AGG registry.
print("materializing AQ-S5-1 ALT aggregates (ALTD1CI6 num / ALTD1CI7 den) ...")
_co = lambda base: ("COALESCE(" + ",".join(
    f"max(CASE WHEN mdrm='{p}{base}' THEN value END)" for p in
    ("RCFD","RCON","RIAD","RCFA","RCFW","RCOA","RCOW","RCFN")) + ")")
_lit6 = "('RCFD1251','RCFD1252','RCFD1253','RCFD1254','RCFD1255','RCFD1256')"
alt = con.execute(f"""
WITH fam AS (
  SELECT quarter_end, id_rssd, any_value(entity_type) AS ft,
    sum(CASE WHEN mdrm IN {_lit6} AND value IS NOT NULL THEN value END)  AS numA,
    count(CASE WHEN mdrm IN {_lit6} AND value IS NOT NULL THEN 1 END)   AS numA_n,
    {_co('1606')} AS t1606, {_co('1607')} AS t1607, {_co('1608')} AS t1608,
    {_co('1763')} AS t1763, {_co('1764')} AS t1764, {_co('1766')} AS t1766,
    COALESCE(max(CASE WHEN mdrm='RCFD2170' THEN value END),
             max(CASE WHEN mdrm='RCON2170' THEN value END),
             max(CASE WHEN mdrm='RIAD2170' THEN value END)) AS assets
  FROM t
  WHERE mdrm IN {_lit6} OR substr(mdrm,5,4) IN ('1606','1607','1608','1763','1764','1766','2170')
  GROUP BY quarter_end, id_rssd),
resolved AS (
  SELECT quarter_end, id_rssd, ft,
    CASE WHEN assets>=250000000 THEN 'SIZE_250B+'
         WHEN assets>=50000000  THEN 'SIZE_50-250B'
         WHEN assets>=10000000  THEN 'SIZE_10-50B'
         WHEN assets>=1000000   THEN 'SIZE_1-10B'
         ELSE 'SIZE_<1B' END AS szb,
    CASE WHEN numA_n > 0 THEN numA
         WHEN t1606 IS NOT NULL OR t1607 IS NOT NULL OR t1608 IS NOT NULL
              THEN COALESCE(t1606,0)+COALESCE(t1607,0)+COALESCE(t1608,0) END AS num,
    CASE WHEN t1763 IS NOT NULL OR t1764 IS NOT NULL
              THEN COALESCE(t1763,0)+COALESCE(t1764,0)
         WHEN t1766 IS NOT NULL THEN t1766 END AS den
  FROM fam),
scoped AS (
  SELECT 'ALL' AS entity_id, 'all' AS kind, quarter_end, num, den FROM resolved
  UNION ALL
  SELECT ft, 'filing_type', quarter_end, num, den FROM resolved WHERE ft IS NOT NULL
  UNION ALL
  SELECT szb, 'size_bucket', quarter_end, num, den FROM resolved)
SELECT entity_id, kind, quarter_end, 'ALTD1CI6' AS mdrm,
       CAST(sum(CAST(num AS DECIMAL(38,6))) AS DOUBLE) AS value,
       count(num) AS n_filers
FROM scoped WHERE num IS NOT NULL GROUP BY entity_id, kind, quarter_end
UNION ALL
SELECT entity_id, kind, quarter_end, 'ALTD1CI7' AS mdrm,
       CAST(sum(CAST(den AS DECIMAL(38,6))) AS DOUBLE) AS value,
       count(den) AS n_filers
FROM scoped WHERE den IS NOT NULL GROUP BY entity_id, kind, quarter_end
""").df()
alt["entity_label"] = alt["entity_id"]
alt["schedule"] = ""
alt["description"] = alt["mdrm"].map({
    "ALTD1CI6": "Materialized ALT aggregate (AQ-S5-1): C&I delinquency NUMERATOR, per-filer "
                "first-present-of [RCFD1251-56 | 1606+1607+1608], summed over the scope.",
    "ALTD1CI7": "Materialized ALT aggregate (AQ-S5-1): C&I loans DENOMINATOR, per-filer "
                "first-present-of [1763+1764 | 1766], summed over the scope."})
alt = alt[["entity_id","entity_label","kind","quarter_end","mdrm","value","n_filers","schedule","description"]]
print(f"  ALT aggregate rows: {len(alt):,} ({alt['quarter_end'].min()} - {alt['quarter_end'].max()})")
seg = pd.concat([seg, alt], ignore_index=True)

# --- individual large banks (assets >= T) ----------------------------------
print(f"selecting individual banks with assets >= {T:,} $thousands ...")
indiv = con.execute(f"""
WITH bank AS (
  SELECT quarter_end, id_rssd,
         max(CASE WHEN mdrm IN ('RCFD2170','RCON2170') THEN value END) AS assets
  FROM t GROUP BY quarter_end, id_rssd),
big AS (SELECT quarter_end, id_rssd FROM bank WHERE assets >= {T})
SELECT t.quarter_end, t.id_rssd, t.mdrm, t.value
FROM t JOIN big USING (quarter_end, id_rssd)
""").df()
print(f"  individual-bank rows: {len(indiv):,}  (distinct banks: {indiv['id_rssd'].nunique()})")

indiv["entity_id"]   = "BANK:" + indiv["id_rssd"].astype(str)
indiv["entity_label"]= indiv["id_rssd"].map(lambda r: f"{nm.get(r,'')} ({r})".strip())
indiv["kind"]="bank"; indiv["n_filers"]=1
indiv["schedule"]    = indiv["mdrm"].map(sched).fillna("")
indiv["description"] = indiv["mdrm"].map(title).fillna("")
indiv = indiv[["entity_id","entity_label","kind","quarter_end","mdrm","value","n_filers","schedule","description"]]

# Synthesize RCFD deposit codes (2200/6631/6636) for individual large banks with IBFs.
# Mirrors the same synthesis done in build_segments_call.py for aggregate segments.
# Without this, individual bank KPI cards and trend charts show RCON2200 (domestic-only)
# even for 031 filers with IBF operations (e.g. JPM: +$578B IBF, Citi: +$644B IBF).
print("   synthesizing RCFD deposit codes for individual IBF-reporting banks ...")
synth_indiv = con.execute(f"""
WITH big AS (
  SELECT quarter_end, id_rssd
  FROM (SELECT quarter_end, id_rssd,
               max(CASE WHEN mdrm IN ('RCFD2170','RCON2170') THEN value END) AS assets
        FROM t GROUP BY quarter_end, id_rssd) sq
  WHERE assets >= {T}
),
dep AS (
  SELECT t.quarter_end, t.id_rssd,
         substr(t.mdrm,5,4) AS base_code,
         sum(t.value) AS combined_value,
         max(CASE WHEN substr(t.mdrm,1,4)='RCFN' THEN t.value END) AS rcfn_max,
         max(CASE WHEN substr(t.mdrm,1,4)='RCON' THEN t.value END) AS rcon_max
  FROM t JOIN big USING (quarter_end, id_rssd)
  WHERE substr(t.mdrm,5,4) IN ('2200','6631','6636')
    AND substr(t.mdrm,1,4) IN ('RCON','RCFN')
  GROUP BY t.quarter_end, t.id_rssd, base_code
  HAVING rcfn_max > 0 AND rcon_max IS NOT NULL
)
SELECT quarter_end, id_rssd, 'RCFD'||base_code AS mdrm, combined_value AS value
FROM dep
""").df()
print(f"   synthesized {len(synth_indiv):,} RCFD deposit rows for individual banks")
synth_indiv["entity_id"]    = "BANK:" + synth_indiv["id_rssd"].astype(str)
synth_indiv["entity_label"] = synth_indiv["id_rssd"].map(lambda r: f"{nm.get(r,'')} ({r})".strip())
synth_indiv["kind"]         = "bank"; synth_indiv["n_filers"] = 1
synth_indiv["schedule"]     = synth_indiv["mdrm"].map(sched).fillna("")
synth_indiv["description"]  = synth_indiv["mdrm"].map(title).fillna("")
synth_indiv = synth_indiv[["entity_id","entity_label","kind","quarter_end","mdrm","value","n_filers","schedule","description"]]
indiv = pd.concat([indiv, synth_indiv], ignore_index=True)

tool = pd.concat([seg, indiv], ignore_index=True)
tool.to_parquet("ffiec_call_tool.parquet", index=False)
mb = __import__("os").path.getsize("ffiec_call_tool.parquet")/1e6
print(f"\nwrote ffiec_call_tool.parquet: {len(tool):,} rows  ({mb:.1f} MB)")
print(f"  entities: {tool['entity_id'].nunique()} "
      f"(aggregates + {indiv['entity_id'].nunique()} individual banks)")
print("  if the MB is too big for the browser, raise --min-assets (e.g. 50000000 = $50B).")
