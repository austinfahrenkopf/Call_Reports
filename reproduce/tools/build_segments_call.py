#!/usr/bin/env python3
"""
build_segments_call.py  (with COMBINED RCFD/RCON series)
Aggregate the full Call panel into SEGMENT buckets: ALL / 031 / 041 / 051 / size.
Adds a COMBINED line for every balance-sheet item: COMB#### = COALESCE(RCFD####,
RCON####) per bank BEFORE summing — the correct cross-form total (e.g. COMB2170 =
all-banks total assets, continuous across 031/041/051).

Setup:  pip install duckdb pandas pyarrow
Run:    python build_segments_call.py
Output: ffiec_call_segments_long.csv (+ .parquet)
"""
import duckdb
con = duckdb.connect(); con.execute("PRAGMA threads=4")
con.execute("CREATE VIEW t0 AS SELECT * FROM read_parquet('cdr_parquet/*.parquet')")

# 0a) Synthesize RCFD deposit codes for 031 filers that operate IBFs.
#
#     On the FFIEC Call Report, total deposits for a 031 filer are split across TWO codes:
#       RCON2200  = domestic-office deposits
#       RCFN2200  = IBF (International Banking Facility) deposits
#     RCFD2200 (consolidated total) does NOT exist as a reported code on the form.
#     Correct consolidated deposits = RCON + RCFN (additive: separate geographic pools).
#
#     Without this step, COMB2200 = COALESCE(null, RCON2200) = domestic-only, understating
#     system deposits by ~$1.72T (9.1%) for all aggregate entities.
#
#     Parallel structure confirmed for sub-totals: 6631 (non-IB deposits) and 6636
#     (interest-bearing deposits) also have RCON+RCFN but no RCFD.
#     Codes 2625/2650/2898/2133 have RCFN but no RCON counterpart — NOT synthesized.
#
#     Empirically verified (Q1 2026): RCFN6631+RCFN6636 = RCFN2200 exactly; deposit/assets
#     ratios (69-90%) are sensible; RCFD2200 is absent from all 201 quarters of raw data.
print("0a) synthesizing RCFD deposit codes (2200/6631/6636) for 031 IBF filers ...")
con.execute("""
CREATE TEMP TABLE t_synth AS
SELECT quarter_end, id_rssd, any_value(entity_type) AS entity_type,
       'RCFD'||substr(mdrm,5,4) AS mdrm,
       sum(value) AS value
FROM t0
WHERE substr(mdrm,5,4) IN ('2200','6631','6636')
  AND substr(mdrm,1,4) IN ('RCON','RCFN')
GROUP BY quarter_end, id_rssd, substr(mdrm,5,4)
HAVING max(CASE WHEN substr(mdrm,1,4)='RCFN' THEN value END) IS NOT NULL
   AND max(CASE WHEN substr(mdrm,1,4)='RCFN' THEN value END) > 0
""")
n_synth = con.execute("SELECT count(*) FROM t_synth").fetchone()[0]
print(f"   synthesized {n_synth:,} RCFD deposit rows (RCON+RCFN where RCFN>0)")

print("0) building COMBINED (RCFD/RCON/RIAD) items ...")
con.execute("""
CREATE TEMP TABLE t AS
SELECT quarter_end, id_rssd, entity_type, mdrm, value FROM t0
UNION ALL
SELECT quarter_end, id_rssd, entity_type, mdrm, value FROM t_synth
UNION ALL
SELECT quarter_end, id_rssd, any_value(entity_type) AS entity_type,
       'COMB'||substr(mdrm,5,4) AS mdrm,
       COALESCE(max(CASE WHEN substr(mdrm,1,4)='RCFD' THEN value END),
                max(CASE WHEN substr(mdrm,1,4)='RCON' THEN value END),
                max(CASE WHEN substr(mdrm,1,4)='RIAD' THEN value END)) AS value
FROM (SELECT quarter_end, id_rssd, entity_type, mdrm, value FROM t0
      UNION ALL
      SELECT quarter_end, id_rssd, entity_type, mdrm, value FROM t_synth)
WHERE substr(mdrm,1,4) IN ('RCFD','RCON','RIAD')
GROUP BY quarter_end, id_rssd, substr(mdrm,5,4)
HAVING COALESCE(max(CASE WHEN substr(mdrm,1,4)='RCFD' THEN value END),
                max(CASE WHEN substr(mdrm,1,4)='RCON' THEN value END),
                max(CASE WHEN substr(mdrm,1,4)='RIAD' THEN value END)) IS NOT NULL
""")

