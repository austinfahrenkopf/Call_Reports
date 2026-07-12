"""
apply_partab.py — Apply Part A and Part B hierarchy edits.

Part A wirable (panel data confirmed):
  RI M.11  = RIADA530  (149,905 rows)
  RCS 9    = RCFDB783  (86 rows)
  RCRI 9.b = RCFAP845  (164 rows — promote from orphan block)
  RCRI §P7 fixes:
    item=9    clear mdrm RCFD3368 (contamination)
    item=26.a remove RCFD3632 (contamination)
    item='31.11' → '31' in RCRI + RCRIB

Part A NOT wirable (0 panel rows) — skipped:
  RC-D 6.c/M.1.c/M.3.d (RCFDHT65/68/G334), RC-Q 12 (RCFDG530),
  RCRI 11/13a/13b (RCFAP851/853, RCFWP854), 34.b (RCFDKX80),
  52a/52b (RCFAH311/RCFWH312), 55b (RCFAH036)

Part B — structural headers (no MDRM):
  RCCI: 1.c, 2, 4, 6, 9, M.1.d, M.1.e, M.8, M.10, M.12, M.13, M.15
  RCE:  M.1.d, M.1.h
  RCEI: M.1.d, M.1.h
  RCL:  1.c
  RCM:  17.d
  RCN:  1.c, 1.e, M.1.d, M.1.e
  RCS:  4, 5
  RCRII: 18

ATOMIC write: temp → os.replace → re-read + json.load verify
"""
import json, os, pathlib, sys, tempfile

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HIER_PATH = pathlib.Path("ffiec_call_hierarchy.json")
LOG = []

def log(msg):
    LOG.append(msg)
    print(msg)

# ── helpers ────────────────────────────────────────────────────────────────────

def header(item, caption, depth):
    return {"item": item, "mdrm": "", "col": False, "depth": depth, "caption": caption}

def leaf(item, mdrm, caption, depth):
    return {"item": item, "mdrm": mdrm, "col": False, "depth": depth, "caption": caption}

def insert_before(nodes, target_item, new_node):
    """Insert new_node immediately before the first node whose item == target_item."""
    for i, n in enumerate(nodes):
        if n.get("item", "") == target_item:
            nodes.insert(i, new_node)
            return True
    return False

def insert_after(nodes, target_item, new_node):
    """Insert new_node immediately after the last node whose item == target_item."""
    last_idx = None
    for i, n in enumerate(nodes):
        if n.get("item", "") == target_item:
            last_idx = i
    if last_idx is not None:
        nodes.insert(last_idx + 1, new_node)
        return True
    return False

def find_by_mdrm(nodes, mdrm):
    for n in nodes:
        if n.get("mdrm", "") == mdrm:
            return n
    return None

def find_by_item(nodes, item):
    for n in nodes:
        if n.get("item", "") == item:
            return n
    return None

def remove_by_mdrm(nodes, mdrm):
    for i, n in enumerate(nodes):
        if n.get("mdrm", "") == mdrm:
            nodes.pop(i)
            return True
    return False

def has_item(nodes, item):
    return any(n.get("item", "") == item for n in nodes)

# ── load ───────────────────────────────────────────────────────────────────────

hier = json.loads(HIER_PATH.read_bytes().replace(b'\x00', b''))
log(f"Loaded hierarchy: {sum(len(v) for v in hier.values())} total nodes")

# ══════════════════════════════════════════════════════════════════════════════
# PART A — wirable items
# ══════════════════════════════════════════════════════════════════════════════

# ── RI M.11 = RIADA530 ────────────────────────────────────────────────────────
ri = hier["RI"]
if not has_item(ri, "M.11"):
    ok = insert_after(ri, "M.10",
        leaf("M.11", "RIADA530",
             "Does the reporting bank have a Subchapter S election in effect for federal income tax purposes?",
             depth=1))
    log(f"RI M.11 RIADA530: {'INSERTED after M.10' if ok else 'FAILED — M.10 not found'}")
else:
    log("RI M.11: already present — skipped")

# ── RCS item 9 = RCFDB783 ────────────────────────────────────────────────────
rcs = hier["RCS"]
if not has_item(rcs, "9"):
    ok = insert_before(rcs, "10",
        leaf("9", "RCFDB783",
             "Reporting bank's unused commitments to provide liquidity to other institutions' securitization structures - 1-4 family residential loans",
             depth=1))
    log(f"RCS 9 RCFDB783: {'INSERTED before item 10' if ok else 'FAILED — item 10 not found'}")
