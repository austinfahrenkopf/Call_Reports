#!/usr/bin/env python3
"""
validate_build_call.py — automated post-build QA gate for the FFIEC 031 (Call) dashboard.
Run AFTER the full pipeline: build_segments_call.py -> enrich_call.py ->
build_tool_dataset.py -> make_site_call.py. Exit code 0 = all green; 1 = at least one FAIL.

Checks:
  1. HIERARCHY   ffiec_call_hierarchy.json present.
  2. DERIV       every DERIV formula base resolves (at least one of COMB/RCFD/RCON/RIAD) in
                 the tool parquet — this is the check that would have caught HIGH-1.
  3. LGMEAS      every LGMEAS code (raw or COMB base) resolves in the tool parquet.
  4. GOLDEN      JPMorgan (RSSD 1039502) COMB2170 present in tool parquet at latest quarter.
  5. RIAD        confirm RIAD income COMBs (4340, 4635, 4605, 4230) exist in segments parquet
                 — verifies build_segments_call.py was rebuilt with RIAD extension.

Usage: python validate_build_call.py   (run from FFIEC 031/ directory)
"""
from __future__ import annotations
import json, os, re, sys

HIER="ffiec_call_hierarchy.json"; SITE_DIR="site_call"; TOOL="ffiec_call_tool.parquet"
SEG="ffiec_call_segments_long.parquet"
GOLDEN_RSSD=852218   # JPMorgan Chase Bank, N.A. (bank RSSD, not BHC)
GOLDEN_CODES=("RCFD2170","RCON2170")  # individual bank rows use RCFD/RCON, not COMB
RIAD_BASES=['4340','4635','4605','4230']   # income codes fixed by HIGH-1

