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
# RC-3 fix (2026-07-08 cycle-11 audit; REWORKED at cycle-11 execution after a pre-regen dry-run
# against the real regen input Call_Cert18409_12312025.PDF proved the as-reviewed regex wrong in
# three ways — evidence: _verify/evidence/c11_step2_call_depth_BEFORE.txt + the step-2 gate log):
#   1. The CDR filed PDF prints Part headings as "Schedule RI-B Part I - ..." with NO comma; the
#      old ",\s*Part" clause required one, so every Part-split schedule heading missed.
#   2. Worse, on a miss the regex BACKTRACKED group 1 from "RI-B" to "RI" and matched the hyphen
#      INSIDE the schedule descriptor as the heading dash, silently setting cur_sch to the wrong
#      key (RI-B pages scanned as "RI", RC-C pages as "RC") — the (?![\w-]) guard now forbids
#      group 1 from stopping immediately before a hyphen/word char, killing that backtrack.
#   3. Un-anchored matching let a mid-line CROSS-REFERENCE with a dash flip the scope: RI item
#      11's own caption "(Describe on Schedule RI-E - Explanations)" re-keyed the rest of
#      Schedule RI (items 12-14 + all Memoranda) onto RIE. Real headings in the CDR rendering
#      always START the extracted line, so the regex is now ^-anchored (dry-run confirmed zero
#      heading loss from anchoring; the old "page furniture may precede the heading" worry does
#      not occur in this input class). "must equal" substring check kept as a second guard.
# The optional ",? Part I/II" clause is captured so the derived key matches this project's own
# CDR-derived Part-I/II key convention (RCCI/RCCII, RCEI/RCEII, RIBI/RIBII, RCRI/RCRII).
# Known/accepted consequence of (schedule,mdrm) scoping: CDR-artifact keys with no printed page of
# their own (RCE, RCR legacy, RCRIA, RCRIB, RICI, RICII, SU) get NO pdf depth — each is either
# removed from the engine's FORM_ORDER, explicitly dropped via overrides, or a documented
# duplicate whose form-true rows live under the real key (see hierarchy_audit_call_B.md).
SCHED_HEADING=re.compile(r"^\s*Schedule\s+([A-Za-z]{2}(?:-[A-Za-z])?)(?![\w-])\s*(?:,?\s*Part\s+(I{1,2})\b)?\s*[—–-]", re.I)
# item-number prefix at start of a PDF line: 1  1.a  1.a.1  M.1.a  M.10.b
# The PDF glues value+code onto the number (e.g. "3.b.0RCFDB989b."), so we grab a
# generous candidate then keep only valid item segments (M / 1-2 digits / single a-z).
ITEMHEAD=re.compile(r"^((?:M\.)?\d+(?:\.[0-9A-Za-z,]+)*)")
PDFCODE=re.compile(r"(RC[A-Z]{2}[A-Z0-9]{4}|RIAD[A-Z0-9]{4})")
def parse_item(s):
    st=s.strip()
    m=ITEMHEAD.match(st)
    if not m: return None, None
    # RC-2 fix (2026-07-08 cycle-11 audit, hierarchy_audit_call_A.md): a genuine printed item
    # marker on the form is never immediately followed by ")" -- that exact signature belongs
    # only to the tail fragment of a wrapped "(must equal Schedule X, item N)" cross-reference
    # line (e.g. RIA's own item 4 text is "4. Net income (loss) attributable to bank (must equal
    # Schedule RI, item 14)..."; when the PDF extractor's line wrap puts "14)" at the start of an
    # extracted line, ITEMHEAD's start-anchored match captures "14" as if it were THAT line's own
    # item number, and if the code also appears on that same wrapped line it gets stamped with the
    # cross-referenced schedule's item number instead of its own). Reject only that narrow
    # signature -- a bare leading number immediately closed by ")" -- so a cross-reference number
    # can never be captured as an item label; every other item-marker shape (followed by ".",
    # whitespace, another digit run, etc.) is untouched, so this cannot regress any currently-
    # correct row. Confirmed root cause of RIA item 4->"14", RCEII item 6->"13.b", RCF item 7->"11",
    # RCG item 5->"20" (4 independent instances found in the audit).
    if st[m.end():m.end()+1] == ")":
        return None, None
    parts=m.group(1).split("."); out=[]; i=0
    if parts and parts[0]=="M": out.append("M"); i=1
    if i>=len(parts) or not re.fullmatch(r"\d{1,2}", parts[i]): return None, None
    out.append(parts[i]); i+=1
    for seg in parts[i:]:
        if re.fullmatch(r"[a-z]", seg) or re.fullmatch(r"[1-9]\d?", seg): out.append(seg)
        else: break
    item=".".join(out); depth=len(out)-(1 if out[0]=="M" else 0)
    return item, depth

