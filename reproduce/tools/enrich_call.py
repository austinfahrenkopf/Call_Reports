#!/usr/bin/env python3
"""
enrich_call.py
Adds titles + start/end validity dates to the Call segment data, including the new
COMB#### combined items (labeled from their base RCFD/RCON item).

Outputs:
  ffiec_call_dictionary.csv    mdrm, schedule, title, caption, start_date, end_date
  ffiec_call_segments.parquet  segment data + schedule + description

Setup:  pip install requests pandas pyarrow
Run:    python enrich_call.py
"""
from __future__ import annotations
import csv, io, zipfile
import requests, pandas as pd

URL="https://www.federalreserve.gov/apps/mdrm/pdf/MDRM.zip"; UA={"User-Agent":"Mozilla/5.0 (research)"}
SEG_IN="ffiec_call_segments_long.parquet"; SEG_OUT="ffiec_call_segments.parquet"
CAP="ffiec_call_captions.csv"; DICT="ffiec_call_dictionary.csv"

print("downloading Fed MDRM dictionary ...")
r=requests.get(URL,headers=UA,timeout=120); r.raise_for_status()
zf=zipfile.ZipFile(io.BytesIO(r.content))
member=max((m for m in zf.namelist() if m.lower().endswith(".csv")), key=lambda m:zf.getinfo(m).file_size)
rows=list(csv.reader(io.StringIO(zf.read(member).decode("latin-1",errors="replace"))))
hi=next(i for i,row in enumerate(rows) if any(c.strip().lower()=="mnemonic" for c in row))
hdr=[c.strip() for c in rows[hi]]
def col(*names):
    for n in names:
        for i,h in enumerate(hdr):
            if h.lower()==n.lower(): return i
    for n in names:
        for i,h in enumerate(hdr):
            if n.lower() in h.lower(): return i
    return None
ci_mn=col("Mnemonic"); ci_ic=col("Item Code","Item"); ci_nm=col("Item Name","Name"); ci_sd=col("Start Date"); ci_ed=col("End Date")
name={}; start={}; end={}
for row in rows[hi+1:]:
    if ci_mn is None or ci_ic is None or ci_nm is None: break
    if len(row)<=max(ci_mn,ci_ic,ci_nm): continue
    code=(row[ci_mn].strip()+row[ci_ic].strip()).upper()
    if len(code)!=8: continue
    if row[ci_nm].strip(): name.setdefault(code,row[ci_nm].strip())
    if ci_sd is not None and len(row)>ci_sd: start.setdefault(code,row[ci_sd].strip())
    if ci_ed is not None and len(row)>ci_ed: end.setdefault(code,row[ci_ed].strip())
print(f"  MDRM names: {len(name):,}")

try:
    capdf=pd.read_csv(CAP); sched_map=dict(zip(capdf["mdrm"],capdf["schedule"])); capt_map=dict(zip(capdf["mdrm"],capdf["caption"]))
except Exception:
    sched_map,capt_map={},{}

def info(code):
    """return (schedule, title, caption, start, end) handling COMB#### codes"""
    if code.startswith("COMB"):
        base=code[4:]
        for pfx in ("RCFD","RCON","RIAD"):
            full=pfx+base
            if name.get(full) or capt_map.get(full):
                t=(name.get(full) or capt_map.get(full))
                return (sched_map.get(full,""), (t+" (RCFD/RCON/RIAD combined)"), capt_map.get(full,""),
                        start.get(full,""), end.get(full,""))
        return ("COMB", "Combined RCFD/RCON/RIAD ("+base+")", "", "", "")
    return (sched_map.get(code,""), (name.get(code,"") or capt_map.get(code,"")), capt_map.get(code,""),
            start.get(code,""), end.get(code,""))

seg=pd.read_parquet(SEG_IN)
codes=sorted(set(seg["mdrm"]) | set(sched_map))
pd.DataFrame([(c,)+info(c) for c in codes],
             columns=["mdrm","schedule","title","caption","start_date","end_date"]).to_csv(DICT,index=False)
print(f"  wrote {DICT} ({len(codes)} codes)")

seg["schedule"]=seg["mdrm"].map(lambda c: info(c)[0])
seg["description"]=seg["mdrm"].map(lambda c: info(c)[1])
seg.to_parquet(SEG_OUT,index=False)
print(f"  wrote {SEG_OUT}: {len(seg):,} rows with schedule + description")