print("1) bank size/type per quarter ...")
con.execute("""
CREATE TEMP TABLE bucket AS
WITH bank AS (
  SELECT quarter_end, id_rssd, any_value(entity_type) AS ft,
         max(CASE WHEN mdrm='COMB2170' THEN value END) AS assets
  FROM t GROUP BY quarter_end, id_rssd)
SELECT quarter_end, id_rssd, ft,
  CASE WHEN assets>=250000000 THEN 'SIZE_250B+'
       WHEN assets>=50000000  THEN 'SIZE_50-250B'
       WHEN assets>=10000000  THEN 'SIZE_10-50B'
       WHEN assets>=1000000   THEN 'SIZE_1-10B'
       ELSE 'SIZE_<1B' END AS szb
FROM bank
""")
print("   bank-quarters:", con.execute("SELECT count(*) FROM bucket").fetchone()[0])

print("2) heavy join -> compact sums ...")
con.execute("""
CREATE TEMP TABLE agg AS
SELECT t.quarter_end, t.mdrm, b.ft, b.szb, sum(t.value) AS val
FROM t JOIN bucket b USING (quarter_end, id_rssd)
GROUP BY t.quarter_end, t.mdrm, b.ft, b.szb
""")
print("   compact rows:", con.execute("SELECT count(*) FROM agg").fetchone()[0])

print("3) roll-ups + filer counts ...")
df = con.execute("""
WITH
 a_all AS (SELECT 'ALL' AS segment,'all' AS segment_type, quarter_end, mdrm, sum(val) AS value FROM agg GROUP BY quarter_end,mdrm),
 a_ft  AS (SELECT ft AS segment,'filing_type' AS segment_type, quarter_end, mdrm, sum(val) AS value FROM agg GROUP BY ft,quarter_end,mdrm),
 a_sz  AS (SELECT szb AS segment,'size_bucket' AS segment_type, quarter_end, mdrm, sum(val) AS value FROM agg GROUP BY szb,quarter_end,mdrm),
 vals  AS (SELECT * FROM a_all UNION ALL SELECT * FROM a_ft UNION ALL SELECT * FROM a_sz),
 nf    AS (
   SELECT 'ALL' AS segment, quarter_end, count(*) AS n FROM bucket GROUP BY quarter_end
   UNION ALL SELECT ft, quarter_end, count(*) FROM bucket GROUP BY ft,quarter_end
   UNION ALL SELECT szb, quarter_end, count(*) FROM bucket GROUP BY szb,quarter_end)
SELECT v.segment, v.segment_type, v.quarter_end, v.mdrm, v.value, COALESCE(nf.n,0) AS n_filers
FROM vals v LEFT JOIN nf ON nf.segment=v.segment AND nf.quarter_end=v.quarter_end
ORDER BY v.segment, v.mdrm, v.quarter_end
""").df()

df.to_csv("ffiec_call_segments_long.csv", index=False)
try: df.to_parquet("ffiec_call_segments_long.parquet", index=False)
except Exception as e: print("(parquet skipped:", e, ")")
print(f"wrote ffiec_call_segments_long.csv: {len(df):,} rows, {df['segment'].nunique()} segments, "
      f"{df['mdrm'].nunique()} line items ({df[df.mdrm.str.startswith('COMB')]['mdrm'].nunique()} combined), "
      f"{df['quarter_end'].nunique()} quarters")
