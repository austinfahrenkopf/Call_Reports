"""
Map hierarchy structure for Part A/B insertion points.
Checks: RI M.11, RCS items, and Part B schedules.
"""
import json, duckdb, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

hier = json.load(open("ffiec_call_hierarchy.json", encoding="utf-8"))
con = duckdb.connect()
con.execute("CREATE VIEW t AS SELECT * FROM read_parquet('cdr_parquet/*.parquet')")

def check(mdrm):
    r = con.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT id_rssd) AS banks, MIN(quarter_end), MAX(quarter_end) FROM t WHERE mdrm=?", [mdrm]).fetchone()
    return r

# --- RI schedule: find M items near M.11 ---
print("=== RI schedule — M items ===")
nodes = hier.get("RI", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    if "M." not in item: continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# --- RCS schedule ---
print("\n=== RCS schedule (all items) ===")
nodes = hier.get("RCS", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# --- RCRI item 9.b candidate ---
print("\n=== RCFAP845 panel rows (item 9.b candidate) ===")
r = check("RCFAP845")
print(f"  RCFAP845: {r[0]:,} rows | {r[1]} banks | {r[2]}–{r[3]}")
r = check("RCOAP845")
print(f"  RCOAP845: {r[0]:,} rows | {r[1]} banks | {r[2]}–{r[3]}")

# --- Check RCFAP845/RCOAP845 in hierarchy ---
rcri = hier.get("RCRI", [])
for n in rcri:
    mdrm = n.get("mdrm","") or ""
    if mdrm in ("RCFAP845","RCOAP845"):
        print(f"  In hierarchy: item={n.get('item','')!r} mdrm={mdrm} depth={n.get('depth')} col={n.get('col')}")

# --- Part B: check which schedules need header nodes ---
print("\n=== Part B schedule sizes ===")
for sched in ["RCCI", "RCE", "RCEI", "RCL", "RCM", "RCN", "RCS", "RCRII"]:
    nodes = hier.get(sched, [])
    items = [n.get("item","") for n in nodes if n.get("item")]
    print(f"  {sched}: {len(nodes)} nodes, items: {', '.join(str(x) for x in items[:30])}")

# --- Part B: RCCI items around 1.c, 2, 4, 6, 9 ---
print("\n=== RCCI items 1-10 and M items ===")
nodes = hier.get("RCCI", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    try:
        lead = int(str(item).split('.')[0])
        if lead > 20 and "M." not in item: continue
    except: pass
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# --- Part B: RCE/RCEI M items ---
print("\n=== RCE M items ===")
nodes = hier.get("RCE", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    if "M." not in item: continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

print("\n=== RCEI M items ===")
nodes = hier.get("RCEI", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    if "M." not in item: continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# --- Part B: RCL items around 1.c ---
print("\n=== RCL items 1.a-1.e ===")
nodes = hier.get("RCL", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    if not item.startswith("1"): continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# --- Part B: RCM items around 17.d ---
print("\n=== RCM items 17.a-17.f ===")
nodes = hier.get("RCM", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    if not item.startswith("17"): continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# --- Part B: RCN items 1.c, 1.e, M items ---
print("\n=== RCN items 1.a-1.f + M items ===")
nodes = hier.get("RCN", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    try:
        lead = int(str(item).split('.')[0])
        if lead == 1 or "M." in item:
            print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")
    except: pass

# --- Part B: RCS items 4, 5 ---
print("\n=== RCS items 3-7 ===")
nodes = hier.get("RCS", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    try:
        lead = int(str(item).split('.')[0])
        if lead < 3 or lead > 7: continue
    except: continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# --- Part B: RCRII item 18 ---
print("\n=== RCRII items 15-22 ===")
nodes = hier.get("RCRII", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    try:
        lead = int(str(item).split('.')[0])
        if lead < 15 or lead > 22: continue
    except: continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")

# Also check RCCI items M.8, M.10, M.12, M.13, M.15
print("\n=== RCCI M items (full) ===")
nodes = hier.get("RCCI", [])
for i, n in enumerate(nodes):
    item = n.get("item","") or ""
    if "M." not in item: continue
    mdrm = n.get("mdrm","") or ""
    cap = (n.get("caption","") or "")[:70]
    depth = n.get("depth") or 0
    print(f"  [{i:3}] item={item!r:15} mdrm={mdrm:12} depth={depth} | {cap}")