SEG_ALPHA = re.compile(r"^[a-z]$")
SEG_DIGIT = re.compile(r"^[1-9]\d?$")

def clean_glued_items(rows, sch=""):
    """Undo two glued-PDF-text artifacts seen when --pdf is a FILED (not blank) Call report,
    where the fillable-form field label glues a filer-assigned serial count onto the real item
    number (2026-07 audit; reworked 2026-07 round 2 after an adversarial review found pass 1
    was grouping by RAW ROWS instead of DISTINCT ITEM LABELS — on matrix schedules several mdrm
    codes legitimately share one item label, one per value column, and the old code lumped every
    row under a parent into a single group and renumbered them by position, silently shredding
    matrix columns into fake distinct sub-items). Operates within one schedule's `rows` list
    (mutated in place). `sch` is the schedule key, used only to label the stdout diff summary.

    1) A bare digit glued onto a real letter sub-item: the PDF line for a free-text "Disclose
       component" row (e.g. RI-E 1.h) is followed by the filer's own count of how many
       components they typed in (".1", ".2", ...), so parse_item() absorbs it as an extra
       segment ("1.h.1", "1.h.2", ...). That serial count is filer/quarter-specific, not a form
       item number, EXCEPT where the form legitimately has more than one such sub-item under
       the same letter (e.g. RC-B 4.a.(1)/4.a.(2)/4.a.(3), each a 4-column matrix row) — those
       show up as two-or-more *different original item-label strings* under the same (prefix,
       parent, letter), each label carried by one-or-more mdrm codes (one per value column).
       Grouping is scoped by mdrm PREFIX (RCFD/RCON/RCFN/RIAD) in addition to the item prefix,
       because the "RC" schedule mixes FFIEC 031 (RCFD-numbered) and FFIEC 041 (RCON-numbered)
       item conventions under one key — an RCFD code and an unrelated RCON code must never be
       paired just because they happen to parse to the same (parent, letter).

       Pass 1 groups rows by (prefix, parent) and then by their DISTINCT ORIGINAL ITEM LABEL
       within that group (never by raw row), so a label carried by several matrix-column rows
       stays one group of several rows, not several groups of one row each:
         - exactly ONE distinct label under (prefix, parent) -> the digit is spurious on every
           row that carries it; drop it on ALL of them at once (restores e.g. "1.h", or, for a
           multi-column matrix such as RC-B RCON 13.a's two columns, restores bare "13.a" on
           both rows together) -- UNLESS a distinct row already carries that bare parent item
           (would create a duplicate label), in which case every row in the group is left
           untouched and the collision is reported to stdout.
         - TWO OR MORE distinct labels under (prefix, parent) -> real form sub-items; each
           label keeps its own row(s) as one group (matrix columns stay together) and is
           renamed to parenthesized notation using ITS OWN original trailing digit, e.g. label
           "4.a.1" (however many rows/columns carry it) -> "4.a.(1)", label "4.a.2" -> "4.a.(2)".
           This is a direct digit->(digit) mapping, NOT a positional re-sequencing: gaps are
           preserved (e.g. digits {1,3,4} on a "6.a" parent produce (1)/(3)/(4), never (1)/(2)/(3)),
           because the digit is the real form sub-item number and re-sequencing by enumerate
           position (the round-1 bug) silently renumbers/relabels real form items.

    2) A bare letter 'a' glued onto a single-digit item that otherwise has NO other lettered
       children anywhere in the schedule under that prefix (e.g. RI-E item 7's Yes/No checkbox,
       which has no "7.b" on the real form) -> collapse to the parent digit alone ("7").
       Fires only when ALL of: no other letter exists under that (prefix, parent) digit, no
       distinct row already carries the bare parent item (would create a dup), AND the '.a'
       label is carried by exactly one row (single mdrm, not a matrix column group) -- a matrix
       '.a' (several mdrm codes/columns sharing one label) must never collapse into a single
       bare-digit row, which would merge distinct columns into one.
    """
    def segs(it):
        return it.split(".") if it else []
    def prefix(mdrm):
        return (mdrm or "")[:4]

    changes = []  # (old_label, new_label, n_rows) for the stdout diff summary

    # ---- pass 1: trailing bare-digit-after-letter, grouped by DISTINCT ORIGINAL LABEL ----
    groups = {}  # (prefix, parent_tuple) -> {original_item_label: [rows sharing that label]}
    for r in rows:
        it = r.get("item")
        sg = segs(it)
        if len(sg) < 3:
            continue
        last, prev = sg[-1], sg[-2]
        if SEG_DIGIT.match(last) and SEG_ALPHA.match(prev):
            key = (prefix(r.get("mdrm")), tuple(sg[:-1]))
            groups.setdefault(key, {}).setdefault(it, []).append(r)

    # Snapshot of every (prefix, item) that already exists BEFORE pass 1 touches anything, so
    # the single-label collapse never creates a duplicate bare-parent label. Each group's
    # parent_str is unique to that group (groups are keyed by (prefix, parent)), so mutations
    # made for one group cannot affect another group's conflict check.
    existing_items = {(prefix(r.get("mdrm")), r.get("item")) for r in rows if r.get("item")}

    for (pfx, parent), labels in groups.items():
        parent_str = ".".join(parent)
        if len(labels) == 1:
            (only_label, members), = labels.items()
            if (pfx, parent_str) in existing_items:
                print(f"  [clean_glued_items] {sch}: SKIP {only_label} -> {parent_str} "
                      f"({pfx}, {len(members)} row(s)) -- bare '{parent_str}' row already "
                      f"exists, left untouched")
                continue
            for r in members:
                r["item"] = parent_str
                r["depth"] = len(parent)
            changes.append((only_label, parent_str, len(members)))
        else:
            for label in sorted(labels, key=lambda l: int(segs(l)[-1])):
                members = labels[label]
                digit = segs(label)[-1]
                new_item = f"{parent_str}.({digit})"
                for r in members:
                    r["item"] = new_item
                    r["depth"] = len(parent) + 1
                changes.append((label, new_item, len(members)))

    # ---- pass 2: singleton lettered-'a' collapse (no other children under that parent,
    #      no bare-parent conflict, and the '.a' label itself is a single non-matrix row) ----
    parent_children = {}
    label_rowcount = {}
    for r in rows:
        sg = segs(r.get("item"))
        pfx = prefix(r.get("mdrm"))
        if len(sg) >= 2 and SEG_DIGIT.match(sg[0]):
            parent_children.setdefault((pfx, sg[0]), set()).add(sg[1])
        if len(sg) == 2:
            k = (pfx, r["item"])
            label_rowcount[k] = label_rowcount.get(k, 0) + 1
    bare_parents = {(prefix(r.get("mdrm")), r.get("item")) for r in rows if r.get("item")}
    for r in rows:
        sg = segs(r.get("item"))
        if len(sg) == 2 and sg[1] == "a" and SEG_DIGIT.match(sg[0]):
            pfx = prefix(r.get("mdrm"))
            if (parent_children.get((pfx, sg[0])) == {"a"}
                    and (pfx, sg[0]) not in bare_parents
                    and label_rowcount.get((pfx, r["item"]), 0) == 1):
                changes.append((r["item"], sg[0], 1))
                r["item"] = sg[0]
                r["depth"] = 1

    # ---- pass 3: letter-digit-letter tail -> the form's own double-paren notation (RC-4 fix,
    #      2026-07-08 cycle-11 audit, hierarchy_audit_call_A.md). A chain whose LAST THREE segments
    #      are [letter, digit, letter] (e.g. "4.c.1.a", "M.1.f.4.a", "6.a.2.a") is a genuine
    #      multi-level form sub-item that parse_item() already parsed with the CORRECT depth (depth
    #      == the raw segment count) -- only its STRING NOTATION is wrong: the form's own printed
    #      label double-parenthesizes the digit level ("4.c.(1)(a)"), never leaves it as a bare dot
    #      segment. This is a pure per-row string reformat: it never groups, renumbers, or collapses
    #      rows the way pass 1 must (pass 1 has to distinguish a spurious filer-glued digit from a
    #      real sub-item digit by grouping distinct labels, which is exactly what the round-1
    #      adversarial review caught fanning matrix columns into fake sub-items when done by raw
    #      position instead of by distinct label) -- every row keeps its own digit/letter values and
    #      its existing depth verbatim, so several rows/columns that already share one such label
    #      before the fix still share the identical reformatted label after it, with no fan-out
    #      risk. Deliberately narrow (exact (alpha, digit, alpha) tail only) so it can never fire on
    #      pass 1/2's own 2-segment-tail patterns or on any row it shouldn't (e.g. it correctly
    #      leaves a 3-segment chain with a pre-existing bare parent, such as "M.1.f.3" alongside
    #      "M.1.f", untouched -- pass 1's bare-parent-conflict guard already owns that ambiguous
    #      case and is not touched here; see the cycle-11 worker report for the residual gap this
    #      leaves). As a side effect this also resolves RC-5 (parent-after-child ordering): once the
    #      label is in double-paren form, apply_overrides()'s existing item_sort_key (unmodified)
    #      already sorts a paren-digit segment strictly after its shorter bare-parent prefix and in
    #      numeric order among paren-digit siblings, so no separate reordering logic is needed.
    for r in rows:
        sg = segs(r.get("item"))
        if len(sg) >= 4 and SEG_ALPHA.match(sg[-1]) and SEG_DIGIT.match(sg[-2]) and SEG_ALPHA.match(sg[-3]):
            new_item = f"{'.'.join(sg[:-2])}.({sg[-2]})({sg[-1]})"
            if new_item != r["item"]:
                changes.append((r["item"], new_item, 1))
                r["item"] = new_item
                # depth intentionally unchanged -- the raw segment count already equalled the
                # correct conceptual nesting depth before this reformat.

    for old, new, n in changes:
        print(f"  [clean_glued_items] {sch}: {old} -> {new}  ({n} row{'s' if n != 1 else ''})")
    return rows

