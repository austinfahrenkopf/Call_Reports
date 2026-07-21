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
import argparse, csv, io, os, re, sys, zipfile
import pandas as pd
from cdr_merge_lib import merge_roster, merge_captions, guard_no_shrink  # AQ-C16-5: merge-don't-replace on incremental runs

ZIPDIR="cdr_zips"; OUTDIR="cdr_parquet"
ROSTER="ffiec_call_roster.csv"; CAP="ffiec_call_captions.csv"
MDRM=re.compile(r"^[A-Z]{4}[A-Z0-9]{4}$")
SCHED=re.compile(r"Schedule\s+([A-Za-z0-9]+)", re.I)

ap=argparse.ArgumentParser(); ap.add_argument("--types", default="031,041,051"); a=ap.parse_args()
TYPES=set(t.strip() for t in a.types.split(","))
os.makedirs(OUTDIR, exist_ok=True)
zips=sorted(f for f in os.listdir(ZIPDIR) if f.lower().endswith(".zip"))
print(f"{len(zips)} quarter zips; types {sorted(TYPES)}")

roster={}; captions={}; parsed_qends=[]; skipped_any=False
for z in zips:
    m=re.search(r"(\d{8})", z)
    if not m: continue
    ymd=m.group(1); qend=f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"
    outpq=os.path.join(OUTDIR, f"call_{ymd}.parquet")
    if os.path.exists(outpq): print(f"  {qend}: exists, skip"); skipped_any=True; continue
    parsed_qends.append(qend)
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
        # C14-A: strip a trailing "%" so RC-R I ratio strings (e.g. "16.2529%") survive numeric
        # coercion as percent-points (NO *100/÷100 — TD raw 18.2813% must equal OPEN-C's 18.2813).
        # RCRI is the ONLY %-bearing schedule in the zip (STEP-0 verified), so this is loss-free elsewhere.
        long["value"]=pd.to_numeric(long["value"].astype("string").str.rstrip("%"), errors="coerce")
        # C14-C: keep filed zeros — a filed 0 is data (removed the `& (long["value"]!=0)` clause;
        # downstream COMB coalesce + JS ??/!=null are null-safe, so 0 never masquerades as missing).
        long=long[long["value"].notna()]
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

# AQ-C16-5 (2026-07-16): on an INCREMENTAL run (quarters skipped because their parquet exists),
# the run dicts cover only the newly parsed quarters — writing them verbatim would collapse the
# roster/caption history. Merge with the existing CSVs instead (old rows baseline, shrink guard
# fails loud). From-scratch runs (nothing skipped / no existing CSVs) are byte-identical to the
# old behavior: merge_* pass the run dicts straight through.
_old_roster = pd.read_csv(ROSTER, dtype=str).to_dict("records") if (skipped_any and os.path.exists(ROSTER)) else None
_old_caps   = pd.read_csv(CAP,    dtype=str).to_dict("records") if (skipped_any and os.path.exists(CAP))    else None
if _old_roster is not None:
    print(f"incremental run: merging {len(roster)} run-roster rows into {len(_old_roster)} existing "
          f"(parsed {len(parsed_qends)} quarter(s), skipped the rest)")
_ro = pd.DataFrame(merge_roster(_old_roster, roster, parsed_qends),
    columns=["id_rssd","institution_name","entity_type","first_quarter","last_quarter","n_quarters"])
# AQ-S6R-3 (dress-rehearsal finding): merge_roster's "n_quarters = old + run" double-counts when
# a parsed quarter RE-covers one already inside the old span (delete-and-re-parse; observed +1
# x4,336 in the S6R rehearsal — the lib's overlap WARN fired but only to stderr, and it still
# wrote the corrupted count). The per-quarter parquets on disk are ground truth: recompute
# first/last/n_quarters per filer from them, cheaply (2-column projection).
try:
    import duckdb as _dk
    _facts = _dk.sql(
        f"select cast(id_rssd as varchar) id_rssd, min(quarter_end) fq, max(quarter_end) lq, "
        f"count(distinct quarter_end) nq from read_parquet('{OUTDIR}/*.parquet') group by 1").df()
    _fmap = {r.id_rssd: (r.fq, r.lq, int(r.nq)) for r in _facts.itertuples()}
    _hit = _ro["id_rssd"].astype(str).map(_fmap)
    _has = _hit.notna()
    _ro.loc[_has, "first_quarter"] = _hit[_has].map(lambda t: t[0])
    _ro.loc[_has, "last_quarter"]  = _hit[_has].map(lambda t: t[1])
    _ro.loc[_has, "n_quarters"]    = _hit[_has].map(lambda t: t[2])
    print(f"roster quarter-facts recomputed from {OUTDIR}/ ground truth for {int(_has.sum())} filers (AQ-S6R-3)")
except Exception as _e:
    print(f"[cdr_parse] WARN: ground-truth n_quarters recompute failed ({_e}) — "
          f"falling back to merge arithmetic; counts may double on re-parsed quarters", file=sys.stderr)
_ca = pd.DataFrame(merge_captions(_old_caps, captions), columns=["schedule","mdrm","caption"])
guard_no_shrink(ROSTER, len(_ro), "roster")   # AQ-C16-5 write-site guard: never overwrite history with less
guard_no_shrink(CAP,    len(_ca), "captions")
_ro.to_csv(ROSTER, index=False)
_ca.to_csv(CAP, index=False)
print(f"\ndone. per-quarter parquet in {OUTDIR}/ ; {ROSTER} ; {CAP}")
