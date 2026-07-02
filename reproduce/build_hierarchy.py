#!/usr/bin/env python3
"""
build_hierarchy.py
Build ffiec_call_hierarchy.json = per-schedule ORDERED line-item map used by the
explorer's hierarchical field picker and the call-report (form-replica) view.

Two sources, merged:
  1) CDR schedule files (cdr_zips/)  -> authoritative ITEM ORDER + captions, all form
     types, every code we actually have. Column order in each schedule file = form order.
  2) A rendered Call PDF (optional)  -> item NUMBERS + nesting DEPTH (1 > 1.a > 1.a.1),
     matched onto codes by mdrm. Pass one or more with --pdf.

Output JSON:
  { "RC-N": [ {"mdrm","caption","order","item","depth"}, ... ], "RC-C": [...], ... }
  item/depth are null where the PDF didn't supply them (picker falls back to flat order).

Run:  python build_hierarchy.py                         # CDR only
      python build_hierarchy.py --pdf Call_Cert18409_12312025.PDF   # CDR + depth
"""
from __future__ import annotations
import argparse, json, os, re, zipfile

ZIPDIR="cdr_zips"; OUT="ffiec_call_hierarchy.json"
MDRM=re.compile(r"^[A-Z]{4}[A-Z0-9]{4}$")
SCHED=re.compile(r"Schedule\s+([A-Za-z0-9-]+)", re.I)
# item-number prefix at start of a PDF line: 1  1.a  1.a.1  M.1.a  M.10.b
# The PDF glues value+code onto the number (e.g. "3.b.0RCFDB989b."), so we grab a
# generous candidate then keep only valid item segments (M / 1-2 digits / single a-z).
ITEMHEAD=re.compile(r"^((?:M\.)?\d+(?:\.[0-9A-Za-z,]+)*)")
PDFCODE=re.compile(r"(RC[A-Z]{2}[A-Z0-9]{4}|RIAD[A-Z0-9]{4})")
def parse_item(s):
    m=ITEMHEAD.match(s.strip())
    if not m: return None, None
    parts=m.group(1).split("."); out=[]; i=0
    if parts and parts[0]=="M": out.append("M"); i=1
    if i>=len(parts) or not re.fullmatch(r"\d{1,2}", parts[i]): return None, None
    out.append(parts[i]); i+=1
    for seg in parts[i:]:
        if re.fullmatch(r"[a-z]", seg) or re.fullmatch(r"[1-9]\d?", seg): out.append(seg)
        else: break
    item=".".join(out); depth=len(out)-(1 if out[0]=="M" else 0)
    return item, depth

def order_from_cdr():
    """schedule -> ordered unique list of (mdrm, caption). Uses the LATEST zip per code."""
    order={}; cap={}
    zips=sorted(f for f in os.listdir(ZIPDIR) if f.lower().endswith(".zip")) if os.path.isdir(ZIPDIR) else []
    for z in zips:  # ascending -> later quarters overwrite captions/order with newest layout
        try: zf=zipfile.ZipFile(os.path.join(ZIPDIR,z))
        except Exception: continue
        for n in zf.namelist():
            if "SCHEDULE" not in n.upper(): continue
            sm=SCHED.search(n); sch=("RC-"+sm.group(1)[-1]).upper() if sm and len(sm.group(1))>2 else (sm.group(1).upper() if sm else "?")
            sch=sm.group(1).upper() if sm else "?"
            try:
                raw=zf.read(n).decode("latin-1","replace").splitlines()
                if len(raw)<2: continue
                codes=[c.strip().strip('"') for c in raw[0].split("\t")]
                caps =[c.strip().strip('"') for c in raw[1].split("\t")]
            except Exception: continue
            seq=order.setdefault(sch, []); seen={m for m,_ in seq}
            for c,cp in zip(codes,caps):
                if MDRM.match(c):
                    cap[c]=cp or cap.get(c,"")
                    if c not in seen: seq.append((c,cap[c])); seen.add(c)
    # refresh captions to newest
    return {s:[(m,cap.get(m,cp)) for m,cp in seq] for s,seq in order.items()}