def item_sort_key(item_str):
    """Convert an item-number string ('1', '1.a', '3.a.(1)', 'M.10.b', '31.11') into a sort
    tuple giving true FORM order — never CDR column order, never lexical string order.
    Adapted from FFIEC 002's build_hierarchy_002.item_sort_key (same tuple-class scheme):
      0 = bare numeric segment ('1','10','31') -> sorts by int value
      1 = alphabetic segment (bare 'a'/'b'/... AND the literal 'M' bucket marker) -> sorts alpha
      2 = parenthesized numeric '(1)','(2)' -> sorts AFTER bare-letter siblings
    Class 0 always sorts before class 1 at the same tuple position, which is what pushes every
    'M.x' memorandum item after all plain numbered items with no special-cased M-handling: 'M'
    itself parses as class 1 (it isn't a digit), same trick the 002 sibling relies on.
    """
    s = (item_str or "").strip().rstrip(".")
    key = []
    for seg in re.findall(r"\([^)]*\)|[^.]+", s):
        paren = seg.startswith("(")
        inner = seg.strip("()")
        if inner.isdigit():
            key.append((2 if paren else 0, int(inner), ""))
        else:
            key.append((1, 0, inner.lower()))
    return key or [(1, 0, "")]

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
    """(schedule, mdrm) -> (item_number, depth) parsed from rendered Call PDF(s). SCOPED per
    schedule (RC-3 fix, 2026-07-08 cycle-11 audit, hierarchy_audit_call_A.md). Previously this was
    one flat mdrm-only dict (out.setdefault(code,(item,depth)), first PDF occurrence wins) -- but
    MDRM codes legitimately recur across schedules (a schedule's own total is cross-referenced
    elsewhere; RCON/RCFD twins repeat; some codes are shared verbatim across RC/RC-B/RC-D/RC-F/
    RC-G/RC-H, or are genuine members of more than one schedule's CDR column list even though they
    only ever appear printed on ONE schedule's page), so a code's item/depth got fixed by whichever
    schedule's page the scanner reached FIRST and that same value then leaked onto every OTHER
    schedule's membership entry for the same code at merge time in main(). Track the CURRENT
    schedule as pages are scanned via each schedule's own printed page heading (SCHED_HEADING);
    each (schedule, mdrm) pair keeps the same first-occurrence-wins semantics as before, just
    scoped, so a value found on schedule X's own page can never be looked up under schedule Y's key.
    """
    out={}
    try: import pypdf
    except Exception:
        print("  (pypdf not installed; skipping PDF depth)"); return out
    for p in paths:
        if not os.path.exists(p): print(f"  PDF not found: {p}"); continue
        r=pypdf.PdfReader(p)
        cur_sch=None
        for pg in r.pages:
            for ln in (pg.extract_text() or "").splitlines():
                hm=SCHED_HEADING.search(ln)
                if hm and "must equal" not in ln.lower():
                    letters=hm.group(1).upper().replace("-","")
                    cur_sch=letters+hm.group(2).upper() if hm.group(2) else letters
                item,depth=parse_item(ln)
                if not item or not cur_sch: continue
                for code in PDFCODE.findall(ln):
                    out.setdefault((cur_sch, code), (item, depth))   # first occurrence wins, per schedule
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

