#!/usr/bin/env python3
"""
number_call_hierarchy.py — OVERLAY form item-numbers + nesting depth onto the CURATED
ffiec_call_hierarchy.json, without regenerating it from CDR (which would re-introduce the
634 SILENT_EMPTY codes the §35 cleanup dropped at the JSON level).

Approach (display-layer only — codes are NEVER added/removed/changed):
  1. Load the curated hierarchy (the gate-green file).
  2. Parse a current Call filing PDF for {mdrm -> (item, depth)} using the same parse_item
     logic as build_hierarchy.py (the filing renders the full RC-* item outline).
  3. For every existing leaf node whose mdrm the PDF supplies, set node['item']/['depth'].
     Nodes the PDF don't cover keep their current item/depth (usually null -> flat tail).
  4. Apply SCHED_CORRECTIONS to fix items where "first occurrence wins" assigned the wrong
     schedule's item number (e.g. RCFD1754 first appears in RC-B at item 8, but its RC
     Balance Sheet item is 2).
  5. Sort each schedule by item number so the tree renders in correct form order.
  6. Write back. Code set is byte-for-byte identical, so the completeness gate (SPURIOUS /
     MISSING) is unaffected; only display item/depth change.

Run from FFIEC 031/:  python number_call_hierarchy.py --pdf ../_form_pdfs/Call_Cert18409_2026-03-31.pdf
"""
from __future__ import annotations
import argparse, json, os, re, sys

HIER = "ffiec_call_hierarchy.json"

# Schedule-specific item corrections: override wrong items set by "first occurrence wins"
# PDF overlay. Keys = (schedule, mdrm); values = (item, depth) or (None, None) to clear.
# RCON* domestic-only codes do not appear as separate RC line items in the 031 form.
# NOTE (§55): RCFD1754 and RCFD3163 are NOT in Schedule RC per the 2026 template
# (FFIEC031_202606_f.pdf p.15): RC item 2 "Securities" = header (2.a HTM=RCFDJJ34, 2.b AFS=RCFD1773),
# and RC item 10 "Intangible assets" = single line RCFD2143 (Goodwill RCFD3163 lives in RC-M item 2.b,
# whose total RCFD2143 equals RC item 10). The stray RCFD1754@RC-item-2 and RCFD3163@RC-item-10.a were
# REMOVED directly from ffiec_call_hierarchy.json (the overlay's code-set assert forbids node removal here).
# RCFD1754 stays at RC-B/RC-R/RC-RII item 8; RCFD3163 stays at RC-M item 2.b. Do NOT re-add to RC.
SCHED_CORRECTIONS: dict[tuple[str, str], tuple] = {
    ("RC", "RCON3190"): (None, None),  # domestic-only other borrowed money; no separate RC item
    ("RC", "RCON1754"): (None, None),  # domestic-only HTM total; no separate RC item
    ("RC", "RCON1773"): (None, None),  # domestic-only AFS total; no separate RC item
    ("RC", "RCONJA22"): (None, None),  # domestic equity securities; no separate RC item
    ("RC", "RCON3545"): (None, None),  # domestic trading assets; no separate RC item
    ("RC", "RCON3548"): (None, None),  # domestic trading liabilities; no separate RC item
    ("RC", "RCONB989"): (None, None),  # domestic sec. purchased; no separate RC item
    ("RC", "RCONB995"): (None, None),  # domestic sec. sold; no separate RC item
    ("RC", "RCFD6724"): ("M.1", 2),    # Memoranda item — depth 2 (M prefix = section level)
    ("RC", "RCON8678"): ("M.2", 2),    # Memoranda item — depth 2
}


def item_sort_key(item_str):
    """Convert item number to sort tuple for hierarchical ordering."""
    parts = re.split(r'[\.\(\)]+', (item_str or '').strip().rstrip('.'))
    key = []
    for p in (p for p in parts if p):
        try:
            key.append((0, int(p)))
        except ValueError:
            key.append((1, p.lower()))
    return key or [(1, '')]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", nargs="+", required=True)
    ap.add_argument("--out", default=HIER)
    a = ap.parse_args()

    # reuse the proven PDF item/depth parser from the builder
    import build_hierarchy as bh
    pdfmap = bh.depth_from_pdf(a.pdf)   # {mdrm: (item, depth)}
    print(f"  PDF supplied item/depth for {len(pdfmap)} codes")

    hier = json.load(open(HIER, encoding="utf-8"))
    codes_before = sorted(n.get("mdrm") for v in hier.values() for n in v if n.get("mdrm"))

    applied = 0
    for sch, nodes in hier.items():
        for n in nodes:
            m = n.get("mdrm")
            if m and m in pdfmap:
                it, dp = pdfmap[m]
                if it:
                    n["item"], n["depth"] = it, dp
                    applied += 1

    # Apply schedule-specific corrections (overrides wrong "first occurrence" assignments)
    corrected = 0
    for sch, nodes in hier.items():
        for n in nodes:
            m = n.get("mdrm")
            if m and (sch, m) in SCHED_CORRECTIONS:
                it, dp = SCHED_CORRECTIONS[(sch, m)]
                n["item"], n["depth"] = it, dp
                corrected += 1

    # Sort each schedule by item number so the tree renders in correct form order
    for sch, nodes in hier.items():
        nodes.sort(key=lambda x: item_sort_key(x.get("item") or ""))
        for i, node in enumerate(nodes):
            node["order"] = i

    codes_after = sorted(n.get("mdrm") for v in hier.values() for n in v if n.get("mdrm"))
    assert codes_before == codes_after, "CODE SET CHANGED — aborting (overlay must not touch codes)"

    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(hier, f, ensure_ascii=False, indent=0)
    os.replace(tmp, a.out)
    json.load(open(a.out, encoding="utf-8"))  # verify readable
    tot = sum(len(v) for v in hier.values())
    withd = sum(1 for v in hier.values() for n in v if n.get("depth") is not None)
    print(f"  overlaid item/depth onto {applied} leaf nodes; corrected {corrected} schedule-specific items")
    print(f"  wrote {a.out}: {tot} nodes ({withd} now numbered); code set UNCHANGED ({len(codes_after)} codes); "
          f"verified, {os.path.getsize(a.out)} bytes")


if __name__ == "__main__":
    main()