else:
    log("RCS 9: already present — skipped")

# ── RCRI §P7 fix 1: item=9 clear contaminating RCFD3368 ─────────────────────
rcri = hier["RCRI"]
n9 = find_by_mdrm(rcri, "RCFD3368")
if n9 and n9.get("item", "") == "9":
    n9["mdrm"] = ""
    n9["caption"] = "AOCI-related adjustments to common equity tier 1 capital (applicable to institutions that did not make the AOCI opt-out election)"
    n9["col"] = False
    log("RCRI item=9: cleared contaminating RCFD3368, caption updated")
else:
    log(f"RCRI item=9/RCFD3368: not found as expected — check manually (found: {n9})")

# ── RCRI §P7 fix 2: item=9.b from RCFAP845 orphan ───────────────────────────
n845 = find_by_mdrm(rcri, "RCFAP845")
if n845:
    if not n845.get("item"):
        n845["item"] = "9.b"
        n845["depth"] = 2
        n845["col"] = False
        log("RCRI item=9.b: RCFAP845 promoted from orphan (item=None→'9.b', depth=None→2)")
    else:
        log(f"RCRI RCFAP845: already has item={n845['item']!r} — skipped")
else:
    log("RCRI RCFAP845: not found in RCRI hierarchy")

# ── RCRI §P7 fix 3: remove item=26.a RCFD3632 contamination ─────────────────
n3632 = find_by_mdrm(rcri, "RCFD3632")
if n3632:
    removed = remove_by_mdrm(rcri, "RCFD3632")
    log(f"RCRI item=26.a RCFD3632: {'REMOVED' if removed else 'removal failed'}")
else:
    log("RCRI RCFD3632: not found — already removed or not present")

# ── RCRI §P7 fix 4: rename item='31.11' → '31' ───────────────────────────────
n3111 = find_by_item(rcri, "31.11")
if n3111:
    n3111["item"] = "31"
    log("RCRI item='31.11' renamed to '31'")
else:
    log("RCRI item='31.11': not found (maybe already '31' or absent)")

# ── RCRIB §P7 fix 4: rename item='31.11' → '31' ─────────────────────────────
rcrib = hier.get("RCRIB", [])
n3111b = find_by_item(rcrib, "31.11")
if n3111b:
    n3111b["item"] = "31"
    log("RCRIB item='31.11' renamed to '31'")
else:
    log("RCRIB item='31.11': not found in RCRIB (may not exist)")

# ══════════════════════════════════════════════════════════════════════════════
# PART B — structural headers (no MDRM)
# ══════════════════════════════════════════════════════════════════════════════

def add_header_before(schedule_key, target_item, item, caption, depth):
    """Add a no-MDRM header before target_item in the named schedule, if not already present."""
    nodes = hier[schedule_key]
    if has_item(nodes, item):
        log(f"{schedule_key} {item}: already present — skipped")
        return
    ok = insert_before(nodes, target_item, header(item, caption, depth))
    log(f"{schedule_key} {item}: {'ADDED before ' + target_item if ok else 'FAILED — ' + target_item + ' not found'}")

# ── RCCI ──────────────────────────────────────────────────────────────────────
# 1.c (depth=2) — parent for 1.c.(1).*, 1.c.(2).*
add_header_before("RCCI", "1.c.(1).(B)", "1.c",
    "All other loans secured by 1-4 family residential properties", depth=2)
# 2 (depth=1) — parent for 2.a, 2.b, 2.c
add_header_before("RCCI", "2.a", "2",
    "Loans to depository institutions and acceptances of other banks", depth=1)
# 4 (depth=1) — parent for 4.a, 4.b
add_header_before("RCCI", "4.a.(A)", "4",
    "Commercial and industrial loans", depth=1)
# 6 (depth=1) — parent for 6.a, 6.b, 6.c, 6.d
add_header_before("RCCI", "6.a.(A)", "6",
    "Loans to individuals for household, family, and other personal expenditures", depth=1)
# 9 (depth=1) — parent for 9.a, 9.b
add_header_before("RCCI", "9.a.(B)", "9",
    "Loans to nondepository financial institutions and other loans", depth=1)
# M.1.d (depth=2) — parent for M.1.d.(1), M.1.d.(2)
add_header_before("RCCI", "M.1.d.(1)", "M.1.d",
    "Loan modifications to borrowers experiencing financial difficulty: secured by nonfarm nonresidential", depth=2)