def depth_from_pdf(paths):
    """mdrm -> (item_number, depth) parsed from rendered Call PDF(s)."""
    out={}
    try: import pypdf
    except Exception:
        print("  (pypdf not installed; skipping PDF depth)"); return out
    for p in paths:
        if not os.path.exists(p): print(f"  PDF not found: {p}"); continue
        r=pypdf.PdfReader(p)
        for pg in r.pages:
            for ln in (pg.extract_text() or "").splitlines():
                item,depth=parse_item(ln)
                if not item: continue
                for code in PDFCODE.findall(ln):
                    out.setdefault(code, (item, depth))   # first occurrence wins
    return out

def load_titles():
    """mdrm -> full Fed MDRM name (from ffiec_call_dictionary.csv 'title'); fuller than the
    truncated CDR header caption (e.g. RCFD1258)."""
    import csv, os
    t={}
    if os.path.exists("ffiec_call_dictionary.csv"):
        for row in csv.DictReader(open("ffiec_call_dictionary.csv", encoding="latin-1")):
            m=(row.get("mdrm") or "").strip(); ti=(row.get("title") or "").strip()
            if m and ti: t[m]=ti
    return t

OVERRIDES="ffiec_call_hierarchy_overrides.json"

def apply_overrides(hier):
    """Apply depth corrections and header rows from ffiec_call_hierarchy_overrides.json."""
    if not os.path.exists(OVERRIDES):
        return
    ov=json.load(open(OVERRIDES,encoding="utf-8"))
    # drop_codes: remove specific mdrm from a specific schedule (to re-add corrected below)
    drops=set()
    for d in ov.get("drop_codes",[]):
        key=d.get("key"); mdrm=d.get("mdrm")
        if key and mdrm: drops.add((key,mdrm))
    ndropped=0
    for key in list(hier):
        before=len(hier[key])
        hier[key]=[r for r in hier[key] if (key,r["mdrm"]) not in drops]
        ndropped+=before-len(hier[key])
    if ndropped: print(f"  [overrides] dropped {ndropped} codes via drop_codes")
    # force_rows: add supplemental or corrected rows with explicit depth/order
    added=0
    for row in ov.get("force_rows",[]):
        key=row.get("key"); mdrm=row.get("mdrm","")
        if not key: continue
        seq=hier.setdefault(key,[])
        existing={x["mdrm"] for x in seq if x.get("mdrm")}
        if mdrm and mdrm in existing: continue
        item=row.get("item")
        if not mdrm and item and any(x.get("item")==item and not x.get("mdrm") for x in seq): continue
        seq.append({"mdrm":mdrm,"caption":row.get("caption",mdrm),
                    "order":row.get("order",len(seq)),"item":item,
                    "depth":row.get("depth")})
        added+=1
    for key in hier:
        hier[key].sort(key=lambda x: x.get("order") or 0)
    print(f"  [overrides] applied {added} force_rows from {OVERRIDES}")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--pdf", nargs="*", default=[]); a=ap.parse_args()
    order=order_from_cdr()
    if not order:
        print("No cdr_zips/ schedule files found — run where cdr_zips/ lives.");
    depth=depth_from_pdf(a.pdf) if a.pdf else {}
    titles=load_titles()
    def nicecap(m, cp):
        ti=titles.get(m)
        # prefer the full dictionary title unless it's empty; keep CDR caption as fallback
        return ti if ti else cp
    hier={}; nfull=0
    for sch in sorted(order):
        rows=[]
        for i,(m,cp) in enumerate(order[sch]):
            it,dp=depth.get(m,(None,None))
            cap=nicecap(m,cp)
            if titles.get(m): nfull+=1
            rows.append({"mdrm":m,"caption":cap,"order":i,"item":it,"depth":dp})
        hier[sch]=rows
    apply_overrides(hier)
    print(f"  captions: {nfull} from full MDRM dictionary, rest from CDR header")
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hier, f, ensure_ascii=False, indent=0)
    os.replace(tmp, OUT)
    json.load(open(OUT, encoding="utf-8"))  # verify readable
    ncodes=sum(len(v) for v in hier.values()); withd=sum(1 for v in hier.values() for r in v if r["depth"])
    print(f"wrote {OUT}: {len(hier)} schedules, {ncodes} items, {withd} with PDF depth; "
          f"verified, {os.path.getsize(OUT)} bytes")

if __name__=="__main__": main()