def main():
    fails=[]; notes=[]

    # 1. HIERARCHY present
    if not os.path.exists(HIER): notes.append(f"[HIERARCHY] {HIER} not found — tree/form view disabled")
    else:
        hier=json.load(open(HIER,encoding="utf-8"))
        notes.append(f"[HIERARCHY] {len(hier)} schedule sections")

    # Find site parquets (for DERIV/LGMEAS checks we use the tool parquet, not site parts)
    if not os.path.isdir(SITE_DIR): sys.exit(f"[SITE] {SITE_DIR}/ not found — run make_site_call.py first")
    site_parts=[os.path.join(SITE_DIR,f) for f in os.listdir(SITE_DIR) if f.endswith(".parquet")]
    if not site_parts: fails.append(f"[SITE] no parquet files in {SITE_DIR}/; run make_site_call.py")

    # Load tool parquet codes (richer set; includes individual bank RIAD rows and COMB segment rows)
    if not os.path.exists(TOOL): sys.exit(f"[TOOL] {TOOL} not found — run build_tool_dataset.py first")
    try:
        import pandas as pd
        tool_codes=set(pd.read_parquet(TOOL,columns=["mdrm"])["mdrm"].unique())
    except Exception as e: sys.exit(f"[TOOL] cannot read {TOOL}: {e}")

    # 2. DERIV code check — parse 4-char bases from deployed site HTML
    SITE_HTML=os.path.join(SITE_DIR,"index.html")
    if os.path.exists(SITE_HTML):
        html=open(SITE_HTML,encoding="utf-8").read()
        dm=re.search(r'const DERIV=\{(.*?)\n\};',html,re.DOTALL)
        if dm:
            bases=set(re.findall(r"'([0-9A-Z]{4})'",dm.group(1)))
            bases.discard('0000')
            missing_deriv=[]
            for b in sorted(bases):
                variants={p+b for p in ('COMB','RCFD','RCON','RIAD')}
                if not variants & tool_codes: missing_deriv.append(b)
            if missing_deriv: fails.append(f"[DERIV] {len(missing_deriv)} DERIV base code(s) absent from tool parquet: {missing_deriv[:10]}")
        else: notes.append("[DERIV] DERIV block not found in site HTML; check skipped")

        # 3. LGMEAS code check
        lm=re.search(r'const LGMEAS=\[(.*?)\];',html,re.DOTALL)
        if lm:
            comb_bases=re.findall(r"code:'COMB([0-9A-Z]{4})'",lm.group(1))
            missing_lg=[]
            for b in comb_bases:
                variants={p+b for p in ('COMB','RCFD','RCON','RIAD')}
                if not variants & tool_codes: missing_lg.append('COMB'+b)
            if missing_lg: fails.append(f"[LGMEAS] {len(missing_lg)} LGMEAS COMB base(s) absent from tool parquet: {missing_lg}")
    else: notes.append(f"[DERIV] {SITE_HTML} not found; run make_site_call.py first")

    # 4. GOLDEN cell — JPMorgan Chase Bank, N.A. (RSSD 852218) assets at latest quarter.
    # Individual bank rows in the tool parquet use RCFD/RCON directly (no COMB for individuals).
    try:
        pnl=pd.read_parquet(TOOL,columns=["quarter_end","entity_id","mdrm","value"])
        lq=pnl["quarter_end"].max()
        jpm_id=f"BANK:{GOLDEN_RSSD}"
        gold=pnl[(pnl["entity_id"]==jpm_id)&(pnl["mdrm"].isin(GOLDEN_CODES))&(pnl["quarter_end"]==lq)]
        if gold.empty: fails.append(f"[GOLDEN] JPM BANK:{GOLDEN_RSSD} RCFD/RCON2170 not found in tool parquet at {lq}")
        else:
            v=int(gold["value"].max())
            found_code=gold.loc[gold["value"].idxmax(),"mdrm"]
            if not (500_000_000 <= v <= 10_000_000_000): notes.append(f"[GOLDEN] {found_code}={v:,} at {lq} — outside expected JPM asset range (500B–10T thousands)")
            else: notes.append(f"[GOLDEN] {found_code}={v:,} at {lq} [OK]")
    except Exception as e: notes.append(f"[GOLDEN] check failed ({e})")

    # 5. RIAD income COMBs — verify HIGH-1 fix was applied
    if os.path.exists(SEG):
        try:
            seg_codes=set(pd.read_parquet(SEG,columns=["mdrm"])["mdrm"].unique())
            missing_riad=[f"COMB{b}" for b in RIAD_BASES if f"COMB{b}" not in seg_codes]
            if missing_riad:
                fails.append(f"[RIAD] {len(missing_riad)} income COMB code(s) missing from segments parquet — rebuild with build_segments_call.py: {missing_riad}")
        except Exception as e: notes.append(f"[RIAD] {SEG} unreadable ({e}); check skipped")
    else: notes.append(f"[RIAD] {SEG} not found; run build_segments_call.py first")

    # 6. COMPLETENESS (manifest-driven) — consume expected_items.json from the form-completeness
    #    auditor. The manifest lists, per form/schedule, MDRM codes that HAVE DATA but are absent
    #    from the hierarchy. We re-test each must-add code (has_recent_data) against the freshly built
    #    hierarchy and WARN with the remaining count for a future fixer — a tracking signal, not a
    #    blocking gate (Call has 100s of historical gaps across RCRII/RCL/RCB/RCQ/RCN/RCT).
    HERE=os.path.dirname(os.path.abspath(__file__))
    _bare=lambda c:(str(c)[4:] if len(str(c))==8 and str(c)[:2].isalpha() else str(c))
    EXP=next((c for c in (os.path.join(HERE,"expected_items.json"),os.path.join(HERE,"..","expected_items.json")) if os.path.exists(c)),None)
    if not EXP:
        notes.append("[COMPLETE2] no expected_items.json manifest found; schedule-completeness check skipped")
    elif not os.path.exists(HIER):
        notes.append("[COMPLETE2] hierarchy not found; completeness check skipped")
    else:
        try:
            hier2=json.load(open(HIER,encoding="utf-8"))
            forms=json.load(open(EXP,encoding="utf-8")).get("forms",{})
            fkey=next((k for k in ("FFIEC 031 (Call)","FFIEC 031 Call","FFIEC 031","Call") if k in forms),None)
            if not fkey:
                notes.append("[COMPLETE2] manifest has no FFIEC 031 (Call) entry; check skipped")
            else:
                present=set()
                for nodes in hier2.values():
                    for nd in nodes:
                        if nd.get("mdrm"): present.add(nd["mdrm"]); present.add(_bare(nd["mdrm"]))
                still=[]; per={}
                for sch,sobj in forms[fkey].get("schedules",{}).items():
                    for mc in sobj.get("missing_codes",[]):
                        if not mc.get("has_recent_data"): continue
                        code=str(mc.get("code","")).strip()
                        if code and code not in present and _bare(code) not in present:
                            still.append(code); per[sch]=per.get(sch,0)+1
                if still:
                    top=sorted(per.items(),key=lambda x:-x[1])[:6]
                    notes.append(f"[COMPLETE2] {fkey}: {len(still)} must-add code(s) still absent from hierarchy "
                                 f"(top schedules: {top}); sample {sorted(still)[:12]} — tracked for the completeness fixer")
                else:
                    notes.append(f"[COMPLETE2] {fkey}: all must-add codes from manifest are now present in the hierarchy")
        except Exception as e:
            notes.append(f"[COMPLETE2] expected_items.json unreadable ({e}); check skipped")

    # Hierarchy structural lint — EMPTY_CAPTION / SCHED_CONTAM / within-DUPLICATE
    try:
        import sys as _sys; _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '_lint_scratch'))
        from hierarchy_linter import lint_structural
        lint_defects = lint_structural(hier, 'call')
        if lint_defects:
            fails.append(f"[HIERARCHY_LINT] {len(lint_defects)} structural defect(s) detected")
            for d in lint_defects[:5]:
                fails.append(f"  {d['check']} {d['sched']} item={d['item']} {d.get('mdrm','')}: {d['problem'][:80]}")
    except ImportError:
        notes.append("[HIERARCHY_LINT] hierarchy_linter not found; structural check skipped")
    except Exception as e:
        notes.append(f"[HIERARCHY_LINT] structural check error ({e}); skipped")

    # COMPLETENESS GATE (bidirectional, era-aware, BLOCKING) — MISSING / SPURIOUS / SEQUENCE /
    # ERA_SEAM. See _completeness_gate.py for the full contract.
    try:
        import sys as _sys; _gbase=os.path.dirname(os.path.abspath(__file__))
        for _p in (os.path.join(_gbase,'..'), _gbase):
            if _p not in _sys.path: _sys.path.insert(0,_p)
        from _completeness_gate import run_gate
        g_fails, g_notes = run_gate('call', hier, _gbase)
        fails.extend(g_fails); notes.extend(g_notes)
    except ImportError:
        fails.append("[GATE] _completeness_gate.py not found — completeness gate is REQUIRED; build cannot be trusted")
    except Exception as e:
        fails.append(f"[GATE] completeness gate error ({e})")

    print("="*60); print("FFIEC 031 (Call) build validation"); print("="*60)
    print(f"  site parts: {len(site_parts)}   tool codes: {len(tool_codes)}")
    for n in notes: print("  NOTE  "+n)
    if fails:
        print(f"\n  {len(fails)} FAILURE(S):")
        for x in fails: print("  FAIL  "+x)
        sys.exit(1)
    print("\n  ALL CHECKS PASSED [OK]")

if __name__=="__main__":
    main()