# M.1.e (depth=2) — parent for M.1.e.(1), M.1.e.(2)
add_header_before("RCCI", "M.1.e.(1)", "M.1.e",
    "Loan modifications to borrowers experiencing financial difficulty: commercial and industrial loans", depth=2)
# M.8 (depth=1) — parent for M.8.a, M.8.b, M.8.c
add_header_before("RCCI", "M.8.a", "M.8",
    "Trading revenue: breakdown by risk exposure type", depth=1)
# M.10 (depth=1) — parent for M.10.a.*, M.10.b.*, etc.
add_header_before("RCCI", "M.10.a.(A)", "M.10",
    "Loans to nondepository financial institutions", depth=1)
# M.12 (depth=1) — parent for M.12.a, M.12.b, M.12.c, M.12.d
add_header_before("RCCI", "M.12.a", "M.12",
    "Acquired loans and leases held for investment: fair value and contractual amounts", depth=1)
# M.13 (depth=1) — parent for M.13.a, M.13.b
add_header_before("RCCI", "M.13.a", "M.13",
    "Construction, land development, and other land loans in domestic offices", depth=1)
# M.15 (depth=1) — parent for M.15.a, M.15.b, M.15.c
add_header_before("RCCI", "M.15.a", "M.15",
    "Reverse mortgages", depth=1)

# ── RCE ───────────────────────────────────────────────────────────────────────
add_header_before("RCE", "M.1.d.(1)", "M.1.d",
    "Selected components of total deposits: brokered deposits — other", depth=2)
add_header_before("RCE", "M.1.h.(1)", "M.1.h",
    "Sweep deposits", depth=2)

# ── RCEI ──────────────────────────────────────────────────────────────────────
add_header_before("RCEI", "M.1.d.(1)", "M.1.d",
    "Selected components of total deposits: brokered deposits — other", depth=2)
add_header_before("RCEI", "M.1.h.(1)", "M.1.h",
    "Sweep deposits", depth=2)

# ── RCL ───────────────────────────────────────────────────────────────────────
add_header_before("RCL", "1.c.(1).a", "1.c",
    "Unused commitments: commercial real estate, construction, and land development", depth=2)

# ── RCM ───────────────────────────────────────────────────────────────────────
add_header_before("RCM", "17.d.(1)", "17.d",
    "Outstanding balance of borrowings from Federal Reserve Banks", depth=2)

# ── RCN ───────────────────────────────────────────────────────────────────────
add_header_before("RCN", "1.c.(1)", "1.c",
    "Loans secured by 1-4 family residential properties", depth=2)
add_header_before("RCN", "1.e.(1)", "1.e",
    "Loans secured by nonfarm nonresidential properties", depth=2)
add_header_before("RCN", "M.1.d.(1)", "M.1.d",
    "Loan modifications to borrowers experiencing financial difficulty: secured by nonfarm nonresidential", depth=2)
add_header_before("RCN", "M.1.e.(1)", "M.1.e",
    "Loan modifications to borrowers experiencing financial difficulty: commercial and industrial loans", depth=2)

# ── RCS ───────────────────────────────────────────────────────────────────────
add_header_before("RCS", "4.a", "4",
    "Past due loan amounts included in item 1", depth=1)
add_header_before("RCS", "5.a", "5",
    "Charge-offs and recoveries on assets sold and securitized with recourse or other seller-provided credit enhancements", depth=1)

# ── RCRII ─────────────────────────────────────────────────────────────────────
add_header_before("RCRII", "18.a", "18",
    "Unused commitments", depth=1)

# ══════════════════════════════════════════════════════════════════════════════
# ATOMIC WRITE
# ══════════════════════════════════════════════════════════════════════════════

total_nodes = sum(len(v) for v in hier.values())
log(f"\nTotal nodes after edits: {total_nodes}")

# Write to temp file first
tmp = HIER_PATH.with_suffix(".json.tmp")
tmp.write_text(json.dumps(hier, ensure_ascii=False), encoding="utf-8")

# Atomic replace
os.replace(tmp, HIER_PATH)

# Re-read and verify
try:
    verify = json.loads(HIER_PATH.read_bytes().replace(b'\x00', b''))
    vtotal = sum(len(v) for v in verify.values())
    log(f"VERIFY: re-read OK — {vtotal} total nodes (expected {total_nodes})")
    assert vtotal == total_nodes, f"Node count mismatch: {vtotal} != {total_nodes}"
    log("WRITE: ATOMIC WRITE VERIFIED OK")
except Exception as e:
    log(f"VERIFY FAILED: {e}")
    raise

print("\n=== CHANGE SUMMARY ===")
for line in LOG:
    print(line)
