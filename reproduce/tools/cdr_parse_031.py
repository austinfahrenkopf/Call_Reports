#!/usr/bin/env python3
"""
cdr_parse_031.py
Parse the downloaded CDR Call bulk zips (cdr_zips/) into a tidy long panel of
FFIEC 031 filers (commercial banks with foreign offices).

For each quarter zip:
  - read the POR file, keep IDRSSD where 'Financial Institution Filing Type' is in --types
  - read every 'Schedule' file (header row = MDRM codes; 2nd row = captions, skipped),
    keep the kept filers, melt MDRM columns to long, drop blanks/zeros
Output (same schema as the 002 panel):
  ffiec031_panel_long.csv          quarter_end,id_rssd,institution_name,entity_type,mdrm,description,value,source
  ffiec031_captions.csv            mdrm -> caption (from the schedule header rows)

Setup:  pip install pandas pyarrow
Run:    python cdr_parse_031.py              # filing type 031
        python cdr_parse_031.py --types 031,041,051   # all commercial banks
"""
from __future__ import annotations
import argparse, csv, io, os, re, zipfile
import pandas as pd

ZIPDIR = "cdr_zips"
OUT_CSV = "ffiec031_panel_long.csv"
OUT_PQ  = "ffiec031_panel_long.parquet"
CAP_CSV = "ffiec031_captions.csv"
MDRM = re.compile(r"^[A-Z]{4}[A-Z0-9]{4}$")

ap = argparse.ArgumentParser()
ap.add_argument("--types", default="031")
a = ap.parse_args()
TYPES = set(t.strip() for t in a.types.split(","))

zips = sorted(f for f in os.listdir(ZIPDIR) if f.lower().endswith(".zip"))
print(f"{len(zips)} quarter zips in {ZIPDIR}/  (filing types: {sorted(TYPES)})")

with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    csv.writer(f).writerow(["quarter_end","id_rssd","institution_name",
                            "entity_type","mdrm","description","value","source"])
captions = {}
grand = 0

for z in zips:
    m = re.search(r"(\d{8})", z)
    if not m: continue
    ymd = m.group(1); qend = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    try:
        zf = zipfile.ZipFile(os.path.join(ZIPDIR, z))
    except Exception as e:
        print(f"  {z}: bad zip {e}"); continue
    names = zf.namelist()
    por = next((n for n in names if "POR" in n.upper()), None)
    if not por:
        print(f"  {z}: no POR"); continue
    pdf = pd.read_csv(zf.open(por), sep="\t", dtype=str, quotechar='"')
    pdf.columns = [c.strip().strip('"') for c in pdf.columns]
    idc = next(c for c in pdf.columns if c.upper()=="IDRSSD")
    ftc = next(c for c in pdf.columns if "FILING TYPE" in c.upper())
    namc= next((c for c in pdf.columns if c.upper()=="FINANCIAL INSTITUTION NAME"), None)
    pdf = pdf[pdf[ftc].astype(str).str.strip().isin(TYPES)]
    keep = set(pdf[idc].astype(str).str.strip())
    names_map = dict(zip(pdf[idc].astype(str).str.strip(),
                         pdf[namc].astype(str).str.strip())) if namc else {}
    ftmap = dict(zip(pdf[idc].astype(str).str.strip(), pdf[ftc].astype(str).str.strip()))
    if not keep:
        print(f"  {z}: 0 filers of {sorted(TYPES)}"); continue

    rows_out = []
    for n in names:
        if "SCHEDULE" not in n.upper(): continue
        raw = zf.read(n).decode("latin-1", errors="replace").splitlines()
        if len(raw) < 3: continue
        codes = [c.strip().strip('"') for c in raw[0].split("\t")]
        caps  = [c.strip().strip('"') for c in raw[1].split("\t")]
        for code, cap in zip(codes, caps):
            if MDRM.match(code) and cap and code not in captions:
                captions[code] = cap
        df = pd.read_csv(io.StringIO("\n".join(raw)), sep="\t", dtype=str,
                         skiprows=[1], quotechar='"')
        df.columns = [c.strip().strip('"') for c in df.columns]
        ic = next((c for c in df.columns if c.upper()=="IDRSSD"), None)
        if not ic: continue
        df[ic] = df[ic].astype(str).str.strip()
        df = df[df[ic].isin(keep)]
        if df.empty: continue
        mcols = [c for c in df.columns if MDRM.match(c)]
        if not mcols: continue
        long = df.melt(id_vars=[ic], value_vars=mcols, var_name="mdrm", value_name="value")
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        long = long[(long["value"].notna()) & (long["value"] != 0)]
        for rssd, mdrm, val in zip(long[ic], long["mdrm"], long["value"]):
            rows_out.append((qend, rssd, names_map.get(rssd,""), ftmap.get(rssd,""),
                             mdrm, "", float(val), "CDR"))
    # de-dup (same MDRM can appear in >1 schedule file part) keep first
    seen=set(); dedup=[]
    for r in rows_out:
        k=(r[1],r[4])
        if k in seen: continue
        seen.add(k); dedup.append(r)
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(dedup)
    grand += len(dedup)
    print(f"  {qend}: filers={len(keep)} rows+={len(dedup):,} total={grand:,}")

pd.DataFrame(sorted(captions.items()), columns=["mdrm","caption"]).to_csv(CAP_CSV, index=False)
print(f"\nwrote {OUT_CSV} ({grand:,} rows) and {CAP_CSV} ({len(captions)} captions)")
try:
    pd.read_csv(OUT_CSV, dtype={"id_rssd":"int64"}).to_parquet(OUT_PQ, index=False)
    print(f"wrote {OUT_PQ}")
except Exception as e:
    print(f"(parquet skipped: {e} — CSV is complete)")