def load_overrides():
    """Load ffiec_call_hierarchy_overrides.json, or {} if absent (overrides-file-absent behavior
    is preserved: empty drops, force_rows loop no-ops, the final sort in apply_overrides still
    always runs)."""
    if os.path.exists(OVERRIDES):
        return json.load(open(OVERRIDES,encoding="utf-8"))
    return {}

def apply_drops(hier, ov):
    """Apply drop_codes from ov BEFORE clean_glued_items runs (2026-07-08 re-review, finding 4:
    drop-before-clean ordering). Root cause of the bug this fixes: clean_glued_items() used to run
    first, so a soon-to-be-dropped contaminant row (e.g. a falsely-duplicated code that belongs to
    a different schedule) could still participate in clean's bare-parent/letter snapshots
    (existing_items / bare_parents / parent_children) and block a legitimate collapse just by
    sitting at the wrong (prefix, item) position. Dropping first means clean only ever sees the
    final, correct row set for each schedule. Key-scoped drop semantics unchanged: {key,mdrm}
    pairs, since Call codes repeat across many schedules (a flat mdrm-only drop would remove
    correct entries from sibling schedules)."""
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
    # drop_headers (2026-07-09, cycle-11.1): remove item-tagged rows that carry NO mdrm —
    # blank header rows drop_codes can't reach (it matches by mdrm). Needed for the legacy
    # RCR '2' header and RCRIB's two contamination headers ('10'/'48'), all wrong-schedule
    # rows per the cycle-11 audit ledgers. Key+item scoped; rows WITH an mdrm are never
    # touched by this path.
    hdr_drops={(d.get("key"),d.get("item")) for d in ov.get("drop_headers",[])
               if d.get("key") and d.get("item")}
    if hdr_drops:
        nh=0
        for key in list(hier):
            before=len(hier[key])
            hier[key]=[r for r in hier[key]
                       if not ((key,r.get("item")) in hdr_drops and not r.get("mdrm"))]
            nh+=before-len(hier[key])
        if nh: print(f"  [overrides] dropped {nh} blank-mdrm header rows via drop_headers")

