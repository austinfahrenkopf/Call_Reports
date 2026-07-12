#!/usr/bin/env python3
"""
cdr_parse_call.py
Parse CDR Call bulk zips (cdr_zips/) into a PARTITIONED parquet panel of ALL
commercial banks (filing types 031/041/051). One parquet per quarter.

Per-quarter parquet columns (lean — names live in the roster, not every row):
  quarter_end, id_rssd, entity_type(=filing type 031/041/051), schedule, mdrm, value
Also writes:
  ffiec_call_roster.csv     id_rssd, institution_name, entity_type, first_quarter, last_quarter, n_quarters
  ffiec_call_captions.csv   schedule, mdrm, caption

Read the panel back with DuckDB:  read_parquet('cdr_parquet/*.parquet')

Setup:  pip install pandas pyarrow
Run:    python cdr_parse_call.py                # 031,041,051
        python cdr_parse_call.py --types 031    # just 031
NOTE: delete the cdr_parquet/ folder before re-running if the schema changed.
"""
from __future__ import annotations
import argparse, csv, io, os, re, zipfile
import pandas as pd

ZIPDIR="cdr_zips"; OUTDIR="cdr_parquet"
ROSTER="ffiec_call_roster.csv"; CAP="ffiec_call_captions.csv"
MDRM=re.compile(r"^[A-Z]{4}[A-Z0-9]{4}$")
SCHED=re.compile(r"Schedule\s+([A-Za-z0-9]+)", re.I)

ap=argparse.ArgumentParser(); ap.add_argument("--types", default="031,041,051"); a=ap.parse_args()
TYPES=set(t.strip() for t in a.types.split(","))
os.makedirs(OUTDIR, exist_ok=True)
zips=sorted(f for f in os.listdir(ZIPDIR) if f.lower().endswith(".zip"))
print(f"{len(zips)} quarter zips; types {sorted(TYPES)}")

roster={}; captions={}
for z in zips:
    m=re.search(r"(\d{8})", z)
    if not m: continue
    ymd=m.group(1); qend=f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    outpq=os.path.join(OUTDIR, f"call_{ymd}.parquet")
    if os.path.exists(outpq): print(f"  {qend}: exists, skip"); continue
    try: zf=zipfile.ZipFile(os.path.join(ZIPDIR,z))
    except Exception as e: print(f"  {z}: bad zip {e}"); continue
    names=zf.namelist()
    por=next((n for n in names if "POR" in n.upper()), None)
    if not por: print(f"  {z}: no POR"); continue
    pdf=pd.read_csv(zf.open(por), sep="\t", dtype=str, quotechar='"', on_bad_lines="skip")
    pdf.columns=[c.strip().strip('"') for c in pdf.columns]
    idc=next(c for c in pdf.columns if c.upper()=="IDRSSD")
    ftc=next(c for c in pdf.columns if "FILING TYPE" in c.upper())
    namc=next((c for c in pdf.columns if c.upper()=="FINANCIAL INSTITUTION NAME"), None)
    pdf[idc]=pdf[idc].astype(str).str.strip(); pdf[ftc]=pdf[ftc].astype(str).str.strip()
    pdf=pdf[pdf[ftc].isin(TYPES)]
    keep=set(pdf[idc]);
    if not keep: print(f"  {qend}: 0 filers"); continue
    nmap=dict(zip(pdf[idc], pdf[namc].astype(str).str.strip())) if namc else {}
    ftmap=dict(zip(pdf[idc], pdf[ftc]))
    # roster update
    for rid in keep:
        r=roster.get(rid)
        nm=nmap.get(rid,""); ft=ftmap.get(rid,"")
        if not r: roster[rid]=[nm,ft,qend,qend,1]
        else:
            r[0]=nm or r[0]; r[1]=ft or r[1]; r[3]=qend; r[4]+=1

    seen=set(); recs=[]
    for n in names:
        if "SCHEDULE" not in n.upper(): continue
        sm=SCHED.search(n); sched=sm.group(1).upper() if sm else "?"
        raw=zf.read(n).decode("latin-1", errors="replace").splitlines()
        if len(raw)<3: continue
        codes=[c.strip().strip('"') for c in raw[0].split("\t")]
        caps =[c.strip().strip('"') for c in raw[1].split("\t")]
        for code,cap in zip(codes,caps):
            if MDRM.match(code) and cap and code not in captions: captions[code]=(sched,cap)
        try:
            df=pd.read_csv(io.StringIO("\n".join(raw)), sep="\t", dtype=str, skiprows=[1],
                           quoting=csv.QUOTE_NONE, on_bad_lines="skip")
        except Exception as e:
            print(f"    {sched}: parse skip ({str(e)[:60]})"); continue
        df.columns=[c.strip().strip('"') for c in df.columns]
        ic=next((c for c in df.columns if c.upper()=="IDRSSD"), None)
        if not ic: continue
        df[ic]=df[ic].astype(str).str.strip(); df=df[df[ic].isin(keep)]
        if df.empty: continue
        mcols=[c for c in df.columns if MDRM.match(c)]
        if not mcols: continue
        long=df.melt(id_vars=[ic], value_vars=mcols, var_name="mdrm", value_name="value")
        long["value"]=pd.to_numeric(long["value"], errors="coerce")
        long=long[(long["value"].notna()) & (long["value"]!=0)]
        for rid,md,val in zip(long[ic], long["mdrm"], long["value"]):
            k=(rid,md)
            if k in seen: continue
            seen.add(k); recs.append((rid,sched,md,float(val)))
    if not recs: print(f"  {qend}: no data"); continue
    out=pd.DataFrame(recs, columns=["id_rssd","schedule","mdrm","value"])
    out.insert(0,"quarter_end",qend)
    out["entity_type"]=out["id_rssd"].map(ftmap).fillna("")
    out["id_rssd"]=pd.to_numeric(out["id_rssd"], errors="coerce").astype("Int64")
    out=out[["quarter_end","id_rssd","entity_type","schedule","mdrm","value"]]
    out.to_parquet(outpq, index=False)
    print(f"  {qend}: filers={len(keep)} rows={len(out):,} -> {outpq}")

pd.DataFrame([(k,v[0],v[1],v[2],v[3],v[4]) for k,v in sorted(roster.items())],
    columns=["id_rssd","institution_name","entity_type","first_quarter","last_quarter","n_quarters"]
    ).to_csv(ROSTER, index=False)
pd.DataFrame([(s,c,cap) for c,(s,cap) in sorted(captions.items())],
    columns=["schedule","mdrm","caption"]).to_csv(CAP, index=False)
print(f"\ndone. per-quarter parquet in {OUTDIR}/ ; {ROSTER} ; {CAP}")