def apply_overrides(hier, ov):
    """Apply force_rows (supplemental/corrected rows) from ov, then ALWAYS re-sort every schedule
    into true form order and rewrite 'order' to match (this final sort runs even if the overrides
    file is absent/empty — it is not conditional on overrides content). Root-cause fix (2026-07
    audit): order previously came straight from CDR column position, which is alphanumeric-by-MDRM,
    not form order; this re-sort replaces that with a hierarchical item-number sort (see
    item_sort_key), the same fix pattern as FFIEC 002's build_hierarchy_002. Runs AFTER
    clean_glued_items (and after apply_drops, called separately, earlier in main()) — drops must
    happen before clean, but force_rows/sort must happen after, since force_rows can add rows clean
    never saw and the sort must see clean's final item labels.
    """
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
    # Final, always-on hierarchical sort: rows with a real item number sort by form order
    # (item_sort_key); rows with item==None (PDF never matched them) are appended after, in
    # their prior relative order — sort() is stable, so ties (including matrix column-variant
    # rows that legitimately share one item number) keep their original CDR relative order.
    for key in hier:
        hier[key].sort(key=lambda x: (1, []) if not x.get("item") else (0, item_sort_key(x["item"])))
        for i, node in enumerate(hier[key]):
            node["order"] = i
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
            it,dp=depth.get((sch,m),(None,None))  # RC-3 fix: scoped lookup, see depth_from_pdf()
            cap=nicecap(m,cp)
            if titles.get(m): nfull+=1
            rows.append({"mdrm":m,"caption":cap,"order":i,"item":it,"depth":dp})
        hier[sch]=rows
    # Pipeline order (2026-07-08 re-review, finding 4): load overrides once, drop contaminant
    # codes BEFORE clean_glued_items runs (so dropped rows never pollute clean's bare-parent/
    # letter snapshots), THEN clean each schedule, THEN force_rows + the final item_sort_key sort
    # (apply_overrides) — force_rows/sort must stay last since force_rows can add rows clean never
    # saw and the sort needs clean's final item labels.
    ov=load_overrides()
    apply_drops(hier, ov)
    for sch in hier:
        hier[sch]=clean_glued_items(hier[sch], sch)
    apply_overrides(hier, ov)
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
